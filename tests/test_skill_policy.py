from __future__ import annotations

from agent_reach.toolbox.runtime import RuntimeStore


def _workflow(label="doctor"):
    return [{"id": label, "tool_name": "reach_doctor", "arguments": {}}]


def test_skill_is_experimental_before_minimum_evidence(tmp_path):
    store = RuntimeStore(str(tmp_path / "runtime.db"))
    store.save_skill("candidate", "candidate", _workflow())
    store.record_skill_outcome("candidate", True, duration_ms=10)
    store.record_skill_outcome("candidate", True, duration_ms=12)
    skill = store.get_skill("candidate")
    assert skill["skill_policy"]["status"] == "EXPERIMENTAL"
    assert skill["skill_policy"]["successes"] == 2


def test_skill_promotes_only_after_success_and_latency_evidence(tmp_path):
    store = RuntimeStore(str(tmp_path / "runtime.db"))
    store.save_skill("trusted", "trusted", _workflow())
    for _ in range(10):
        store.record_skill_outcome(
            "trusted",
            True,
            audited_valid=True,
            duration_ms=20,
        )
    skill = store.get_skill("trusted")
    policy = skill["skill_policy"]
    assert policy["status"] == "TRUSTED_PRODUCTION"
    assert policy["successes"] == 10
    assert policy["last_20_success_rate"] == 1.0
    assert policy["audited_invalid_outputs"] == 0
    assert policy["latency_samples"] == 10


def test_audited_invalid_output_blocks_trust(tmp_path):
    store = RuntimeStore(str(tmp_path / "runtime.db"))
    store.save_skill("invalid-audit", "invalid", _workflow())
    for index in range(10):
        store.record_skill_outcome(
            "invalid-audit",
            True,
            audited_valid=(index != 9),
            duration_ms=20,
        )
    policy = store.get_skill("invalid-audit")["skill_policy"]
    assert policy["status"] == "EXPERIMENTAL"
    assert policy["audited_invalid_outputs"] == 1


def test_pathological_latency_blocks_trust(tmp_path):
    store = RuntimeStore(str(tmp_path / "runtime.db"))
    store.save_skill("slow", "slow", _workflow())
    for _ in range(10):
        store.record_skill_outcome(
            "slow",
            True,
            audited_valid=True,
            duration_ms=130_000,
        )
    policy = store.get_skill("slow")["skill_policy"]
    assert policy["status"] == "EXPERIMENTAL"
    assert policy["p95_duration_ms"] > policy["pathological_p95_threshold_ms"]


def test_two_demotion_episodes_archive_without_deleting_history(tmp_path):
    store = RuntimeStore(str(tmp_path / "runtime.db"))
    store.save_skill("unstable", "v1", _workflow("v1"))
    for _ in range(10):
        store.record_skill_outcome("unstable", False, duration_ms=10)
    first = store.get_skill("unstable")
    assert first["skill_policy"]["status"] == "DEMOTED"
    assert first["skill_policy"]["demotion_count"] == 1

    store.save_skill("unstable", "v2", _workflow("v2"), change_note="recovery revision")
    second_revision = store.get_skill("unstable")
    assert second_revision["revision"] == 2
    assert second_revision["skill_policy"]["status"] == "EXPERIMENTAL"
    assert second_revision["skill_policy"]["demotion_count"] == 1

    for _ in range(10):
        store.record_skill_outcome("unstable", False, duration_ms=10)
    archived = store.get_skill("unstable")
    assert archived["skill_policy"]["status"] == "ARCHIVED"
    assert archived["skill_policy"]["demotion_count"] == 2

    with store._connect() as conn:
        outcome_count = conn.execute(
            "SELECT COUNT(*) AS c FROM runtime_skill_outcomes WHERE name='unstable'"
        ).fetchone()["c"]
        version_count = conn.execute(
            "SELECT COUNT(*) AS c FROM runtime_skill_versions WHERE name='unstable'"
        ).fetchone()["c"]
    assert outcome_count == 20
    assert version_count == 2


def test_skill_list_surfaces_policy_status(tmp_path):
    store = RuntimeStore(str(tmp_path / "runtime.db"))
    store.save_skill("listed", "listed", _workflow())
    items = store.list_skills()
    assert items[0]["skill_policy"]["status"] == "EXPERIMENTAL"
