"""Live acceptance test for Ahmed Video Intelligence."""
from __future__ import annotations
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from agent_reach.toolbox.video import (
    probe_media_acquisition, _extract_keyframes, extract_visual_ocr_local,
    build_av_evidence_timeline,
)
from agent_reach.transcribe import (
    download_audio, download_video_for_frames, transcribe_local, build_transcript_evidence,
)
DEFAULT_BENCHMARK_URL = "https://youtu.be/9Ignyhh1WqQ?si=pNs6thfrPoS3X7Pw"

def main() -> None:
    url = os.getenv("AHMED_VIDEO_BENCHMARK_URL", DEFAULT_BENCHMARK_URL).strip()
    result = probe_media_acquisition(url, include_visual=True)
    print("AHMED_VIDEO_BENCHMARK=" + json.dumps(result, separators=(",", ":"), sort_keys=True), flush=True)
    if result.get("status") != "OK" or not result.get("audio", {}).get("acquired") or not result.get("visual", {}).get("acquired"):
        raise SystemExit("media acquisition failed")
    if int(result.get("visual", {}).get("preview_frame_count") or 0) < 1:
        raise SystemExit("no preview frames extracted")
    if os.getenv("AHMED_VIDEO_TRANSCRIBE_ACCEPTANCE", "").lower() in {"1","true","yes"}:
        with TemporaryDirectory(prefix="ahmed-av-") as tmp:
            root = Path(tmp)
            audio = download_audio(url, root)
            transcript = transcribe_local(audio)
            evidence = build_transcript_evidence(transcript, source_url=url)
            video = download_video_for_frames(url, root)
            frames = _extract_keyframes(video, root, interval_seconds=5)
            visual = extract_visual_ocr_local(frames)
            timeline = build_av_evidence_timeline(evidence, visual)
            print("AHMED_VIDEO_TRANSCRIPT=" + json.dumps(transcript, ensure_ascii=False, separators=(",", ":")), flush=True)
            print("AHMED_VIDEO_SPEECH_EVIDENCE=" + json.dumps(evidence, ensure_ascii=False, separators=(",", ":")), flush=True)
            print("AHMED_VIDEO_VISUAL_EVIDENCE=" + json.dumps(visual, ensure_ascii=False, separators=(",", ":")), flush=True)
            print("AHMED_VIDEO_AV_TIMELINE=" + json.dumps(timeline, ensure_ascii=False, separators=(",", ":")), flush=True)
            if not transcript.get("text"):
                raise SystemExit("local transcription produced no text")

if __name__ == "__main__":
    main()
