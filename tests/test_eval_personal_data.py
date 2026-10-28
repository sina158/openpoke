"""Committed eval data is synthetic: no real addresses, phone numbers, credentials or session headers."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCANNED = ["evals/fixtures/*.jsonl", "evals/golden/*.jsonl", "evals/golden/*.md", "evals/cassettes/*.jsonl",
           "evals/cassettes/heldout/*/*.jsonl", "evals/results/*.json", "evals/results/*.md",
           "evals/results/heldout/*.json", "evals/results/heldout/*.md"]
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.ca", "example.org", ".test")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
PHONE = re.compile(r"(?<![\w-])\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w-])")
SYNTHETIC_PHONE = re.compile(r"555[ .-]01\d\d$")
# Markers that only real traffic would carry: credentials, auth headers, provider session headers.
DENYLIST = ("Authorization", "Bearer ", "sk-or-", "x-opencode-session", "api_key")


def problems(text: str) -> list[str]:
    found = []
    for match in EMAIL.finditer(text):
        domain = match.group(1).lower()
        if not any(domain == d.lstrip(".") or domain.endswith("." + d.lstrip(".")) for d in ALLOWED_EMAIL_DOMAINS):
            found.append(f"email {match.group(0)}")
    found += [f"phone {m.group(0)}" for m in PHONE.finditer(text) if not SYNTHETIC_PHONE.search(m.group(0))]
    found += [f"marker {token!r}" for token in DENYLIST if token in text]
    return found


def test_the_scan_catches_real_looking_data():
    planted = "write to dana@realmail.net or call (416) 555-2368; Authorization: Bearer x"
    assert len(problems(planted)) == 4
    assert problems("ops@harbour.example.ca, desk@gull.test, 416-555-0142") == []


def test_committed_eval_data_is_synthetic():
    files = [path for pattern in SCANNED for path in sorted(ROOT.glob(pattern))]
    assert len(files) >= 5
    found = {str(path.relative_to(ROOT)): problems(path.read_text(encoding="utf-8")) for path in files}
    assert {name: hits[:3] for name, hits in found.items() if hits} == {}


def test_a_rendered_shortlist_with_evidence_is_synthetic(fresh_stores, tmp_path, monkeypatch):
    # Evidence quotes agents' logs into the prompt, so the scan also runs over what a turn would send.
    from evals import run_routing
    from evals.generate import load_cases
    from server.config import get_settings

    for key, value in run_routing._arm_env(run_routing.RunConfig(arm="fix", model=run_routing.DEFAULT_MODEL)).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OPENPOKE_ROUTING_PROFILE", "v2")
    monkeypatch.setenv("OPENPOKE_SHORTLIST_EVIDENCE", "1")
    get_settings.cache_clear()
    case = next(c for c in load_cases() if c["case_id"] == "near_duplicate-006")

    content = run_routing.render_case(case, 50, tmp_path).messages[0]["content"]

    assert content.count(' evidence="') >= 2 and "@" in content
    assert problems(content) == []
