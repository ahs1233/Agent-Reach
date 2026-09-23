from pathlib import Path


STARTUP_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "start-ahmed-toolbox.sh"


def test_model_provider_failure_isolated_from_core_startup():
    script = STARTUP_SCRIPT.read_text(encoding="utf-8")

    assert '"timeout_seconds": 45' in script
    assert "except (RuntimeError, SystemExit) as exc:" in script
    assert "model_subagent_degraded = str(exc)" in script
    assert "__AHMED_MODEL_SUBAGENT_STARTUP_ACCEPTANCE__degraded:" in script
    assert 'print("__AHMED_RUNTIME_STARTUP_ACCEPTANCE__ok")' in script

    assert 'raise SystemExit(f"model-subagent delegation failed:' not in script
    assert 'raise SystemExit(f"model-subagent task failed:' not in script
    assert 'raise SystemExit("Ahmed model-subagent provider configuration is incomplete")' not in script

    degraded = script.index("__AHMED_MODEL_SUBAGENT_STARTUP_ACCEPTANCE__degraded:")
    runtime_ok = script.index('__AHMED_RUNTIME_STARTUP_ACCEPTANCE__ok')
    assert degraded < runtime_ok


def test_live_network_acceptance_cannot_block_health_startup():
    script = STARTUP_SCRIPT.read_text(encoding="utf-8")
    start = script.index('case "${AHMED_DUAL_TEMPORAL_LIVE_ACCEPTANCE:-0}"')
    end = script.index("esac", start)
    live_block = script[start:end]

    assert "scripts/dual-temporal-live-acceptance.py" in live_block
    assert ") &" in live_block
    assert "live-network acceptance failed (non-fatal)" in live_block
