from agent_reach.toolbox.video import semantic_chunk_segments, evaluate_video_intelligence

def test_semantic_chunks_preserve_source_citations():
    segs=[
      {"start_seconds":0,"end_seconds":100,"transcript":"a"*20,"citation":"media:0-100"},
      {"start_seconds":100,"end_seconds":200,"transcript":"b"*20,"citation":"media:100-200"},
      {"start_seconds":200,"end_seconds":300,"transcript":"c"*20,"citation":"media:200-300"},
    ]
    chunks=semantic_chunk_segments(segs,max_chars=100,max_seconds=150)
    assert len(chunks) == 3
    assert chunks[1]["citations"] == ["media:100-200"]

def test_eval_measures_visual_sampling_reduction():
    manifest={"duration_seconds":100,"segments":[],"semantic_chunks":[],
              "visual_frames":[{},{}],"sampling":{"raw_frame_count":10},
              "visual_events":[],"timeline":[],"claim_candidates":[],"visual_evidence_candidates":[]}
    result=evaluate_video_intelligence(manifest)
    assert result["visual"]["sampling_reduction_ratio"] == 0.8
