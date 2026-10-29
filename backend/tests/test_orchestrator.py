from app import orchestrator


def test_mock_loop_heals():
    result = orchestrator.run_once("NoSuchElementException: #submit", "<html/>", "assert True\n")
    assert result.final_state == "DONE"
    assert result.diagnosis is not None
    assert result.fix is not None
    assert result.events[0] == "COLLECT"
    assert "DIAGNOSE" in result.events
    assert result.events[-1] == "DONE"


def test_verify_fail_escalates():
    result = orchestrator.run_once("err", "<html/>", "assert True\n", verify=lambda diff: False)
    assert result.final_state == "ESCALATE"
