"""README numbers must come from the results file."""

import json

from evals.check_readme_numbers import check, main

RESULTS = {
    "by_key": {"baseline_m_k8_n500": {"accuracy": {"rate": 0.53077, "k": 69, "n": 130}, "model": "m"}},
    "selector": {"tokens": {"n5000": {"full_mean": 55014.2}}},
    "groups": [{"arm": "baseline"}],
}


def _statuses(readme):
    return [(key, status) for key, _, _, status in check(readme, RESULTS)]


def test_matching_values_pass():
    readme = (
        "Accuracy <!-- n:by_key.baseline_m_k8_n500.accuracy.rate -->53.1%<!-- /n --> "
        "(<!-- n:by_key.baseline_m_k8_n500.accuracy.k -->69<!-- /n -->/"
        "<!-- n:by_key.baseline_m_k8_n500.accuracy.n -->130<!-- /n -->), "
        "<!-- n:selector.tokens.n5000.full_mean -->55,014<!-- /n --> tokens, model "
        "<!-- n:by_key.baseline_m_k8_n500.model -->m<!-- /n -->, arm <!-- n:groups.0.arm -->baseline<!-- /n -->, "
        "rate <!-- n:by_key.baseline_m_k8_n500.accuracy.rate -->0.531<!-- /n -->"
    )
    assert all(status == "ok" for _, status in _statuses(readme))
    assert len(_statuses(readme)) == 7


def test_mismatch_and_unknown_key_fail():
    readme = ("<!-- n:by_key.baseline_m_k8_n500.accuracy.rate -->53.0%<!-- /n --> "
              "<!-- n:by_key.fix_m_k8_n500.accuracy.rate -->60.0%<!-- /n --> "
              "<!-- n:groups.3.arm -->fix<!-- /n -->")
    assert [status for _, status in _statuses(readme)] == ["MISMATCH", "UNKNOWN KEY", "UNKNOWN KEY"]


def test_main_exit_codes(tmp_path, capsys):
    results = tmp_path / "RESULTS.json"
    results.write_text(json.dumps(RESULTS), encoding="utf-8")
    good = tmp_path / "good.md"
    good.write_text("<!-- n:by_key.baseline_m_k8_n500.accuracy.k -->69<!-- /n -->", encoding="utf-8")
    bad = tmp_path / "bad.md"
    bad.write_text("<!-- n:by_key.baseline_m_k8_n500.accuracy.k -->70<!-- /n -->", encoding="utf-8")
    assert main(["--readme", str(good), "--results", str(results)]) == 0
    assert main(["--readme", str(bad), "--results", str(results)]) == 1
    assert "1 marked numbers checked, 1 wrong" in capsys.readouterr().out
