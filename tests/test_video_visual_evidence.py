from agent_reach.toolbox.video import visual_evidence_candidates, build_unified_timeline

def test_visual_observation_keeps_frame_provenance():
    events=[{"kind":"visual","timestamp_seconds":30.0,"frame_sha256":"abc",
             "citation":"frame:30.0","analysis":{"visible_text":"Revenue 10M",
             "scene_description":"A chart","notable_visual_claims":["Revenue is 10M"]}}]
    item=visual_evidence_candidates(events)[0]
    assert item["frame_sha256"] == "abc"
    assert item["status"] == "VISUAL_OBSERVATION_UNVERIFIED"
    assert item["requires_external_verification"] is True

def test_visual_and_speech_share_one_timeline():
    speech=[{"start_seconds":0.0,"end_seconds":60.0,"transcript":"hello","citation":"media:0-60"}]
    visual=[{"kind":"visual","timestamp_seconds":30.0,"citation":"frame:30.0","text":"slide"}]
    timeline=build_unified_timeline(speech, visual)
    assert [x["kind"] for x in timeline] == ["speech","visual"]
