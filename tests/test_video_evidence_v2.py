from agent_reach.toolbox.video import MediaSegment, build_unified_timeline, extract_claim_candidates

def test_timeline_orders_speech_and_visual_events():
    segments=[{"start_seconds":10.0,"end_seconds":20.0,"transcript":"later","citation":"media:10-20"},
              {"start_seconds":0.0,"end_seconds":10.0,"transcript":"first","citation":"media:0-10"}]
    visual=[{"timestamp_seconds":5.0,"text":"slide"}]
    timeline=build_unified_timeline(segments, visual)
    assert [x["kind"] for x in timeline] == ["speech","visual","speech"]

def test_claim_candidates_are_explicitly_unverified():
    m={"source_url":"https://example.com/video","segments":[{"index":0,"start_seconds":0.0,"end_seconds":10.0,"transcript":"Oil rose 5%.","citation":"media:0-10"}]}
    claim=extract_claim_candidates(m)[0]
    assert claim["status"] == "UNVERIFIED_SOURCE_STATEMENT"
    assert claim["requires_external_verification"] is True
