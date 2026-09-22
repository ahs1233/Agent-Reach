from agent_reach.toolbox.video import extract_media_text

def test_extract_media_text_keeps_speech_and_ocr_separate():
    manifest={
        "segments":[{"citation":"media:0-10","start_seconds":0,"end_seconds":10,"transcript":"spoken words"}],
        "visual_events":[{"citation":"frame:5.0","timestamp_seconds":5,"frame_sha256":"abc",
                          "analysis":{"visible_text":"SLIDE TEXT"}}],
    }
    out=extract_media_text(manifest)
    assert out["plain_transcript"] == "spoken words"
    assert out["plain_visual_text"] == "SLIDE TEXT"
    assert out["speech_text"][0]["citation"] == "media:0-10"
    assert out["visual_text"][0]["citation"] == "frame:5.0"
