from agent_reach.toolbox.video import MediaSegment

def test_precise_media_citation_supports_sub_chunk_timing():
    s=MediaSegment(0,12.34,18.91,"hello","x")
    assert s.citation == "media:12.3-18.9"

def test_segment_hash_keeps_text_provenance():
    s=MediaSegment(1,1,2,"text","abc")
    assert s.transcript_sha256 == "abc"
