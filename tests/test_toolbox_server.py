from agent_reach.toolbox.server import _initialize_result


def test_initialize_does_not_advertise_unimplemented_notifications() -> None:
    result = _initialize_result({"protocolVersion": "2024-11-05"})

    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert result["serverInfo"] == {
        "name": "Ahmed ToolBox",
        "version": "0.1.2",
    }
