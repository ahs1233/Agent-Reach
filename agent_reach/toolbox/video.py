"""Video/audio evidence ingestion for Ahmed Research Engine."""
from __future__ import annotations
import hashlib
import base64
import requests
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
    _require_duration_within_budget, _probe_audio_duration, chunk_audio, compress_audio,
    download_audio, download_video_for_frames, transcribe_chunk, transcribe_chunk_timed,
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


def probe_media_acquisition(source_url: str, *, include_visual: bool = True) -> dict[str, Any]:
    """Preflight direct media acquisition without spending transcription/vision API calls."""
    with TemporaryDirectory(prefix="ahmed-media-probe-") as tmp:
        root = Path(tmp)
        audio = download_audio(source_url, root)
        duration = _probe_audio_duration(audio)
        result: dict[str, Any] = {
            "status": "OK",
            "source_url": source_url,
            "audio": {
                "acquired": True,
                "bytes": audio.stat().st_size,
                "duration_seconds": duration,
                "suffix": audio.suffix,
            },
            "visual": {"requested": include_visual, "acquired": False},
        }
        if include_visual:
            video = download_video_for_frames(source_url, root)
            frames = _extract_keyframes(video, root, interval_seconds=60)
            result["visual"] = {
                "requested": True,
                "acquired": True,
                "bytes": video.stat().st_size,
                "suffix": video.suffix,
                "preview_frame_count": len(frames),
                "preview_frame_sha256": [f["sha256"] for f in frames[:3]],
            }
        return result


def extract_media_evidence_bundle(source_url: str, *, frame_interval_seconds: int = 15) -> dict[str, Any]:
    """Model-free evidence package for handoff to ChatGPT or another intelligence layer."""
    with TemporaryDirectory(prefix="ahmed-media-evidence-") as tmp:
        root = Path(tmp)
        audio = download_audio(source_url, root)
        duration = _probe_audio_duration(audio)
        video = download_video_for_frames(source_url, root)
        frames = _extract_keyframes(video, root, interval_seconds=frame_interval_seconds)
        return {
            "schema": "ahmed.media_evidence_bundle.v1",
            "source_url": source_url,
            "duration_seconds": duration,
            "audio": {
                "bytes": audio.stat().st_size,
                "suffix": audio.suffix,
                "sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
            },
            "video": {
                "bytes": video.stat().st_size,
                "suffix": video.suffix,
                "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
            },
            "frames": [
                {
                    "index": frame["index"],
                    "timestamp_seconds": frame["timestamp_seconds"],
                    "citation": f"media:{frame['timestamp_seconds']:.1f}",
                    "sha256": frame["sha256"],
                }
                for frame in frames
            ],
            "handoff": {
                "target": "chatgpt",
                "purpose": "deep_understanding",
                "model_calls_used": 0,
                "instructions": [
                    "Preserve timestamps and citations.",
                    "Separate extraction from interpretation.",
                    "Use the attached media evidence for cross-modal deep understanding.",
                ],
            },
        }


def _extract_keyframes(src: Path, out_dir: Path, interval_seconds: int = 30) -> list[dict[str, Any]]:
    """Extract real timestamped frames bounded by the source duration."""
    if interval_seconds < 1 or interval_seconds > 300:
        raise TranscribeError("frame interval must be between 1 and 300 seconds")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise TranscribeError("ffmpeg/ffprobe not found in PATH")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
        capture_output=True, text=True, timeout=60,
    )
    try:
        duration = max(0.0, float((probe.stdout or "0").strip()))
    except ValueError:
        duration = 0.0
    frame_dir = out_dir / "frames"
    frame_dir.mkdir(exist_ok=True)
    for stale in frame_dir.glob("frame_*.jpg"):
        stale.unlink()
    timestamps = [0.0]
    t = float(interval_seconds)
    while duration > 0 and t < duration:
        timestamps.append(t)
        t += interval_seconds
    frames = []
    for i, ts in enumerate(timestamps[:48]):
        path = frame_dir / f"frame_{i:05d}.jpg"
        proc = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{ts:.3f}", "-i", str(src),
             "-map", "0:v:0", "-frames:v", "1", "-vf", "scale='min(1920,iw)':-2",
             "-q:v", "2", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        if path.exists() and path.stat().st_size:
            frames.append({
                "index": len(frames), "timestamp_seconds": ts, "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
    if not frames:
        raise TranscribeError("frame extraction produced no decodable frames")
    return frames


def _ocr_variant(frame_path: str, *, psm: str) -> str:
    proc = subprocess.run(
        ["tesseract", frame_path, "stdout", "-l", "ara+eng", "--psm", psm,
         "-c", "preserve_interword_spaces=1"],
        capture_output=True, text=True, timeout=60,
    )
    return (proc.stdout or "").strip()


def extract_visual_ocr_local(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Multi-pass local OCR with noise rejection for charts and social-video overlays."""
    if not shutil.which("tesseract"):
        raise TranscribeError("tesseract not found in PATH")
    events = []
    for frame in frames:
        candidates = [_ocr_variant(frame["path"], psm=mode) for mode in ("6", "11", "12")]
        def score(text: str) -> tuple[int, int]:
            useful = sum(ch.isalnum() for ch in text)
            return (useful, -len(text))
        best = max(candidates, key=score, default="")
        useful = sum(ch.isalnum() for ch in best)
        noisy = useful < 3
        events.append({
            "kind": "visual_ocr",
            "timestamp_seconds": float(frame["timestamp_seconds"]),
            "citation": f"media:{float(frame['timestamp_seconds']):.1f}",
            "frame_sha256": frame["sha256"],
            "visible_text": "" if noisy else best,
            "has_text": not noisy,
            "ocr_passes": 3,
            "ocr_quality": "LOW" if noisy else "RAW",
        })
    return events

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




def _responses_json(prompt: str, *, config: Config, model: str) -> dict[str, Any]:
    key = config.get("openai_api_key")
    if not key:
        raise NoProviderConfigured("openai: missing openai_api_key")
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "input": prompt, "text": {"format": {"type": "json_object"}}},
        timeout=120,
    )
    if response.status_code >= 400:
        raise TranscribeError(f"language/reasoning request failed: HTTP {response.status_code}")
    data = response.json()
    text = ""
    for item in data.get("output") or []:
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                text += str(part.get("text") or "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise TranscribeError("model returned invalid JSON") from exc


def translate_media_manifest(manifest: dict[str, Any], target_language: str, *,
                             config: Config | None = None,
                             model: str = "gpt-5.6-luna") -> dict[str, Any]:
    """Translate transcript segments while preserving source text and timestamps."""
    cfg = config or Config()
    translated = []
    for seg in manifest.get("segments") or []:
        source = str(seg.get("transcript") or "").strip()
        if not source:
            continue
        result = _responses_json(
            "Translate the following media transcript faithfully into " + target_language +
            ". Preserve names, numbers, uncertainty, tone and technical terminology. "
            "Do not summarize or add facts. Return JSON {translation:string}.\nSOURCE:\n" + source,
            config=cfg, model=model,
        )
        translated.append({
            "index": seg.get("index"), "start_seconds": seg.get("start_seconds"),
            "end_seconds": seg.get("end_seconds"), "citation": seg.get("citation"),
            "source_text": source, "translation": str(result.get("translation") or ""),
            "target_language": target_language, "model": model,
        })
    return {"target_language": target_language, "segments": translated,
            "full_translation": "\n\n".join(
                f"[{x['citation']}] {x['translation']}" for x in translated)}


def deep_understand_media(manifest: dict[str, Any], *, config: Config | None = None,
                          model: str = "gpt-5.6-luna") -> dict[str, Any]:
    """Build a structured semantic model of the media, distinct from fact verification."""
    cfg = config or Config()
    transcript = str(manifest.get("full_transcript") or "")[:120000]
    visual = json.dumps(manifest.get("visual_events") or [], ensure_ascii=False)[:60000]
    prompt = """Analyze this video/audio deeply as a reasoning artifact, not merely as claims to fact-check.
Return ONLY JSON with:
summary, central_thesis, purpose_and_intent, argument_map, key_concepts,
causal_model, assumptions, evidence_used_by_speaker, counterarguments_or_missing_views,
contradictions_or_tensions, rhetorical_strategy, uncertainty_and_ambiguity,
important_entities, numbers_and_dates, implications, open_questions,
scene_or_topic_structure, strongest_insights, possible_misinterpretations.
Separate what the source explicitly says from your interpretation. Never infer private mental states.
Every important point should include supporting media citations/timestamps when possible.
TRANSCRIPT:
""" + transcript + "\nVISUAL EVENTS:\n" + visual
    result = _responses_json(prompt, config=cfg, model=model)
    result["analysis_model"] = model
    result["analysis_type"] = "DEEP_SEMANTIC_UNDERSTANDING"
    result["verification_status"] = "INTERPRETATION_NOT_FACT_VERIFICATION"
    return result






def detect_topic_boundaries(segments: list[dict[str, Any]], *, config: Config | None = None,
                            model: str = "gpt-5.6-luna") -> list[dict[str, Any]]:
    if not segments:
        return []
    cfg = config or Config()
    compact = [{"i": i, "start": s.get("start_seconds"), "end": s.get("end_seconds"),
                "citation": s.get("citation"), "text": str(s.get("transcript") or "")[:1200]}
               for i, s in enumerate(segments)]
    prompt = ("Detect genuine topic boundaries. Return ONLY JSON with key chapters; each chapter has "
              "title,start_index,end_index,reason. Use contiguous ranges; do not split for pauses/examples.\n" +
              json.dumps(compact, ensure_ascii=False)[:120000])
    result = _responses_json(prompt, config=cfg, model=model)
    chapters = []
    for n, item in enumerate(result.get("chapters") or []):
        try:
            a, b = int(item["start_index"]), int(item["end_index"])
            if a < 0 or b < a or b >= len(segments): continue
            chapters.append({"chapter_id": f"CH-{n+1:03d}", "title": str(item.get("title") or ""),
                "start_seconds": segments[a]["start_seconds"], "end_seconds": segments[b]["end_seconds"],
                "citations": [s["citation"] for s in segments[a:b+1]], "reason": str(item.get("reason") or "")})
        except (KeyError, TypeError, ValueError): continue
    return chapters

def infer_speaker_turns(segments: list[dict[str, Any]], *, config: Config | None = None,
                        model: str = "gpt-5.6-luna") -> list[dict[str, Any]]:
    if not segments:
        return []
    cfg = config or Config()
    compact = [{"i": i, "citation": s.get("citation"), "text": str(s.get("transcript") or "")[:1200]}
               for i, s in enumerate(segments)]
    prompt = ("Infer speaker turns only from textual evidence. Return ONLY JSON with key turns; each turn has "
              "start_index,end_index,speaker_label,confidence,basis. Use SPEAKER_UNKNOWN if insufficient. "
              "This is not acoustic voice identification.\n" + json.dumps(compact, ensure_ascii=False)[:120000])
    result = _responses_json(prompt, config=cfg, model=model)
    turns = []
    for item in result.get("turns") or []:
        try:
            a, b = int(item["start_index"]), int(item["end_index"])
            if a < 0 or b < a or b >= len(segments): continue
            turns.append({"speaker_label": str(item.get("speaker_label") or "SPEAKER_UNKNOWN"),
                "confidence": str(item.get("confidence") or "LOW"), "basis": str(item.get("basis") or ""),
                "start_seconds": segments[a]["start_seconds"], "end_seconds": segments[b]["end_seconds"],
                "citations": [s["citation"] for s in segments[a:b+1]], "method": "TEXTUAL_SPEAKER_TURN_INFERENCE"})
        except (KeyError, TypeError, ValueError): continue
    return turns

def _image_fingerprint(path: Path, grid: int = 16) -> bytes:
    """Dependency-free perceptual fingerprint using ffmpeg grayscale raw pixels."""
    proc = subprocess.run([
        "ffmpeg","-loglevel","error","-i",str(path),"-vf",f"scale={grid}:{grid},format=gray",
        "-frames:v","1","-f","rawvideo","-"
    ], capture_output=True, timeout=30)
    if proc.returncode != 0 or len(proc.stdout) < grid * grid:
        return b""
    return proc.stdout[:grid * grid]


def _fingerprint_distance(a: bytes, b: bytes) -> float:
    if not a or not b or len(a) != len(b):
        return 1.0
    return sum(abs(x-y) for x,y in zip(a,b)) / (255.0 * len(a))


def adaptive_sample_frames(frames: list[dict[str, Any]], *,
                           change_threshold: float = 0.10,
                           max_frames: int = 48) -> list[dict[str, Any]]:
    """Keep visually meaningful changes and suppress near-duplicate frames."""
    if not frames:
        return []
    selected: list[dict[str, Any]] = []
    previous_fp = b""
    for frame in frames:
        path = Path(str(frame.get("path") or ""))
        if not path.is_file():
            continue
        fp = _image_fingerprint(path)
        distance = _fingerprint_distance(previous_fp, fp) if previous_fp else 1.0
        if not selected or distance >= change_threshold:
            item = dict(frame)
            item["visual_change_score"] = round(distance, 4)
            selected.append(item)
            previous_fp = fp
        if len(selected) >= max_frames:
            break
    if frames and selected:
        last = frames[-1]
        if selected[-1].get("sha256") != last.get("sha256") and len(selected) < max_frames:
            item = dict(last)
            item["visual_change_score"] = None
            selected.append(item)
    return selected


def semantic_chunk_segments(segments: list[dict[str, Any]], *,
                            max_chars: int = 7000,
                            max_seconds: float = 240.0) -> list[dict[str, Any]]:
    """Create bounded semantic-ready chunks while preserving timestamp provenance."""
    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    chars = 0
    for seg in segments:
        text = str(seg.get("transcript") or "").strip()
        if not text:
            continue
        start = float(seg.get("start_seconds") or 0.0)
        prospective_end = float(seg.get("end_seconds") or start)
        current_start = float(current[0].get("start_seconds") or 0.0) if current else start
        exceeds = current and (chars + len(text) > max_chars or prospective_end - current_start > max_seconds)
        if exceeds:
            chunks.append(_finalize_semantic_chunk(current, len(chunks)))
            current, chars = [], 0
        current.append(seg)
        chars += len(text)
    if current:
        chunks.append(_finalize_semantic_chunk(current, len(chunks)))
    return chunks


def _finalize_semantic_chunk(items: list[dict[str, Any]], index: int) -> dict[str, Any]:
    start = float(items[0].get("start_seconds") or 0.0)
    end = float(items[-1].get("end_seconds") or start)
    return {
        "chunk_id": f"SC-{index+1:04d}", "start_seconds": start, "end_seconds": end,
        "citations": [x.get("citation") for x in items if x.get("citation")],
        "text": "\n".join(str(x.get("transcript") or "").strip() for x in items),
        "source_segment_count": len(items),
    }

def evaluate_video_intelligence(manifest: dict[str, Any], *,
                                understanding: dict[str, Any] | None = None,
                                longitudinal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deterministic baseline metrics for Video Intelligence quality/cost regressions."""
    segments = manifest.get("segments") or []
    frames = manifest.get("visual_frames") or []
    visual_events = manifest.get("visual_events") or []
    duration = float(manifest.get("duration_seconds") or 0.0)
    transcript_chars = sum(len(str(x.get("transcript") or "")) for x in segments)
    cited_segments = sum(1 for x in segments if x.get("citation"))
    ocr_events = sum(1 for x in visual_events
                     if str((x.get("analysis") or {}).get("visible_text") or "").strip())
    graph = build_media_knowledge_graph(longitudinal or {})
    contradictions = (longitudinal or {}).get("internal_contradictions") or []
    chapters = (longitudinal or {}).get("chapters") or []
    return {
        "schema_version": "video-eval-v1",
        "duration_seconds": duration,
        "speech": {
            "segment_count": len(segments),
            "transcript_chars": transcript_chars,
            "citation_coverage": (cited_segments / len(segments)) if segments else 0.0,
            "average_segment_seconds": (duration / len(segments)) if segments and duration else None,
            "semantic_chunk_count": len(manifest.get("semantic_chunks") or []),
        },
        "visual": {
            "raw_frame_count": int((manifest.get("sampling") or {}).get("raw_frame_count") or len(frames)),
            "sampled_frame_count": len(frames),
            "sampling_reduction_ratio": (1.0 - len(frames) / int((manifest.get("sampling") or {}).get("raw_frame_count"))) if int((manifest.get("sampling") or {}).get("raw_frame_count") or 0) else 0.0,
            "analyzed_frame_count": len(visual_events),
            "ocr_event_count": ocr_events,
            "analysis_coverage": (len(visual_events) / len(frames)) if frames else 0.0,
        },
        "reasoning": {
            "has_deep_understanding": bool(understanding),
            "chapter_count": len(chapters),
            "knowledge_graph_nodes": graph["node_count"],
            "knowledge_graph_edges": graph["edge_count"],
            "internal_contradiction_count": len(contradictions),
        },
        "provenance": {
            "timeline_event_count": len(manifest.get("timeline") or []),
            "claim_candidate_count": len(manifest.get("claim_candidates") or []),
            "visual_evidence_candidate_count": len(manifest.get("visual_evidence_candidates") or []),
        },
    }


def compare_video_evaluations(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Compare two benchmark snapshots without inventing a single vanity score."""
    def get(d: dict[str, Any], path: tuple[str, ...]) -> float:
        cur: Any = d
        for key in path:
            cur = (cur or {}).get(key) if isinstance(cur, dict) else None
        return float(cur or 0.0)
    metrics = [
        ("speech.citation_coverage", ("speech","citation_coverage"), True),
        ("speech.average_segment_seconds", ("speech","average_segment_seconds"), False),
        ("visual.analysis_coverage", ("visual","analysis_coverage"), True),
        ("reasoning.knowledge_graph_nodes", ("reasoning","knowledge_graph_nodes"), None),
        ("reasoning.knowledge_graph_edges", ("reasoning","knowledge_graph_edges"), None),
        ("provenance.timeline_event_count", ("provenance","timeline_event_count"), None),
    ]
    changes = []
    for name, path, higher_better in metrics:
        before, after = get(baseline, path), get(candidate, path)
        delta = after - before
        if higher_better is True:
            direction = "IMPROVED" if delta > 0 else ("REGRESSED" if delta < 0 else "UNCHANGED")
        elif higher_better is False:
            direction = "IMPROVED" if delta < 0 else ("REGRESSED" if delta > 0 else "UNCHANGED")
        else:
            direction = "CHANGED" if delta else "UNCHANGED"
        changes.append({"metric": name, "baseline": before, "candidate": after,
                        "delta": delta, "direction": direction})
    return {"schema_version": "video-eval-compare-v1", "changes": changes,
            "note": "No aggregate score: quality, granularity, coverage, latency and cost must remain inspectable."}

def reason_across_time(manifest: dict[str, Any], understanding: dict[str, Any] | None = None, *,
                       config: Config | None = None,
                       model: str = "gpt-5.6-luna") -> dict[str, Any]:
    """Trace how ideas, claims, entities and contradictions evolve across the full media timeline."""
    cfg = config or Config()
    timeline = manifest.get("timeline") or []
    compact = []
    for event in timeline[:1000]:
        compact.append({
            "kind": event.get("kind"), "t": event.get("timestamp_seconds"),
            "end": event.get("end_seconds"), "citation": event.get("citation"),
            "text": event.get("text"), "analysis": event.get("analysis"),
        })
    prompt = """Perform longitudinal reasoning over this media timeline.
Return ONLY JSON with:
chapters, thesis_evolution, argument_steps, claim_graph, entity_graph,
causal_links, callbacks_and_dependencies, internal_consistencies,
internal_contradictions, unresolved_tensions, premise_to_conclusion_paths,
turning_points, evidence_dependencies, narrative_or_rhetorical_progression,
cross_time_insights, questions_for_verification.
Rules:
- Every node/edge/contradiction must cite one or more media timestamps/citations.
- Distinguish explicit source statements from analytical inference.
- A contradiction requires incompatible propositions, not mere topic change.
- Never infer private mental states or hidden intent.
- Do not mark external-world claims verified.
TIMELINE:
""" + json.dumps(compact, ensure_ascii=False)[:180000]
    if understanding:
        prompt += "\nPRIOR SEMANTIC MODEL:\n" + json.dumps(understanding, ensure_ascii=False)[:60000]
    result = _responses_json(prompt, config=cfg, model=model)
    result["analysis_type"] = "LONGITUDINAL_MEDIA_REASONING"
    result["analysis_model"] = model
    result["verification_status"] = "STRUCTURAL_REASONING_NOT_EXTERNAL_FACT_VERIFICATION"
    return result


def build_media_knowledge_graph(longitudinal: dict[str, Any]) -> dict[str, Any]:
    """Normalize model graph output into portable nodes/edges with provenance."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    graph = longitudinal.get("claim_graph") or {}
    raw_nodes = graph.get("nodes") if isinstance(graph, dict) else []
    raw_edges = graph.get("edges") if isinstance(graph, dict) else []
    for i, node in enumerate(raw_nodes or []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or f"N-{i+1:04d}")
        nodes[node_id] = {
            "id": node_id,
            "type": str(node.get("type") or "claim"),
            "label": str(node.get("label") or node.get("statement") or ""),
            "citations": list(node.get("citations") or []),
            "epistemic_status": str(node.get("epistemic_status") or "SOURCE_OR_INFERENCE"),
        }
    for i, edge in enumerate(raw_edges or []):
        if not isinstance(edge, dict):
            continue
        edges.append({
            "id": str(edge.get("id") or f"EDGE-{i+1:04d}"),
            "source": str(edge.get("source") or ""),
            "target": str(edge.get("target") or ""),
            "relation": str(edge.get("relation") or "RELATED_TO"),
            "citations": list(edge.get("citations") or []),
        })
    return {"nodes": list(nodes.values()), "edges": edges,
            "node_count": len(nodes), "edge_count": len(edges)}

def extract_media_text(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return speech text and visible/OCR text as separate provenance-preserving streams."""
    speech = [{"citation": s.get("citation"), "start_seconds": s.get("start_seconds"),
               "end_seconds": s.get("end_seconds"), "text": s.get("transcript")}
              for s in manifest.get("segments") or [] if s.get("transcript")]
    visual = []
    for event in manifest.get("visual_events") or []:
        analysis = event.get("analysis") or {}
        text = str(analysis.get("visible_text") or "").strip()
        if text:
            visual.append({"citation": event.get("citation"),
                           "timestamp_seconds": event.get("timestamp_seconds"),
                           "frame_sha256": event.get("frame_sha256"), "text": text})
    return {"speech_text": speech, "visual_text": visual,
            "plain_transcript": "\n".join(str(x["text"]) for x in speech),
            "plain_visual_text": "\n".join(str(x["text"]) for x in visual)}

def analyze_visual_frames(frames: list[dict[str, Any]], *, config: Config | None = None,
                          model: str = "gpt-5.6-luna", max_frames: int = 24) -> list[dict[str, Any]]:
    """Analyze sampled keyframes with a multimodal model and return grounded visual evidence."""
    cfg = config or Config()
    key = cfg.get("openai_api_key")
    if not key:
        raise NoProviderConfigured("openai: missing openai_api_key for visual analysis")
    selected = frames[:max_frames]
    events = []
    for frame in selected:
        path = Path(str(frame.get("path") or ""))
        if not path.is_file():
            continue
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        prompt = (
            "Analyze this single video keyframe as evidence. Return ONLY JSON with keys: "
            "scene_description, visible_text, chart_or_table, people_or_speakers, "
            "notable_visual_claims. Do not infer facts not visibly supported."
        )
        payload = {
            "model": model,
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": "data:image/jpeg;base64," + encoded},
            ]}],
            "text": {"format": {"type": "json_object"}},
        }
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload, timeout=90,
        )
        if response.status_code >= 400:
            raise TranscribeError(f"vision request failed: HTTP {response.status_code}")
        data = response.json()
        output_text = ""
        for item in data.get("output") or []:
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    output_text += str(part.get("text") or "")
        try:
            analysis = json.loads(output_text)
        except json.JSONDecodeError:
            analysis = {"scene_description": output_text, "visible_text": "", "parse_warning": True}
        events.append({
            "kind": "visual",
            "timestamp_seconds": frame.get("timestamp_seconds"),
            "frame_sha256": frame.get("sha256"),
            "analysis": analysis,
            "text": str(analysis.get("visible_text") or analysis.get("scene_description") or ""),
            "citation": f"frame:{float(frame.get('timestamp_seconds') or 0):.1f}",
            "model": model,
        })
    return events


def visual_evidence_candidates(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert model-grounded frame observations into auditable evidence candidates."""
    candidates = []
    for event in events:
        analysis = event.get("analysis") or {}
        visible = str(analysis.get("visible_text") or "").strip()
        description = str(analysis.get("scene_description") or "").strip()
        claims = analysis.get("notable_visual_claims") or []
        passage = visible or description
        if not passage and not claims:
            continue
        candidates.append({
            "citation": event.get("citation"),
            "timestamp_seconds": event.get("timestamp_seconds"),
            "frame_sha256": event.get("frame_sha256"),
            "supporting_passage": passage,
            "visible_text": visible,
            "scene_description": description,
            "notable_visual_claims": claims,
            "status": "VISUAL_OBSERVATION_UNVERIFIED",
            "requires_external_verification": bool(claims),
        })
    return candidates

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
    for visual in manifest.get("visual_evidence_candidates") or []:
        passage = str(visual.get("supporting_passage") or "").strip()
        if not passage:
            continue
        item = store.add_evidence(
            run_id, source_id=source["source_id"], supporting_passage=passage,
            observation_type="UNKNOWN",
            structured_fact={
                "media_citation": visual.get("citation"),
                "timestamp_seconds": visual.get("timestamp_seconds"),
                "frame_sha256": visual.get("frame_sha256"),
                "visible_text": visual.get("visible_text"),
                "scene_description": visual.get("scene_description"),
                "notable_visual_claims": visual.get("notable_visual_claims"),
                "verification_status": "VISUAL_OBSERVATION_UNVERIFIED",
            },
            extraction_method="multimodal_keyframe_analysis",
        )
        evidence.append(item)
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
                 language: str | None = None, config: Config | None = None,
                 analyze_visuals: bool = False, vision_model: str = "gpt-5.6-luna") -> dict[str, Any]:
    """Return a bounded timestamped transcript manifest for a public media URL."""
    cfg = config or Config()
    selected = _select_provider(provider, cfg)
    with TemporaryDirectory(prefix="ahmed-media-") as tmp:
        root = Path(tmp)
        downloaded = download_audio(source_url, root)
        duration = _require_duration_within_budget(downloaded)
        raw_frames: list[dict[str, Any]] = []
        frames: list[dict[str, Any]] = []
        visual_events: list[dict[str, Any]] = []
        if analyze_visuals:
            visual_source = download_video_for_frames(source_url, root)
            raw_frames = _extract_keyframes(visual_source, root, interval_seconds=10)
            frames = adaptive_sample_frames(raw_frames)
            visual_events = analyze_visual_frames(frames, config=cfg, model=vision_model)
        compressed = compress_audio(downloaded, root)
        chunks = chunk_audio(compressed, root, CHUNK_SECONDS)
        segments = []
        segment_index = 0
        chunk_offset = 0.0
        for chunk in chunks:
            timed = transcribe_chunk_timed(chunk, selected, config=cfg)
            for part in timed:
                transcript = str(part.get("text") or "").strip()
                if not transcript:
                    continue
                start = min(chunk_offset + float(part.get("start") or 0.0), duration)
                end = min(chunk_offset + float(part.get("end") or 0.0), duration)
                segments.append(MediaSegment(segment_index, start, max(start, end), transcript,
                    hashlib.sha256(transcript.encode("utf-8")).hexdigest()))
                segment_index += 1
            chunk_offset += _probe_audio_duration(chunk)
    return {
        "source_url": source_url,
        "media_type": "video_or_audio",
        "duration_seconds": duration,
        "provider": selected,
        "language_hint": language,
        "segments": [{**asdict(s), "citation": s.citation} for s in segments],
        "semantic_chunks": semantic_chunk_segments([{**asdict(s), "citation": s.citation} for s in segments]),
        "sampling": {"strategy": "adaptive_visual_change", "raw_frame_count": len(raw_frames), "selected_frame_count": len(frames)},
        "visual_frames": [{k: v for k, v in frame.items() if k != "path"} for frame in frames],
        "visual_events": visual_events,
        "visual_evidence_candidates": visual_evidence_candidates(visual_events),
        "full_transcript": "\n\n".join(f"[{s.citation}] {s.transcript}" for s in segments if s.transcript),
        "provenance": {
            "retrieval_tool": "yt-dlp", "audio_processing": "ffmpeg",
            "visual_acquisition": "yt-dlp bounded video <=720p" if analyze_visuals else "disabled",
            "transcription_provider": selected, "timestamp_basis": "provider segment timestamps with chunk offsets",
        },
        "timeline": build_unified_timeline([{**asdict(s), "citation": s.citation} for s in segments], visual_events),
        "claim_candidates": extract_claim_candidates({"source_url": source_url, "segments": [{**asdict(s), "citation": s.citation} for s in segments]}),
        "limitations": [
            "timestamps are provider-segment-level when supported, with chunk fallback",
            "speaker diarization is not performed",
            "visual analysis is optional and requires a configured OpenAI multimodal model",
            "transcript statements are source evidence, not independently verified facts",
        ],
    }


def build_av_evidence_timeline(speech_evidence: dict[str, Any], visual_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge timestamped speech evidence and visual OCR into one bounded timeline."""
    events: list[dict[str, Any]] = []
    for item in speech_evidence.get("evidence", []):
        events.append({
            "kind": "speech",
            "timestamp_seconds": float(item.get("start_seconds", 0.0)),
            "end_seconds": float(item.get("end_seconds", 0.0)),
            "citation": item.get("citation"),
            "text": item.get("text", ""),
            "confidence": item.get("confidence"),
        })
    events.extend(visual_events)
    events.sort(key=lambda x: (float(x.get("timestamp_seconds", 0.0)), x.get("kind", "")))
    return events
