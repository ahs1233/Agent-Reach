import pytest

from agent_reach.toolbox.server import _initialize_result, _validate_auth_configuration


def test_auth_configuration_allows_loopback_without_token() -> None:
    _validate_auth_configuration("127.0.0.1", "")


def test_auth_configuration_requires_token_for_non_loopback() -> None:
    with pytest.raises(RuntimeError, match="AHMED_TOOLBOX_TOKEN is required"):
        _validate_auth_configuration("0.0.0.0", "")


def test_auth_configuration_allows_non_loopback_with_token() -> None:
    _validate_auth_configuration("0.0.0.0", "configured-secret")


def test_initialize_does_not_advertise_unimplemented_notifications() -> None:
    result = _initialize_result({"protocolVersion": "2024-11-05"})

    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert result["serverInfo"] == {
        "name": "Ahmed ToolBox",
        "version": "0.1.2",
    }
