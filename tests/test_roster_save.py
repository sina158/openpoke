import json

from server.services.execution.roster import AgentRoster


def test_save_round_trips_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "execution_agents" / "roster.json"
    roster = AgentRoster(path)
    roster.add_agent("Email to Alice")
    roster.add_agent("Renewal reminder")

    assert json.loads(path.read_text(encoding="utf-8")) == ["Email to Alice", "Renewal reminder"]
    assert AgentRoster(path).get_agents() == ["Email to Alice", "Renewal reminder"]
    assert [p.name for p in path.parent.iterdir()] == ["roster.json"]
