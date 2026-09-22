from agent_reach.toolbox.video import evaluate_video_intelligence, compare_video_evaluations

def sample(avg_segments=2):
    segs=[{"transcript":"abc","citation":"media:0-5"} for _ in range(avg_segments)]
    return {"duration_seconds":10,"segments":segs,"visual_frames":[{"sha256":"x"}],
            "visual_events":[{"analysis":{"visible_text":"slide"}}],
            "timeline":[{},{}],"claim_candidates":[{}],"visual_evidence_candidates":[{}]}

def test_eval_reports_provenance_and_coverage_without_vanity_score():
    result=evaluate_video_intelligence(sample())
    assert result["speech"]["citation_coverage"] == 1.0
    assert result["visual"]["analysis_coverage"] == 1.0
    assert "score" not in result

def test_comparison_detects_finer_timestamp_granularity():
    base=evaluate_video_intelligence(sample(1))
    better=evaluate_video_intelligence(sample(2))
    cmp=compare_video_evaluations(base, better)
    metric=next(x for x in cmp["changes"] if x["metric"]=="speech.average_segment_seconds")
    assert metric["direction"] == "IMPROVED"
