import json
import re

from evals.run_injection import DEFAULT_CASES, CaseResult, SuiteResult, load_cases, main, run_suite, summary

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


def test_runner_writes_the_summary_for_the_results(tmp_path):
    out = tmp_path / "INJECTION.json"
    assert main(["--json", str(out)]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    attacks = sum(case["kind"] == "attack" for case in load_cases())
    assert written["attacks"] == attacks and written["unauthorised_sends"] == 0
    assert written["positive_controls"] == written["positive_controls_sent"] >= 2
    assert written["upper95"] == 3 / attacks


def test_summary_gives_no_zero_event_bound_once_a_send_slips_through():
    def case(kind, **counts):
        return CaseResult(id=kind, kind=kind, guard="none", technique="", expected_sends=1, **counts)

    suite = SuiteResult([case("attack", sends=1, unauthorised_sends=1), case("control", sends=1),
                         case("control", sends=0)])
    assert summary(suite) == {"attacks": 1, "unauthorised_sends": 1, "positive_controls": 2,
                              "positive_controls_sent": 1, "upper95": None}
