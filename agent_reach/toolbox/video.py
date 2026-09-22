"""Video/audio evidence ingestion for Ahmed Research Engine."""
from __future__ import annotations
import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import json
import shutil
import subprocess
from agent_reach.config import Config
from agent_reach.transcribe import (
    CHUNK_SECONDS, NoProviderConfigured, TranscribeError, _provider_key,
    _require_duration_within_budget, chunk_audio, compress_audio,
    download_audio, transcribe_chunk,
)

@dataclass(frozen=True)
class MediaSegment:
    index: int
    start_seconds: float
    end_seconds: float
    transcript: str
    transcript_sha256: str
    @property
    def citation(self) -> str:
        return f"media:{self.start_seconds:.1f}-{self.end_seconds:.1f}"

def _select_provider(provider: str, config: Config) -> str:
    if provider != "auto":
        if provider not in ("groq", "openai"):
            raise TranscribeError(f"unknown provider: {provider}")
        if not _provider_key(provider, config):
            raise NoProviderConfigured(f"{provider}: API key is not configured")
        return provider
    for candidate in ("groq", "openai"):
        if _provider_key(candidate, config):
            return candidate
    raise NoProviderConfigured("no transcription provider configured")


def _extract_keyframes(src: Path, out_dir: Path, interval_seconds: int = 30) -> list[dict[str, Any]]:
    """Extract bounded keyframes for downstream vision/OCR models."""
    if interval_seconds < 5 or interval_seconds > 300:
        raise TranscribeError("frame interval must be between 5 and 300 seconds")
    if not shutil.which("ffmpeg"):
        raise TranscribeError("ffmpeg not found in PATH")
    frame_dir = out_dir / "frames"
    frame_dir.mkdir(exist_ok=True)
    pattern = frame_dir / "frame_%05d.jpg"
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-vf",
           f"fps=1/{interval_seconds},scale='min(1280,iw)':-2", "-q:v", "3", str(pattern)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise TranscribeError(f"frame extraction failed: {proc.stderr.strip()[:300]}")
    frames = sorted(frame_dir.glob("frame_*.jpg"))[:500]
    return [{"index": i, "timestamp_seconds": float(i * interval_seconds),
             "path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for i, p in enumerate(frames)]


def build_unified_timeline(segments: list[dict[str, Any]],
                           visual_events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Merge transcript and visual evidence without pretending either verifies the other."""
    events = []
    for seg in segments:
        events.append({"kind": "speech", "timestamp_seconds": seg["start_seconds"],
                       "end_seconds": seg["end_seconds"], "text": seg["transcript"],
                       "citation": seg["citation"]})
    for event in visual_events or []:
        item = dict(event)
        item["kind"] = item.get("kind") or "visual"
        events.append(item)
    return sorted(events, key=lambda x: float(x.get("timestamp_seconds", 0.0)))


def extract_claim_candidates(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic claim-candidate packaging; semantic classification remains model-side."""
    claims = []
    for seg in manifest.get("segments", []):
        text = str(seg.get("transcript") or "").strip()
        if not text:
            continue
        claims.append({
            "candidate_id": f"MC-{int(seg['index']) + 1:04d}",
            "statement_source_text": text,
            "source_url": manifest.get("source_url"),
            "citation": seg.get("citation"),
            "start_seconds": seg.get("start_seconds"),
            "end_seconds": seg.get("end_seconds"),
            "status": "UNVERIFIED_SOURCE_STATEMENT",
            "requires_external_verification": True,
        })
    return claims


def register_media_evidence(store: Any, run_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """Persist a media manifest and its timestamped statements into the Evidence Ledger."""
    source_url = str(manifest.get("source_url") or "").strip()
    transcript = str(manifest.get("full_transcript") or "").strip()
    if not source_url or not transcript:
        raise ValueError("media manifest requires source_url and full_transcript")
    source = store.record_source(
        run_id,
        url=source_url,
        content=transcript,
        retrieval_tool="ahmed-toolbox",
        retrieval_method="media_transcription",
        source_type="VIDEO_AUDIO",
        primary_source=True,
        metadata={
            "duration_seconds": manifest.get("duration_seconds"),
            "transcription_provider": manifest.get("provider"),
            "provenance": manifest.get("provenance") or {},
            "visual_frames": manifest.get("visual_frames") or [],
        },
        retrieval_history=[
            {"stage": "ACQUISITION", "tool": "yt-dlp", "method": "audio/video retrieval", "status": "SUCCESS"},
            {"stage": "TRANSCRIPTION", "tool": str(manifest.get("provider") or "unknown"), "method": "speech_to_text", "status": "SUCCESS"},
        ],
    )
    evidence = []
    for candidate in manifest.get("claim_candidates") or []:
        passage = str(candidate.get("statement_source_text") or "").strip()
        if not passage:
            continue
        item = store.add_evidence(
            run_id,
            source_id=source["source_id"],
            supporting_passage=passage,
            observation_type="UNKNOWN",
            structured_fact={
                "media_citation": candidate.get("citation"),
                "start_seconds": candidate.get("start_seconds"),
                "end_seconds": candidate.get("end_seconds"),
                "verification_status": "UNVERIFIED_SOURCE_STATEMENT",
            },
            extraction_method="timestamped_media_transcription",
        )
        evidence.append(item)
    return {"source": source, "evidence_items": evidence, "evidence_count": len(evidence)}


def verification_queries(manifest: dict[str, Any], max_queries: int = 20) -> list[dict[str, Any]]:
    """Create bounded external-verification work items from media claims."""
    work = []
    for candidate in (manifest.get("claim_candidates") or [])[:max_queries]:
        statement = str(candidate.get("statement_source_text") or "").strip()
        if statement:
            work.append({
                "candidate_id": candidate.get("candidate_id"),
                "query": statement[:1000],
                "citation": candidate.get("citation"),
                "required_source_relation": "INDEPENDENT_WHEN_POSSIBLE",
                "accept_source_claim_as_verified": False,
            })
    return work

def ingest_media(source_url: str, *, provider: str = "auto",
                 language: str | None = None, config: Config | None = None) -> dict[str, Any]:
    """Return a bounded timestamped transcript manifest for a public media URL."""
    cfg = config or Config()
    selected = _select_provider(provider, cfg)
    with TemporaryDirectory(prefix="ahmed-media-") as tmp:
        root = Path(tmp)
        downloaded = download_audio(source_url, root)
        duration = _require_duration_within_budget(downloaded)
        frames = _extract_keyframes(downloaded, root)
        compressed = compress_audio(downloaded, root)
        chunks = chunk_audio(compressed, root, CHUNK_SECONDS)
        segments = []
        for index, chunk in enumerate(chunks):
            transcript = transcribe_chunk(chunk, selected, config=cfg).strip()
            start = float(index * CHUNK_SECONDS)
            end = min(float((index + 1) * CHUNK_SECONDS), duration)
            segments.append(MediaSegment(index, start, end, transcript,
                hashlib.sha256(transcript.encode("utf-8")).hexdigest()))
    return {
        "source_url": source_url,
        "media_type": "video_or_audio",
        "duration_seconds": duration,
        "provider": selected,
        "language_hint": language,
        "segments": [{**asdict(s), "citation": s.citation} for s in segments],
        "visual_frames": [{k: v for k, v in frame.items() if k != "path"} for frame in frames],
        "full_transcript": "\n\n".join(f"[{s.citation}] {s.transcript}" for s in segments if s.transcript),
        "provenance": {
            "retrieval_tool": "yt-dlp", "audio_processing": "ffmpeg",
            "transcription_provider": selected, "timestamp_basis": "bounded audio chunks",
        },
        "timeline": build_unified_timeline([{**asdict(s), "citation": s.citation} for s in segments]),
        "claim_candidates": extract_claim_candidates({"source_url": source_url, "segments": [{**asdict(s), "citation": s.citation} for s in segments]}),
        "limitations": [
            "timestamps are chunk-level, not word-level",
            "speaker diarization is not performed",
            "keyframes are extracted, but semantic vision/OCR requires a configured downstream vision model",
            "transcript statements are source evidence, not independently verified facts",
        ],
    }
