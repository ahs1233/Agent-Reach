from agent_reach.toolbox import video

class FakeConfig:
    def get(self, key):
        return {"groq_api_key": "x"}.get(key)

def test_select_provider_prefers_configured_groq():
    assert video._select_provider("auto", FakeConfig()) == "groq"

def test_media_segment_has_stable_timestamp_citation():
    item = video.MediaSegment(0, 0.0, 10.0, "hello", "abc")
    assert item.citation == "media:0.0-10.0"
