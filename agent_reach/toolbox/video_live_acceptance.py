"""Live zero-model acceptance test for Ahmed Video Intelligence media acquisition."""
from __future__ import annotations

import json
import os

from agent_reach.toolbox.video import probe_media_acquisition

DEFAULT_BENCHMARK_URL = "https://youtu.be/9Ignyhh1WqQ?si=pNs6thfrPoS3X7Pw"


def main() -> None:
    url = os.getenv("AHMED_VIDEO_BENCHMARK_URL", DEFAULT_BENCHMARK_URL).strip()
    result = probe_media_acquisition(url, include_visual=True)
    print("AHMED_VIDEO_BENCHMARK=" + json.dumps(result, separators=(",", ":"), sort_keys=True), flush=True)
    if result.get("status") != "OK":
        raise SystemExit(1)
    if not result.get("audio", {}).get("acquired"):
        raise SystemExit("audio acquisition failed")
    if not result.get("visual", {}).get("acquired"):
        raise SystemExit("visual acquisition failed")
    if int(result.get("visual", {}).get("preview_frame_count") or 0) < 1:
        raise SystemExit("no preview frames extracted")


if __name__ == "__main__":
    main()
