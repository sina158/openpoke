"""Offline selector eval: ranks, recall@k and the roster-size estimate on a few real cases."""

from evals import run_selector
from evals.generate import load_cases


def _cases():
    cases = load_cases(split="all")
    wanted = ["explicit-002", "pronoun-002", "ambiguous-002", "should_create-002"]
    return [c for c in cases if c["case_id"] in wanted]


def test_recall_and_mrr_on_real_cases():
    cases = _cases()
    result = run_selector.evaluate(cases, sizes=(5, 50), scorers=("recency", "bm25", "plus_pins", "hybrid"))
    assert result["hybrid"]
    rows = {(r["case_id"], r["size"]): r for r in result["rows"]}
    assert rows[("should_create-002", 5)]["ranks"]["bm25"] == []
    assert len(rows[("ambiguous-002", 50)]["ranks"]["bm25"]) == 2
    recall = result["summary"]["recall"]
    assert recall["plus_pins"]["n50"]["n"] == 3 and recall["plus_pins"]["n50"]["k8"] == 1.0
    assert 0 < recall["plus_pins"]["n50"]["mrr"] <= 1
    assert recall["recency"]["n50"]["k3"] <= recall["bm25"]["n50"]["k3"]
    assert result["summary"]["create_best_score"]["bm25"]["n5"]["n"] == 1
    tokens = result["summary"]["tokens"]
    assert tokens["n50"]["full_mean"] > tokens["n5"]["full_mean"]


def test_ranking_is_deterministic():
    cases = _cases()[:2]
    first = run_selector.evaluate(cases, sizes=(50,), scorers=("plus_pins",))
    second = run_selector.evaluate(list(reversed(cases)), sizes=(50,), scorers=("plus_pins",))
    assert sorted(first["rows"], key=lambda r: r["case_id"]) == sorted(second["rows"], key=lambda r: r["case_id"])


def test_hybrid_is_skipped_without_a_cache(tmp_path):
    result = run_selector.evaluate(_cases()[:1], sizes=(5,), cache_path=tmp_path / "none.bin",
                                   scorers=("bm25", "hybrid"))
    assert not result["hybrid"] and list(result["summary"]["recall"]) == ["bm25"]


def test_markdown_renders(tmp_path):
    assert run_selector.main(["--sizes", "5", "--split", "heldout", "--out-dir", str(tmp_path)]) == 0
    markdown = (tmp_path / "SELECTOR.md").read_text(encoding="utf-8")
    assert "recall@8" in markdown and "Roster block size" in markdown
    assert len(list(tmp_path.glob("*_selector_*.json"))) == 1
