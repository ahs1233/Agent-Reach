from agent_reach.toolbox.server import _initialize_result


def test_initialize_advertises_dynamic_tool_list() -> None:
    result = _initialize_result({"protocolVersion": "2024-11-05"})

    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"]["tools"]["listChanged"] is True
    assert result["serverInfo"] == {
        "name": "Ahmed ToolBox",
        "version": "0.1.1",
    }
