"""Cassettes: record once, replay for free, and fail loudly on a miss."""

import asyncio

import pytest

from evals.cassette import CURRENT, CallContext, Cassette, CassetteMiss, LiveCallLimit


def _fake_llm(calls):
    async def fake(*, model, messages, system=None, tools=None, api_key=None, **kwargs):
        calls.append(api_key)
        return {"id": "x", "model": model, "usage": {"prompt_tokens": 11, "completion_tokens": 2},
                "choices": [{"message": {"role": "assistant", "content": f"echo {messages[-1]['content']}"},
                             "finish_reason": "stop"}]}
    return fake


def _ask(cassette, text, seed=7):
    async def go():
        CURRENT.set(CallContext(temperature=0.0, seed=seed))
        return await cassette(model="m", messages=[{"role": "user", "content": text}], system="sys",
                              api_key="secret-key-value", tools=[])
    return asyncio.run(go())


def test_record_then_replay_hits_without_calling_the_model(tmp_path):
    calls = []
    recorder = Cassette("run", mode="record", directory=tmp_path, inner=_fake_llm(calls))
    first = _ask(recorder, "hello")
    assert first["choices"][0]["message"]["content"] == "echo hello"
    assert len(calls) == 1
    assert "secret-key-value" not in (tmp_path / "run.jsonl").read_text(encoding="utf-8")

    replay = Cassette("other-name", mode="replay", directory=tmp_path, inner=_fake_llm(calls))
    assert _ask(replay, "hello") == first
    assert len(calls) == 1 and replay.hits == 1


def test_replay_miss_raises(tmp_path):
    Cassette("run", mode="record", directory=tmp_path, inner=_fake_llm([]))
    replay = Cassette("run", mode="replay", directory=tmp_path)
    with pytest.raises(CassetteMiss):
        _ask(replay, "never recorded")


def test_seed_is_part_of_the_key(tmp_path):
    calls = []
    recorder = Cassette("run", mode="record", directory=tmp_path, inner=_fake_llm(calls))
    _ask(recorder, "hello", seed=1)
    _ask(recorder, "hello", seed=2)
    _ask(recorder, "hello", seed=1)
    assert len(calls) == 2


def test_live_call_limit(tmp_path):
    live = Cassette("run", mode="live", directory=tmp_path, inner=_fake_llm([]), max_live_calls=1)
    _ask(live, "one")
    with pytest.raises(LiveCallLimit):
        _ask(live, "two")
    assert not (tmp_path / "run.jsonl").exists()
