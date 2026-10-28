import re

from evals.run_injection import DEFAULT_CASES, load_cases, main, run_suite

GUARDS = {
    "provenance",
    "consent_code",
    "args_hash",
    "single_use",
    "expiry",
    "escaping",
    "approval_required",
    "unknown_action",
    "tool_surface",
}


def test_no_attack_sends_and_the_user_still_can():
    suite = run_suite()

    assert len(suite.attacks) >= 40
    assert len(suite.controls) >= 2
    assert [case.id for case in suite.failures] == []
    assert suite.unauthorised_sends == 0
    assert suite.forged_turns == 0
    assert all(case.sends == 1 for case in suite.controls)


def test_every_attack_is_stopped_by_the_guard_it_names():
    suite = run_suite()

    assert {case.guard for case in suite.attacks} == GUARDS
    assert all(case.guard in case.guards_hit for case in suite.attacks)


def test_runner_exit_code_is_zero_when_clean(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "0 unauthorised sends" in out
    assert "consent_code 5/5" in out
    assert "tests the gate, not the model" in out


def test_cases_use_only_synthetic_addresses():
    text = DEFAULT_CASES.read_text(encoding="utf-8")
    addresses = [a.rstrip(".") for a in re.findall(r"[\w.+-]+@[\w.-]+", text)]

    assert addresses
    assert all(re.search(r"(\.test|@example\.com|\.example\.(com|ca))$", a) for a in addresses), addresses
    assert len(load_cases()) == len(text.splitlines())
