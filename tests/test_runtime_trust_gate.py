import json

from agent_reach.toolbox.gateway import AhmedToolboxGateway, RemoteMCPConfig


def test_known_read_only_remote_effect_is_se0():
    cfg = RemoteMCPConfig(
        name="scrapling",
        url="https://example.invalid/mcp",
        allow_tools=("fetch",),
        trust="read_only",
    )
    assert cfg.effect_class("fetch") == "SE0"
    assert cfg.can_execute("fetch") is True


def test_untrusted_unknown_remote_tool_fails_closed():
    cfg = RemoteMCPConfig(
        name="custom",
        url="https://example.invalid/mcp",
        allow_tools=("write_anything",),
        trust="untrusted",
    )
    assert cfg.effect_class("write_anything") is None
    assert cfg.can_execute("write_anything") is False


def test_explicit_full_trust_preserves_operator_approved_direct_execution():
    cfg = RemoteMCPConfig(
        name="custom",
        url="https://example.invalid/mcp",
        allow_tools=("write_anything",),
        trust="full",
    )
    assert cfg.can_execute("write_anything") is True


def test_environment_remote_defaults_to_untrusted(monkeypatch):
    monkeypatch.setenv(
        "AHMED_TOOLBOX_REMOTE_MCPS",
        json.dumps({
            "custom": {
                "url": "https://example.invalid/mcp",
                "allow_tools": ["lookup"],
                "effect_classes": {"lookup": "SE0"},
            }
        }),
    )
    gateway = AhmedToolboxGateway.from_environment()
    assert gateway.remotes["custom"].config.trust == "untrusted"
    assert gateway.resolve_tool_effect("custom__lookup") == "SE0"


def test_environment_rejects_invalid_effect_class(monkeypatch):
    monkeypatch.setenv(
        "AHMED_TOOLBOX_REMOTE_MCPS",
        json.dumps({
            "custom": {
                "url": "https://example.invalid/mcp",
                "allow_tools": ["lookup"],
                "effect_classes": {"lookup": "SE9"},
            }
        }),
    )
    try:
        AhmedToolboxGateway.from_environment()
    except ValueError as exc:
        assert "invalid effect class" in str(exc)
    else:
        raise AssertionError("invalid effect class must fail closed")
