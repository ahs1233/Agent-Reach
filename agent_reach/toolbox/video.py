"""Video/audio evidence ingestion for Ahmed Research Engine."""
from __future__ import annotations
import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
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

def ingest_media(source_url: str, *, provider: str = "auto",
                 language: str | None = None, config: Config | None = None) -> dict[str, Any]:
    """Return a bounded timestamped transcript manifest for a public media URL."""
    cfg = config or Config()
    selected = _select_provider(provider, cfg)
    with TemporaryDirectory(prefix="ahmed-media-") as tmp:
        root = Path(tmp)
        downloaded = download_audio(source_url, root)
        duration = _require_duration_within_budget(downloaded)
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
        "full_transcript": "\n\n".join(f"[{s.citation}] {s.transcript}" for s in segments if s.transcript),
        "provenance": {
            "retrieval_tool": "yt-dlp", "audio_processing": "ffmpeg",
            "transcription_provider": selected, "timestamp_basis": "bounded audio chunks",
        },
        "limitations": [
            "timestamps are chunk-level, not word-level",
            "speaker diarization is not performed",
            "visual scene/OCR evidence is not yet extracted",
            "transcript statements are source evidence, not independently verified facts",
        ],
    }
