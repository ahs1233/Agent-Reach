from agent_reach.toolbox.research import ResearchStore
from agent_reach.toolbox.video import register_media_evidence, verification_queries

def manifest():
    return {
        "source_url": "https://example.com/video",
        "duration_seconds": 60,
        "provider": "groq",
        "full_transcript": "[media:0.0-60.0] Oil rose 5%.",
        "provenance": {"retrieval_tool": "yt-dlp"},
        "visual_frames": [],
        "claim_candidates": [{
            "candidate_id": "MC-0001", "statement_source_text": "Oil rose 5%.",
            "citation": "media:0.0-60.0", "start_seconds": 0.0, "end_seconds": 60.0,
        }],
    }

def test_media_registers_source_and_timestamped_evidence():
    store=ResearchStore(":memory:")
    run=store.create_run("verify video")
    result=register_media_evidence(store, run["run_id"], manifest())
    assert result["evidence_count"] == 1
    ev=result["evidence_items"][0]
    assert ev["structured_fact"]["media_citation"] == "media:0.0-60.0"
    assert ev["observation_type"] == "UNKNOWN"

def test_verification_work_never_marks_source_statement_verified():
    work=verification_queries(manifest())
    assert work[0]["accept_source_claim_as_verified"] is False
    assert work[0]["required_source_relation"] == "INDEPENDENT_WHEN_POSSIBLE"
