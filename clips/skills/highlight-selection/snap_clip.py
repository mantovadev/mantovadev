#!/usr/bin/env python3
"""snap_clip.py: turn agent-picked highlight candidates into snapped cut points.

Part of the Mantova Dev clipping pipeline. See clips/PLANNING.md and this
folder's SKILL.md. Standard library only, no third-party imports.

Subcommands:
  snap    --event <YYYY-MM-DD> [--refresh-silence] [--highlights <path>] [--out-dir <dir>]
          Validate candidates in highlights.json, compute cut.{start,end,duration,
          start_snapped,end_snapped,flags} for each, write highlights.json back,
          regenerate candidates.md.
  choose  --event <YYYY-MM-DD> [--ids 1,3] [--shift-start ID=SECONDS ...]
          [--shift-end ID=SECONDS ...] [--highlights <path>] [--out-dir <dir>]
          Mark the given candidate ids as chosen (all others unchosen; omit --ids to
          leave the flags alone), apply manual shifts (relative to the snapped cut,
          kept per edge until replaced), recompute duration/flags, regenerate
          candidates.md.
          --start ID=TIME / --end ID=TIME set a cut edge from a time read off the
          preview file's player (seconds or M:SS). Changed candidates that already
          have a preview get it re-rendered.
  preview --event <YYYY-MM-DD> [--ids 1,3] [--pad 5] [--highlights <path>] [--out-dir <dir>]
          Render a small review file per candidate (480p, sentence captions burned
          in, a few seconds of padding before and after the cut, labelled
          "OUTSIDE CUT") into <out-dir>/previews/.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REQUIRED_TOP_KEYS = {"event": str, "source_transcript": str, "candidates": list}
REQUIRED_CANDIDATE_KEYS = {
    "id": int,
    "start_seg": int,
    "end_seg": int,
    "start_quote": str,
    "end_quote": str,
    "score": (int, float),
    "hook": str,
    "reason": str,
    "needs_visuals": bool,
}

TOO_SHORT_S = 15.0
TOO_LONG_S = 60.0
START_WINDOW = 1.0   # look for silence end within S-1.0 .. S+1.0
END_WINDOW_PAD = 0.15  # look for silence start within Lw+0.15 .. E+1.0
END_WINDOW_TAIL = 1.0
MAX_LEAD = 0.35
MAX_TAIL = 0.45
PREVIEW_PAD = 5.0
PREVIEW_CAPTION_WORDS = 10


class UsageError(Exception):
    """Raised for bad input; caller prints message and exits 1."""


def repo_root() -> Path:
    # this file lives at <repo>/clips/skills/highlight-selection/snap_clip.py
    return Path(__file__).resolve().parents[3]


def fail(msg: str):
    print(f"snap_clip.py: error: {msg}", file=sys.stderr)
    sys.exit(1)


def load_json(path: Path, what: str):
    if not path.is_file():
        fail(f"{what} not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        fail(f"{what} is not valid JSON ({path}): {e}")


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def validate_highlights(data, path: Path):
    if not isinstance(data, dict):
        fail(f"{path}: top level must be a JSON object")
    for key, typ in REQUIRED_TOP_KEYS.items():
        if key not in data:
            fail(f"{path}: missing required top-level key '{key}'")
        if not isinstance(data[key], typ):
            fail(f"{path}: '{key}' must be a {typ.__name__}, got {type(data[key]).__name__}")
    for i, cand in enumerate(data["candidates"]):
        if not isinstance(cand, dict):
            fail(f"{path}: candidates[{i}] must be an object")
        for key, typ in REQUIRED_CANDIDATE_KEYS.items():
            if key not in cand:
                fail(f"{path}: candidates[{i}] missing required key '{key}'")
            if not isinstance(cand[key], typ) or isinstance(cand[key], bool) and typ is not bool:
                fail(
                    f"{path}: candidates[{i}].{key} must be {typ}, "
                    f"got {type(cand[key]).__name__}"
                )
        if cand["id"] < 1:
            fail(f"{path}: candidates[{i}].id must be >= 1, got {cand['id']}")


def validate_transcript(data, path: Path):
    if not isinstance(data, dict) or "segments" not in data or "words" not in data:
        fail(f"{path}: not a valid transcript.json (missing 'segments' or 'words')")


def segments_by_index(transcript):
    return {seg["i"]: seg for seg in transcript["segments"]}


def words_for_segment(transcript, seg_i):
    return [w for w in transcript["words"] if w["seg"] == seg_i]


def ffprobe_duration(audio_path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio_path)],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError:
        fail("ffprobe not found on PATH. Install with: brew install ffmpeg-full")
    except subprocess.CalledProcessError as e:
        fail(f"ffprobe failed on {audio_path}: {e.stderr.strip()}")
    try:
        return float(out.stdout.strip())
    except ValueError:
        fail(f"ffprobe returned an unparseable duration for {audio_path}: {out.stdout!r}")


SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silences(audio_path: Path):
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats", "-i", str(audio_path),
                "-af", "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-",
            ],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        fail("ffmpeg not found on PATH. Install with: brew install ffmpeg-full")
    intervals = []
    pending_start = None
    for line in proc.stderr.splitlines():
        m = SILENCE_START_RE.search(line)
        if m:
            pending_start = float(m.group(1))
            continue
        m = SILENCE_END_RE.search(line)
        if m and pending_start is not None:
            intervals.append({"start": pending_start, "end": float(m.group(1))})
            pending_start = None
    return sorted(intervals, key=lambda iv: iv["start"])


def load_or_build_silences(work_dir: Path, audio_path: Path, refresh: bool):
    cache_path = work_dir / "silences.json"
    if not refresh and cache_path.is_file():
        cached = load_json(cache_path, "silences cache")
        return cached, cache_path
    if not audio_path.is_file():
        fail(f"audio file not found: {audio_path}")
    print(f"Detecting silences in {audio_path} (this scans the whole file)...", file=sys.stderr)
    intervals = detect_silences(audio_path)
    duration = ffprobe_duration(audio_path)
    cached = {"audio": str(audio_path), "duration": duration, "intervals": intervals}
    save_json(cache_path, cached)
    print(f"  wrote {cache_path} ({len(intervals)} silence intervals)", file=sys.stderr)
    return cached, cache_path


def compute_cut(cand, segments, transcript, silence_cache):
    intervals = silence_cache["intervals"]
    duration_total = silence_cache["duration"]
    flags = []

    start_seg = cand["start_seg"]
    end_seg = cand["end_seg"]

    if start_seg not in segments or end_seg not in segments:
        fail(
            f"candidate {cand['id']}: start_seg/end_seg out of range "
            f"(start_seg={start_seg}, end_seg={end_seg}, valid range 0..{len(segments) - 1})"
        )
    if start_seg > end_seg:
        fail(f"candidate {cand['id']}: start_seg ({start_seg}) is after end_seg ({end_seg})")

    if normalize(cand["start_quote"]) != normalize(segments[start_seg]["text"]):
        flags.append("quote_mismatch")
    elif normalize(cand["end_quote"]) != normalize(segments[end_seg]["text"]):
        flags.append("quote_mismatch")

    S = segments[start_seg]["s"]
    E = segments[end_seg]["e"]
    last_seg_words = sorted(words_for_segment(transcript, end_seg), key=lambda w: w["s"])
    Lw = last_seg_words[-1]["s"] if last_seg_words else E

    # START: find a silence interval whose END (speech onset) is near S.
    start_candidates = [iv for iv in intervals if (S - START_WINDOW) <= iv["end"] <= (S + START_WINDOW)]
    if start_candidates:
        iv = min(start_candidates, key=lambda iv: abs(iv["end"] - S))
        silence_len = iv["end"] - iv["start"]
        lead = min(MAX_LEAD, silence_len / 2)
        start = iv["end"] - lead
        start_snapped = True
    else:
        start = max(0.0, S - 0.2)
        start_snapped = False

    # END: find the earliest silence interval whose START is within Lw+0.15 .. E+1.0.
    end_candidates = sorted(
        (iv for iv in intervals if (Lw + END_WINDOW_PAD) <= iv["start"] <= (E + END_WINDOW_TAIL)),
        key=lambda iv: iv["start"],
    )
    if end_candidates:
        iv = end_candidates[0]
        silence_len = iv["end"] - iv["start"]
        tail = min(MAX_TAIL, silence_len / 2)
        end = iv["start"] + tail
        end_snapped = True
    else:
        end = E + 0.2
        end_snapped = False

    start = max(0.0, min(start, duration_total))
    end = max(0.0, min(end, duration_total))

    dur = round(end - start, 2)
    if dur < TOO_SHORT_S:
        flags.append("too_short")
    if dur > TOO_LONG_S:
        flags.append("too_long")

    return {
        "segs": [start_seg, end_seg],
        "start": round(start, 2),
        "end": round(end, 2),
        "duration": dur,
        "start_snapped": start_snapped,
        "end_snapped": end_snapped,
        "flags": flags,
    }


def refresh_duration(cut, duration_total=None):
    """Clamp the cut, recompute its duration and the too_short / too_long flags."""
    cut["start"] = max(0.0, cut["start"])
    if duration_total is not None:
        cut["end"] = min(cut["end"], duration_total)
    cut["duration"] = round(cut["end"] - cut["start"], 2)
    cut["flags"] = [f for f in cut["flags"] if f not in ("too_short", "too_long")]
    if cut["duration"] < TOO_SHORT_S:
        cut["flags"].append("too_short")
    if cut["duration"] > TOO_LONG_S:
        cut["flags"].append("too_long")


def apply_overlap_flags(candidates):
    # Strip any previous overlap flags, then recompute from current cut times.
    for c in candidates:
        if "cut" in c:
            c["cut"]["flags"] = [f for f in c["cut"]["flags"] if not f.startswith("overlaps_candidate_")]
    with_cut = [c for c in candidates if "cut" in c]
    for i, a in enumerate(with_cut):
        for b in with_cut[i + 1:]:
            a_s, a_e = a["cut"]["start"], a["cut"]["end"]
            b_s, b_e = b["cut"]["start"], b["cut"]["end"]
            if a_s < b_e and b_s < a_e:
                a["cut"]["flags"].append(f"overlaps_candidate_{b['id']}")
                b["cut"]["flags"].append(f"overlaps_candidate_{a['id']}")


def mmss(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def clock(seconds: float) -> str:
    """M:SS.s, used for times read off the preview player."""
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def parse_clock(text: str) -> float:
    """Accept plain seconds ("63.5") or M:SS(.s) ("1:03.5")."""
    parts = text.strip().split(":")
    if len(parts) > 2:
        raise ValueError(text)
    value = float(parts[-1])
    if len(parts) == 2:
        value += int(parts[0]) * 60
    if value < 0:
        raise ValueError(text)
    return value


def srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def build_preview_srt(cut, transcript, origin: float, total: float) -> str:
    """Sentence captions (segment timing) plus OUTSIDE CUT labels, in preview time."""
    events = []
    clip_in = cut["start"] - origin
    clip_out = cut["end"] - origin
    if clip_in > 0.05:
        events.append((0.0, clip_in, "{\\an8}OUTSIDE CUT (clip starts at %s)" % clock(clip_in)))
    if total - clip_out > 0.05:
        events.append((clip_out, total, "{\\an8}OUTSIDE CUT (clip ended at %s)" % clock(clip_out)))

    segs = sorted(transcript["segments"], key=lambda g: g["s"])
    for idx, seg in enumerate(segs):
        seg_s = seg["s"]
        seg_e = seg["e"]
        if idx + 1 < len(segs):
            seg_e = min(seg_e, segs[idx + 1]["s"])
        if seg_e <= origin or seg_s >= origin + total or seg_e <= seg_s:
            continue
        words = seg["text"].split()
        if not words:
            continue
        chunks = [words[i:i + PREVIEW_CAPTION_WORDS] for i in range(0, len(words), PREVIEW_CAPTION_WORDS)]
        t = seg_s
        for chunk in chunks:
            span = (seg_e - seg_s) * len(chunk) / len(words)
            a, b = t - origin, t + span - origin
            t += span
            a, b = max(0.0, a), min(total, b)
            if b - a > 0.05:
                events.append((a, b, " ".join(chunk)))

    events.sort(key=lambda ev: ev[0])
    blocks = [f"{n}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n" for n, (a, b, text) in enumerate(events, 1)]
    return "\n".join(blocks)


def render_preview(cand, transcript, work_dir: Path, out_dir: Path, pad: float, duration_total):
    cut = cand["cut"]
    source = work_dir / "source.mp4"
    if not source.exists():
        fail(f"source video not found: {source}. Run ingest.sh first.")
    origin = max(0.0, cut["start"] - pad)
    end = cut["end"] + pad
    if duration_total is not None:
        end = min(end, duration_total)
    total = round(end - origin, 2)

    previews_dir = out_dir / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    name = f"candidate_{cand['id']}"
    (previews_dir / f"{name}.srt").write_text(build_preview_srt(cut, transcript, origin, total), encoding="utf-8")

    # Run inside previews_dir so the subtitles filter gets a bare file name (no escaping).
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{origin:.2f}", "-t", f"{total:.2f}", "-i", str(source.resolve()),
        "-vf", f"scale=-2:480,subtitles={name}.srt:force_style='FontSize=20,Outline=2,MarginV=24'",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", f"{name}.mp4",
    ]
    print(f"Rendering preview for candidate {cand['id']} ({total}s)...", file=sys.stderr)
    try:
        proc = subprocess.run(cmd, cwd=previews_dir, capture_output=True, text=True)
    except FileNotFoundError:
        fail("ffmpeg not found on PATH. Install with: brew install ffmpeg-full")
    if proc.returncode != 0:
        fail(f"ffmpeg failed rendering preview for candidate {cand['id']}: {proc.stderr.strip()}")
    cut["preview"] = {"file": f"previews/{name}.mp4", "origin": round(origin, 2), "pad": pad}
    print(f"  wrote {previews_dir / (name + '.mp4')}", file=sys.stderr)


def render_candidates_md(highlights, event, transcript, out_path: Path):
    segments = segments_by_index(transcript)
    lines = [f"# Highlight candidates: {event}", ""]
    for cand in sorted(highlights["candidates"], key=lambda c: c["id"]):
        cut = cand.get("cut")
        chosen = cand.get("chosen", False)
        if cut:
            time_range = f"{mmss(cut['start'])} to {mmss(cut['end'])} ({cut['duration']}s)"
            flags = ", ".join(cut["flags"]) if cut["flags"] else "none"
            edges = "start {}, end {}".format(
                "on a pause" if cut["start_snapped"] else "NOT on a pause (check the preview)",
                "on a pause" if cut["end_snapped"] else "NOT on a pause (check the preview)",
            )
            if cut.get("manual_shift"):
                edges += ", manual shift {}".format(
                    ", ".join(f"{k} {v:+g}s" for k, v in cut["manual_shift"].items())
                )
        else:
            time_range = "not snapped yet"
            flags = "none"
            edges = "n/a"
        marker = " [CHOSEN]" if chosen else ""
        lines.append(f"## Candidate {cand['id']}: score {cand['score']}{marker}")
        lines.append("")
        lines.append(f"- Range: segments {cand['start_seg']} to {cand['end_seg']}, {time_range}")
        lines.append(f"- Flags: {flags}")
        lines.append(f"- Cut edges: {edges}")
        lines.append(f"- Needs visuals: {'yes' if cand['needs_visuals'] else 'no'}")
        lines.append("")
        lines.append(f"**Hook:** {cand['hook']}")
        lines.append("")
        lines.append(f"**Reason:** {cand['reason']}")
        lines.append("")
        text = " ".join(
            segments[i]["text"] for i in range(cand["start_seg"], cand["end_seg"] + 1) if i in segments
        )
        lines.append("**Transcript:**")
        lines.append("")
        lines.append(f"> {text}")
        lines.append("")
        if cut and cut.get("preview"):
            pv = cut["preview"]
            clip_in = cut["start"] - pv["origin"]
            clip_out = cut["end"] - pv["origin"]
            lines.append(
                f"**Preview:** `{pv['file']}` (in the player the clip runs from "
                f"{clock(clip_in)} to {clock(clip_out)}; the rest is padding, labelled OUTSIDE CUT)"
            )
        elif cut:
            lines.append(f"**Preview:** not rendered yet, run `snap_clip.py preview --event {event}`")
        lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"  wrote {out_path}", file=sys.stderr)


def resolve_paths(args, root: Path):
    event = args.event
    work_dir = root / "clips" / "work" / event
    highlights_path = Path(args.highlights).resolve() if args.highlights else work_dir / "highlights.json"
    transcript_path = work_dir / "transcript.json"
    out_dir = Path(args.out_dir).resolve() if args.out_dir else work_dir
    return highlights_path, transcript_path, work_dir, out_dir


def cmd_snap(args, root: Path):
    highlights_path, transcript_path, work_dir, out_dir = resolve_paths(args, root)

    highlights = load_json(highlights_path, "highlights.json")
    validate_highlights(highlights, highlights_path)
    transcript = load_json(transcript_path, "transcript.json")
    validate_transcript(transcript, transcript_path)
    segments = segments_by_index(transcript)

    audio_path = work_dir / "audio.wav"
    silence_cache, cache_path = load_or_build_silences(work_dir, audio_path, args.refresh_silence)

    for cand in highlights["candidates"]:
        old = cand.get("cut") or {}
        cut = compute_cut(cand, segments, transcript, silence_cache)
        # Same segment span as before: keep the human's manual adjustments, and the
        # preview if the cut did not move. A changed span starts from scratch.
        if old.get("segs") == cut["segs"]:
            manual = old.get("manual_shift") or {}
            for edge, shift in manual.items():
                cut[edge] = round(cut[edge] + shift, 2)
            if manual:
                cut["manual_shift"] = manual
                refresh_duration(cut, silence_cache["duration"])
            if old.get("preview") and (old["start"], old["end"]) == (cut["start"], cut["end"]):
                cut["preview"] = old["preview"]
        cand["cut"] = cut
        cand.setdefault("chosen", False)

    apply_overlap_flags(highlights["candidates"])

    save_json(highlights_path, highlights)
    print(f"Wrote {highlights_path}", file=sys.stderr)

    candidates_md = out_dir / "candidates.md"
    render_candidates_md(highlights, args.event, transcript, candidates_md)

    for cand in sorted(highlights["candidates"], key=lambda c: c["id"]):
        cut = cand["cut"]
        flag_str = f" flags={cut['flags']}" if cut["flags"] else ""
        print(
            f"  candidate {cand['id']}: {cut['start']:.2f}s -> {cut['end']:.2f}s "
            f"({cut['duration']}s, start_snapped={cut['start_snapped']}, "
            f"end_snapped={cut['end_snapped']}){flag_str}"
        )


def parse_shift_args(pairs, clock_values=False):
    shifts = {}
    for pair in pairs or []:
        if "=" not in pair:
            fail(f"invalid shift argument (expected ID=SECONDS): {pair}")
        id_str, sec_str = pair.split("=", 1)
        try:
            cid = int(id_str)
            sec = parse_clock(sec_str) if clock_values else float(sec_str)
        except ValueError:
            fail(f"invalid shift argument (expected ID=SECONDS): {pair}")
        shifts[cid] = sec
    return shifts


def cmd_choose(args, root: Path):
    highlights_path, transcript_path, work_dir, out_dir = resolve_paths(args, root)

    highlights = load_json(highlights_path, "highlights.json")
    validate_highlights(highlights, highlights_path)
    transcript = load_json(transcript_path, "transcript.json")
    validate_transcript(transcript, transcript_path)

    chosen_ids = None  # None = leave the chosen flags as they are
    if args.ids is not None:
        try:
            chosen_ids = {int(x) for x in args.ids.split(",") if x.strip()}
        except ValueError:
            fail(f"--ids must be a comma-separated list of integers, got: {args.ids}")

    known_ids = {c["id"] for c in highlights["candidates"]}
    unknown = (chosen_ids or set()) - known_ids
    if unknown:
        fail(f"--ids references unknown candidate id(s): {sorted(unknown)} (known: {sorted(known_ids)})")

    shift_start = parse_shift_args(args.shift_start)
    shift_end = parse_shift_args(args.shift_end)
    at_start = parse_shift_args(args.start, clock_values=True)
    at_end = parse_shift_args(args.end, clock_values=True)
    for cid in list(shift_start) + list(shift_end) + list(at_start) + list(at_end):
        if cid not in known_ids:
            fail(f"shift references unknown candidate id: {cid}")
    for cid in set(shift_start) & set(at_start):
        fail(f"candidate {cid}: use either --shift-start or --start, not both")
    for cid in set(shift_end) & set(at_end):
        fail(f"candidate {cid}: use either --shift-end or --end, not both")

    audio_path = work_dir / "audio.wav"
    duration_total = ffprobe_duration(audio_path) if audio_path.is_file() else None

    for cand in highlights["candidates"]:
        if chosen_ids is not None:
            cand["chosen"] = cand["id"] in chosen_ids
        cut = cand.get("cut")
        if cut is None:
            continue
        # A shift is always relative to the snapped cut. An edge named in this call has
        # its earlier shift undone and replaced; edges not named keep what they had, so
        # candidates can be adjusted one at a time without losing earlier adjustments.
        before = (cut["start"], cut["end"])
        manual = dict(cut.pop("manual_shift", {}))
        touched = False
        for edge, given, shifts in (("start", at_start, shift_start), ("end", at_end, shift_end)):
            if cand["id"] not in given and cand["id"] not in shifts:
                continue
            touched = True
            snapped = round(cut[edge] - manual.pop(edge, 0.0), 2)
            if cand["id"] in given:
                # a time read off the preview player: turn it into a shift
                if not cut.get("preview"):
                    fail(f"candidate {cand['id']}: --{edge} needs a rendered preview; run the preview subcommand first")
                shift = round(cut["preview"]["origin"] + given[cand["id"]] - snapped, 2)
            else:
                shift = shifts[cand["id"]]
            cut[edge] = round(snapped + shift, 2)
            if shift:
                manual[edge] = shift
        if touched:
            refresh_duration(cut, duration_total)
        if manual:
            cut["manual_shift"] = manual
        if cut["end"] <= cut["start"]:
            fail(f"candidate {cand['id']}: cut end ({cut['end']}) is not after cut start ({cut['start']})")
        if cut.get("preview") and (cut["start"], cut["end"]) != before:
            render_preview(cand, transcript, work_dir, out_dir, cut["preview"]["pad"], duration_total)

    apply_overlap_flags(highlights["candidates"])

    save_json(highlights_path, highlights)
    print(f"Wrote {highlights_path}", file=sys.stderr)

    candidates_md = out_dir / "candidates.md"
    render_candidates_md(highlights, args.event, transcript, candidates_md)

    for cand in sorted(highlights["candidates"], key=lambda c: c["id"]):
        marker = "CHOSEN" if cand["chosen"] else "-"
        print(f"  candidate {cand['id']}: {marker}")


def cmd_preview(args, root: Path):
    highlights_path, transcript_path, work_dir, out_dir = resolve_paths(args, root)
    highlights = load_json(highlights_path, "highlights.json")
    validate_highlights(highlights, highlights_path)
    transcript = load_json(transcript_path, "transcript.json")
    validate_transcript(transcript, transcript_path)

    wanted = None
    if args.ids:
        try:
            wanted = {int(x) for x in args.ids.split(",") if x.strip()}
        except ValueError:
            fail(f"--ids must be a comma-separated list of integers, got: {args.ids}")
        unknown = wanted - {c["id"] for c in highlights["candidates"]}
        if unknown:
            fail(f"--ids references unknown candidate id(s): {sorted(unknown)}")

    audio_path = work_dir / "audio.wav"
    duration_total = ffprobe_duration(audio_path) if audio_path.is_file() else None

    for cand in sorted(highlights["candidates"], key=lambda c: c["id"]):
        if wanted is not None and cand["id"] not in wanted:
            continue
        if "cut" not in cand:
            fail(f"candidate {cand['id']} has no cut yet; run the snap subcommand first")
        render_preview(cand, transcript, work_dir, out_dir, args.pad, duration_total)

    save_json(highlights_path, highlights)
    render_candidates_md(highlights, args.event, transcript, out_dir / "candidates.md")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="snap_clip.py",
        description="Validate highlight candidates, snap cut points to silence, and generate candidates.md.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snap", help="Compute cut points for all candidates in highlights.json.")
    p_snap.add_argument("--event", required=True, help="Event date, YYYY-MM-DD.")
    p_snap.add_argument("--refresh-silence", action="store_true", help="Ignore the cached silences.json and re-run silencedetect.")
    p_snap.add_argument("--highlights", help="Override path to highlights.json (default: clips/work/<event>/highlights.json).")
    p_snap.add_argument("--out-dir", help="Override directory for candidates.md (default: clips/work/<event>/).")
    p_snap.set_defaults(func=cmd_snap)

    p_choose = sub.add_parser("choose", help="Mark candidates as chosen and optionally shift their cut points.")
    p_choose.add_argument("--event", required=True, help="Event date, YYYY-MM-DD.")
    p_choose.add_argument("--ids", help="Comma-separated candidate ids to mark chosen, e.g. 1,3 (all others become unchosen). Omit to leave the chosen flags unchanged while adjusting cuts.")
    p_choose.add_argument("--shift-start", action="append", metavar="ID=SECONDS", help="Shift a candidate's cut.start by SECONDS (negative = earlier). Repeatable.")
    p_choose.add_argument("--shift-end", action="append", metavar="ID=SECONDS", help="Shift a candidate's cut.end by SECONDS (negative = earlier). Repeatable.")
    p_choose.add_argument("--start", action="append", metavar="ID=TIME", help="Set a candidate's cut start from a time read off its preview file (seconds or M:SS). Repeatable.")
    p_choose.add_argument("--end", action="append", metavar="ID=TIME", help="Set a candidate's cut end from a time read off its preview file (seconds or M:SS). Repeatable.")
    p_choose.add_argument("--highlights", help="Override path to highlights.json (default: clips/work/<event>/highlights.json).")
    p_choose.add_argument("--out-dir", help="Override directory for candidates.md (default: clips/work/<event>/).")
    p_choose.set_defaults(func=cmd_choose)

    p_prev = sub.add_parser("preview", help="Render a captioned, padded review file per candidate.")
    p_prev.add_argument("--event", required=True, help="Event date, YYYY-MM-DD.")
    p_prev.add_argument("--ids", help="Comma-separated candidate ids (default: all).")
    p_prev.add_argument("--pad", type=float, default=PREVIEW_PAD, help=f"Seconds of padding before and after the cut (default: {PREVIEW_PAD}).")
    p_prev.add_argument("--highlights", help="Override path to highlights.json (default: clips/work/<event>/highlights.json).")
    p_prev.add_argument("--out-dir", help="Override output directory (default: clips/work/<event>/).")
    p_prev.set_defaults(func=cmd_preview)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    event = getattr(args, "event", None)
    if event is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", event):
        fail(f"--event must match YYYY-MM-DD, got: {event}")

    root = repo_root()
    args.func(args, root)


if __name__ == "__main__":
    main()
