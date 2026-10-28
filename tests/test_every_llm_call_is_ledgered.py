"""Every request_chat_completion call under server/ must pass a literal role= for the cost ledger."""

import ast
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"
TARGET = "request_chat_completion"


def scan(source: str, filename: str) -> tuple[int, list[str]]:
    """Return (calls found, calls without a literal non-empty role=)."""
    tree = ast.parse(source, filename)
    names = {TARGET}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.asname for alias in node.names if alias.name == TARGET and alias.asname)

    calls, missing = 0, []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        callee = getattr(func, "id", None) or getattr(func, "attr", None)
        if callee not in names:
            continue
        calls += 1
        role = next((kw.value for kw in node.keywords if kw.arg == "role"), None)
        if not (isinstance(role, ast.Constant) and isinstance(role.value, str) and role.value):
            missing.append(f"{filename}:{node.lineno}")
    return calls, missing


def test_every_llm_call_passes_a_literal_role():
    files_with_calls, missing = set(), []
    for path in sorted(SERVER.rglob("*.py")):
        calls, unledgered = scan(path.read_text(encoding="utf-8"), str(path))
        if calls:
            files_with_calls.add(path.relative_to(SERVER).as_posix())
        missing += unledgered

    assert missing == []
    # Positive control: the walker must see the call sites known today.
    assert files_with_calls >= {
        "agents/interaction_agent/runtime.py",
        "agents/execution_agent/runtime.py",
        "agents/execution_agent/tasks/search_email/tool.py",
        "services/conversation/summarization/summarizer.py",
        "services/gmail/importance_classifier.py",
    }


def test_scanner_flags_calls_without_a_literal_role():
    flagged = {
        "no role": "request_chat_completion(model='m', messages=[])",
        "variable role": "client.request_chat_completion(model='m', role=name)",
        "empty role": "request_chat_completion(model='m', role='')",
        "aliased": "from x import request_chat_completion as ask\nask(model='m')",
    }
    for label, source in flagged.items():
        calls, missing = scan(source, label)
        assert (calls, len(missing)) == (1, 1), label

    assert scan("request_chat_completion(model='m', role='interaction')", "ok") == (1, [])
    assert scan("other_call(model='m')", "unrelated") == (0, [])
