#!/usr/bin/env python3
"""clips.py: Mantova Dev clipping pipeline, one command-line tool.

Standard library only. Runs on Python 3.9. Needs ffmpeg (with libass) and
whisper-cli on PATH. See clips/README.md for the overview and the SKILL.md files
in clips/skills/ for how and when to run each step. Everything a run produces goes
in clips/work/<event>/ (gitignored).

Subcommands:
  ingest      normalize a source recording into source.mp4 + audio.wav
  transcribe  run whisper.cpp and derive a compact transcript.json
  render      print transcript.json as numbered, mm:ss-stamped lines
  snap        compute cut points for highlight candidates
  preview     render a small captioned review file per candidate
  choose      mark candidates chosen and adjust their cut points
  status      print one block per candidate: id, score, chosen, range, ...
  cut         cut every chosen candidate from source.mp4
  align       transcribe a cut clip on its own for word-level timing
  tighten     shorten the pauses inside a cut clip
  burn       build the .ass and render the final vertical clip

Run `clips.py <subcommand> --help` for each subcommand's options.
"""

import argparse
import json
import math
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import NoReturn, Optional

# ---------------------------------------------------------------- tuning

# transcribe: end-time clamp. Whisper word end times absorb the silence that
# follows the word, so a raw end can lie far past the spoken word. Each
# word's end is clamped to the earliest of: its own raw end, the next word's
# start, and its own start + 1.5s.
WORD_END_CLAMP_MS = 1500.0

# snap: candidate duration thresholds and silence-snap windows.
TOO_SHORT_S = 15.0
TOO_LONG_S = 60.0       # the finished clip
RAW_TOO_LONG_S = 120.0  # the cut itself may run longer, if tighten then drops stretches from it
START_WINDOW = 1.0     # look for silence end within S-1.0 .. S+1.0
END_WINDOW_PAD = 0.15  # look for silence start within Lw+0.15 .. E+1.0
END_WINDOW_TAIL = 1.0
MAX_LEAD = 0.35
MAX_TAIL = 0.45
# What counts as a pause (ffmpeg silencedetect). The right noise floor depends on the
# room: raise it (e.g. -30dB) for a noisy room, lower it for a quiet one.
SILENCE_NOISE = "-35dB"
SILENCE_MIN_S = 0.4
PREVIEW_PAD = 5.0
PHRASE_SEARCH_S = 30.0  # --start-at / --end-after look this far around the current cut
PREVIEW_CAPTION_WORDS = 10

# align: timing. DTW token times tend to land a little after the
# audible word onset, so they are pulled earlier by DTW_LEAD. The first word
# of each whisper segment uses the segment start instead, which sits right on
# the speech onset after a pause.
DTW_LEAD = 0.12
MIN_WORD = 0.08          # minimum spacing between consecutive word starts
LAST_WORD_HOLD = 0.6     # how long the final word of a chunk stays if nothing follows

# burn: chunking. A chunk is what is on screen at once.
MAX_WORDS = 3
MAX_CHARS = 14          # upper case Aeonik Black at size 100: 15 chars fill about 80% of the width
GAP_BREAK = 0.45        # a pause this long always starts a new chunk
BREAK_AFTER = ".?!,;:"  # punctuation that ends a chunk
STRIP_PUNCT = ".,;:"    # punctuation not shown on screen ("?" and "!" stay)

# tighten: pauses inside a cut clip are shortened, never removed whole. A pause is a
# quiet run found by silencedetect on the clip itself, at the same noise floor as snap.
TIGHT_DETECT_S = 0.15       # shortest quiet run silencedetect reports for tighten
TIGHT_MERGE_S = 0.03        # quiet runs split by a click shorter than this count as one
# Only long pauses are touched, and they keep about half a second: short pauses are
# speech rhythm, and a speaker without them sounds rushed.
TIGHT_MIN_PAUSE = 0.7       # shorter pauses are speech rhythm and stay as they are
TIGHT_GAP = 0.45            # what a pause is shortened to inside a sentence
TIGHT_GAP_SENTENCE = 0.6    # ... and after a word ending in . ? !
TIGHT_HEAD = 0.6            # share of the kept gap left right after the previous word
                            # (consonant release, room tail); the rest leads into the next word
TIGHT_MIN_CUT = 0.10        # do not bother splicing for less than this
TIGHT_FADE = 0.025          # audio fade on each side of a splice: hides clicks, softens the room tone jump
# tighten --drop: each edge of a dropped stretch moves onto the pause nearest to the start
# of the word after that edge, searched this far before and after it.
DROP_BACK = 0.6
DROP_AHEAD = 0.3
# Where the room is noisy (Q&A, audience) a pause does not get below SILENCE_NOISE. A drop
# edge that finds no pause tries again at these louder floors.
DROP_NOISE_LADDER = ("-30dB", "-25dB")
DROP_SEP = " ... "          # --drop "ID=first words ... last words"

CANVAS_W, CANVAS_H = 1080, 1920   # output is always vertical
LOGO_W = 640             # logo width and top offset on the canvas
LOGO_Y = 150

# Aeonik Pro is the Mantova Dev brand font. It is proprietary, so it is not in the repo:
# it must be installed on the machine. If it is missing, libass silently falls back to
# a default font; pass --font to pick another one.
DEFAULT_STYLE = {
    "font": "Aeonik Pro Black",
    "footer_font": "Aeonik Pro Bold",
    "footer_size": 56,
    "footer_margin_v": 140,
    "background": "041917",      # canvas colour (brand dark), RRGGBB
    "size": 100,                 # caption font size on the 1080x1920 canvas
    "primary": "&H00FFFFFF",     # ASS colours are &HAABBGGRR
    "active": "&H00CFE003",      # highlighted word: Mantova Dev turquoise #03E0CF
    "outline_colour": "&H00000000",
    "outline": 4,
    "shadow": 2,
    "active_scale": 108,         # percent
    "uppercase": True,
}

EVENT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

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


# ---------------------------------------------------------------- shared helpers

def fail(msg: str) -> NoReturn:
    """Print a one-line error and exit 1. NoReturn tells type checkers that nothing after
    a fail() call runs, so values set before it are not seen as possibly missing."""
    print(f"clips.py: error: {msg}", file=sys.stderr)
    sys.exit(1)


def repo_root() -> Path:
    # this file lives at <repo>/clips/clips.py
    return Path(__file__).resolve().parents[1]


def run(cmd, **kwargs):
    """Print the command, then run it. Fails cleanly if the binary is missing."""
    print("Running: " + " ".join(str(c) for c in cmd), file=sys.stderr)
    try:
        return subprocess.run(cmd, **kwargs)
    except FileNotFoundError:
        fail(f"{cmd[0]} not found on PATH")


def validate_event(event):
    if not event or not EVENT_RE.match(event):
        fail(f"--event must match YYYY-MM-DD, got: {event}")


def work_dir_for(root: Path, event: str, override: Optional[str]) -> Path:
    if override:
        return Path(override).resolve()
    return root / "clips" / "work" / event


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


def ffprobe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError:
        fail("ffprobe not found on PATH. Install with: brew install ffmpeg-full")
    except subprocess.CalledProcessError as e:
        fail(f"ffprobe failed on {path}: {e.stderr.strip()}")
    try:
        return float(out.stdout.strip())
    except ValueError:
        fail(f"ffprobe returned an unparseable duration for {path}: {out.stdout!r}")


def ffprobe_size(path: Path):
    proc = run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        width, height = (int(x) for x in proc.stdout.strip().split(",")[:2])
        return width, height
    except ValueError:
        fail(f"could not read the video size of {path}")


def ffprobe_fps(path: Path) -> str:
    """Frame rate of the first video stream as ffmpeg prints it, e.g. "30/1" or "30000/1001"."""
    proc = run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=r_frame_rate", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    fps = proc.stdout.strip().split(",")[0]
    if not re.match(r"^\d+/\d+$", fps) or fps.endswith("/0"):
        fail(f"could not read the frame rate of {path}")
    return fps


def ffprobe_video(path: Path):
    """(fps, start time, frame count) of the first video stream. The start time matters:
    a cut clip's first frame often sits one frame after the audio start."""
    proc = run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=r_frame_rate,start_time,nb_frames", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    try:
        stream = json.loads(proc.stdout)["streams"][0]
        num, den = (int(x) for x in stream["r_frame_rate"].split("/"))
        return num / den, float(stream["start_time"]), int(stream["nb_frames"])
    except (ValueError, KeyError, IndexError, ZeroDivisionError):
        fail(f"could not read the frame rate, start time and frame count of {path}")


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


def ass_time(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ==================================================================
# ingest
# ==================================================================

def cmd_ingest(args, root: Path, work_dir: Path):
    source = Path(args.source)
    if not source.exists():
        fail(f"source not found: {source}")

    print("Checking ffmpeg for libass support (needed later for caption burn-in)...")
    filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    if re.search(r"\bass\b", filters.stdout or ""):
        print("  ok: ass filter present")
    else:
        print("WARNING: ffmpeg is missing the 'ass' filter (no libass). The burn step will fail.", file=sys.stderr)
        print("WARNING: on macOS, install Homebrew ffmpeg-full instead of plain ffmpeg.", file=sys.stderr)

    source_abs = source.resolve()
    print(f"Output directory: {work_dir}")
    work_dir.mkdir(parents=True, exist_ok=True)

    out_mp4 = work_dir / "source.mp4"
    out_wav = work_dir / "audio.wav"

    if not args.force:
        if out_mp4.exists() or out_mp4.is_symlink():
            fail(f"{out_mp4} already exists (use --force to overwrite)")
        if out_wav.exists():
            fail(f"{out_wav} already exists (use --force to overwrite)")

    if source_abs.suffix.lower() == ".mp4":
        print("Source is already .mp4: creating a symlink instead of copying.")
        if out_mp4.exists() or out_mp4.is_symlink():
            out_mp4.unlink()
        out_mp4.symlink_to(source_abs)
        print(f"  linked {out_mp4} -> {source_abs}")
    else:
        print(f"Remuxing {source_abs} to {out_mp4} (stream copy, no re-encode)...")
        proc = run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source_abs),
                    "-c", "copy", "-map", "0", "-movflags", "+faststart", str(out_mp4)])
        if proc.returncode != 0:
            if out_mp4.exists():
                out_mp4.unlink()
            fail("remux to mp4 failed. Some streams (e.g. certain subtitle or data streams) do not fit "
                 "in an mp4 container. Pass a recording that holds only video and audio (mp4 or mkv).")
        print(f"  wrote {out_mp4}")

    print("Audio streams in source:")
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index,codec_name,channels:stream_tags=title",
         "-of", "csv=p=0", str(source_abs)],
        capture_output=True, text=True,
    )
    for n, line in enumerate(proc.stdout.splitlines()):
        parts = line.split(",")
        parts += [""] * (4 - len(parts))
        index, codec, channels, title = parts[:4]
        print(f"  a:{n}  index={index}  codec={codec}  channels={channels}  title={title or '(none)'}")

    print(f"Extracting audio track a:{args.audio_track} to 16 kHz mono WAV...")
    proc = run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source_abs),
                "-map", f"0:a:{args.audio_track}", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)])
    if proc.returncode != 0:
        fail(f"audio extraction failed for track a:{args.audio_track}. Check the audio stream list above "
             "and pick a valid --audio-track.")
    print(f"  wrote {out_wav}")

    duration = ffprobe_duration(out_wav)

    print()
    print(f"Ingest complete for event {args.event}:")
    print(f"  video: {out_mp4}")
    print(f"  audio: {out_wav} (duration: {duration}s)")


# ==================================================================
# transcribe (whisper-cli, then the compact transcript)
# ==================================================================

def ms_to_s(value_ms) -> float:
    return round(value_ms / 1000.0, 2)


SPECIAL_TOKEN_RE = re.compile(r"^\[_.*\]$")


def seg_shift(seg, vad: bool) -> float:
    """VAD timeline fix: with --vad, whisper-cli (as of 1.9.4) reports segment offsets on
    the original audio timeline but token offsets on the VAD-compressed timeline (silence
    removed), so token times fall behind by all the silence removed before them.
    When vad is true, shift each segment's tokens so its first real token lands on the
    segment start. Silence removed INSIDE a segment is not recovered, so words after an
    internal pause can be early by the length of that pause."""
    if not vad:
        return 0.0
    t0 = None
    for tok in seg.get("tokens", []) or []:
        if SPECIAL_TOKEN_RE.match(tok.get("text", "")):
            continue
        t0 = tok["offsets"]["from"]
        break
    if t0 is None:
        return 0.0
    return seg["offsets"]["from"] - t0


def merge_segment_tokens(tokens, segidx, shift):
    """Word merge: within each segment, walk tokens in order. Special tokens (text
    matching ^\\[_.*\\]$, e.g. "[_BEG_]", "[_TT_123]") are skipped. A token whose
    text starts with a space begins a new word; otherwise it is appended to the
    current word (this is how sub-word pieces and punctuation like "," or "'"
    reattach, e.g. "dell" + "'" + "amb" + "iente" -> "dell'ambiente"). A word's
    start is its first token's offset; its end is its last token's offset."""
    words = []
    cur = None
    for tok in tokens:
        text = tok.get("text", "")
        if SPECIAL_TOKEN_RE.match(text):
            continue
        if cur is None:
            cur = {"text": text, "s": tok["offsets"]["from"], "e": tok["offsets"]["to"]}
        elif text.startswith(" "):
            words.append(cur)
            cur = {"text": text, "s": tok["offsets"]["from"], "e": tok["offsets"]["to"]}
        else:
            cur["text"] += text
            cur["e"] = tok["offsets"]["to"]
    if cur is not None:
        words.append(cur)

    out = []
    for w in words:
        text = w["text"].strip()
        if not text:
            continue
        out.append({"w": text, "s": w["s"] + shift, "e": w["e"] + shift, "seg": segidx})
    return out


def build_compact(raw: dict, event: str, language: str, model_basename: str, vad: bool):
    segs = raw.get("transcription") or []

    raw_words = []
    for segidx, seg in enumerate(segs):
        shift = seg_shift(seg, vad)
        raw_words.extend(merge_segment_tokens(seg.get("tokens") or [], segidx, shift))

    n = len(raw_words)
    words = []
    for idx in range(n):
        w = raw_words[idx]
        next_s = raw_words[idx + 1]["s"] if idx + 1 < n else w["e"]
        e_clamped = min(w["e"], next_s, w["s"] + WORD_END_CLAMP_MS)
        words.append({
            "w": w["w"],
            "s": ms_to_s(w["s"]),
            "e": ms_to_s(e_clamped),
            "seg": w["seg"],
        })

    segments = []
    for segidx, seg in enumerate(segs):
        segments.append({
            "i": segidx,
            "s": ms_to_s(seg["offsets"]["from"]),
            "e": ms_to_s(seg["offsets"]["to"]),
            "text": seg["text"].strip(),
        })

    return {
        "event": event,
        "language": language,
        "model": model_basename,
        "segments": segments,
        "words": words,
    }


def cmd_transcribe(args, root: Path, work_dir: Path):
    out_raw_json = work_dir / "transcript.raw.json"
    out_compact = work_dir / "transcript.json"
    out_meta = work_dir / "transcript.meta.json"

    if args.compact_only:
        if not out_raw_json.is_file():
            fail(f"{out_raw_json} not found. Run transcribe without --compact-only first.")
        if out_compact.exists() and not args.force:
            fail(f"{out_compact} already exists (use --force to overwrite)")
        vad = True
        if out_meta.is_file():
            meta = load_json(out_meta, "transcript.meta.json")
            vad = bool(meta.get("vad", True))
        model_basename = Path(args.model).name
        raw = load_json(out_raw_json, "transcript.raw.json")
        compact = build_compact(raw, args.event, args.language, model_basename, vad)
        save_json(out_compact, compact)
        _report_transcribe(args, out_raw_json, out_compact, compact)
        return

    audio = work_dir / "audio.wav"
    if not audio.is_file():
        fail(f"{audio} not found. Run ingest for event {args.event} first.")

    model = Path(args.model)
    if not model.is_file():
        fail(f"whisper model not found: {model}\n"
             "Download ggml-large-v3-turbo.bin manually from https://huggingface.co/ggerganov/whisper.cpp\n"
             f"and place it at: {model}")

    use_vad = not args.no_vad
    vad_model = Path(args.vad_model)
    if use_vad and not vad_model.is_file():
        fail(f"VAD model not found: {vad_model}\n"
             "Download the Silero VAD ggml model manually from https://huggingface.co/ggml-org/whisper-vad\n"
             f"and place it at: {vad_model}\n"
             "(or pass --no-vad to transcribe without VAD)")

    if not args.force:
        if out_raw_json.exists():
            fail(f"{out_raw_json} already exists (use --force to overwrite)")
        if out_compact.exists():
            fail(f"{out_compact} already exists (use --force to overwrite)")

    out_raw_prefix = work_dir / "transcript.raw"
    cmd = ["whisper-cli", "-m", str(model), "-l", args.language, "-ojf",
           "-of", str(out_raw_prefix), "-f", str(audio)]

    if use_vad:
        cmd += ["--vad", "--vad-model", str(vad_model)]

    if args.threads:
        cmd += ["-t", str(args.threads)]

    proc = run(cmd)
    if proc.returncode != 0:
        fail("whisper-cli failed. See output above.")

    if not out_raw_json.is_file():
        fail(f"expected whisper-cli output not found: {out_raw_json}")

    print(f"Deriving compact transcript: {out_compact}")
    save_json(out_meta, {"vad": use_vad})

    model_basename = model.name
    raw = load_json(out_raw_json, "transcript.raw.json")
    compact = build_compact(raw, args.event, args.language, model_basename, use_vad)
    save_json(out_compact, compact)
    _report_transcribe(args, out_raw_json, out_compact, compact)


def _report_transcribe(args, out_raw_json, out_compact, compact):
    word_count = len(compact["words"])
    segment_count = len(compact["segments"])
    first_ts = compact["words"][0]["s"] if compact["words"] else None
    last_ts = compact["words"][-1]["e"] if compact["words"] else None

    print()
    print(f"Transcription complete for event {args.event}:")
    print(f"  raw whisper output: {out_raw_json}")
    print(f"  compact transcript: {out_compact}")
    print(f"  segments: {segment_count}")
    print(f"  words: {word_count}")
    print(f"  first word starts at: {first_ts}s, last word ends at: {last_ts}s")
    print()
    print("REMINDER: read the first and last minutes (clips.py render) for hallucinations:")
    print("whisper can turn silence, applause or music into invented text.")


# ==================================================================
# render
# ==================================================================

def cmd_render(args, root: Path, work_dir: Path):
    transcript_path = work_dir / "transcript.json"
    if not transcript_path.is_file():
        fail(f"{transcript_path} not found. Run transcribe for event {args.event} first.")
    transcript = load_json(transcript_path, "transcript.json")
    lines = []
    for seg in transcript["segments"]:
        t = int(math.floor(float(seg["s"])))
        m, s = divmod(t, 60)
        lines.append(f"[{seg['i']}] {m:02d}:{s:02d} {seg['text']}")
    sys.stdout.write("\n".join(lines) + ("\n" if lines else ""))


# ==================================================================
# highlight-selection (snap / preview / choose / status)
# ==================================================================

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


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silences(audio_path: Path, min_s: float = SILENCE_MIN_S, noise: str = SILENCE_NOISE):
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-vn", "-i", str(audio_path),
             "-af", f"silencedetect=noise={noise}:d={min_s}", "-f", "null", "-"],
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
    if dur > RAW_TOO_LONG_S:
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
    if cut["duration"] > RAW_TOO_LONG_S:
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


def render_preview(cand, transcript, work_dir: Path, pad: float, duration_total):
    cut = cand["cut"]
    source = work_dir / "source.mp4"
    if not source.exists():
        fail(f"source video not found: {source}. Run ingest first.")
    origin = max(0.0, cut["start"] - pad)
    end = cut["end"] + pad
    if duration_total is not None:
        end = min(end, duration_total)
    total = round(end - origin, 2)

    previews_dir = work_dir / "previews"
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
    proc = run(cmd, cwd=previews_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        fail(f"ffmpeg failed rendering preview for candidate {cand['id']}: {proc.stderr.strip()}")
    cut["preview"] = {"file": f"previews/{name}.mp4", "origin": round(origin, 2), "pad": pad}
    print(f"  wrote {previews_dir / (name + '.mp4')}", file=sys.stderr)


def candidate_segments(cand, transcript):
    """[(label, segment)] for the segments inside the candidate's cut (its segment span
    if there is no cut yet), plus one segment of context before and after."""
    segs = sorted(transcript["segments"], key=lambda g: g["i"])
    cut = cand.get("cut")
    inside = []
    for n, seg in enumerate(segs):
        if cut:
            seg_end = segs[n + 1]["s"] if n + 1 < len(segs) else seg["e"]
            if seg["s"] < cut["end"] - 0.3 and seg_end > cut["start"] + 0.3:
                inside.append(n)
        elif cand["start_seg"] <= seg["i"] <= cand["end_seg"]:
            inside.append(n)
    if not inside:
        return []
    out = []
    if inside[0] > 0:
        out.append(("(before)", segs[inside[0] - 1]))
    out.extend(("", segs[n]) for n in inside)
    if inside[-1] + 1 < len(segs):
        out.append(("(after)", segs[inside[-1] + 1]))
    return out


def find_phrase_time(transcript, silences, phrase: str, near, edge: str, cand_id):
    """Source time for a cut edge given by words: the start of the phrase (edge "start")
    or the end of it (edge "end"), moved onto a nearby pause when there is one. Word
    times are rough and tend to run early (up to about a second), so the search for a
    pause reaches further forward than back, and the human still checks the re-rendered
    preview."""
    def norm(word):
        return re.sub(r"[^\w']+", "", word.lower())
    target = [norm(w) for w in phrase.split() if norm(w)]
    if not target:
        fail(f"candidate {cand_id}: empty phrase")
    words = [w for w in transcript["words"] if near[0] - PHRASE_SEARCH_S <= w["s"] <= near[1] + PHRASE_SEARCH_S]
    normed = [norm(w["w"]) for w in words]
    hits = [i for i in range(len(words) - len(target) + 1) if normed[i:i + len(target)] == target]
    if not hits:
        fail(f"candidate {cand_id}: \"{phrase}\" not found near this clip. Copy the words exactly as "
             "they appear in the transcript (clips.py status).")
    if len(hits) > 1:
        fail(f"candidate {cand_id}: \"{phrase}\" appears {len(hits)} times near this clip. Use a longer phrase.")
    i = hits[0]
    if edge == "start":
        t = words[i]["s"]
        near_pauses = [iv for iv in silences if t - 0.6 <= iv["end"] <= t + 1.2]
        if near_pauses:
            iv = min(near_pauses, key=lambda iv: abs(iv["end"] - t))
            return iv["end"] - min(MAX_LEAD, (iv["end"] - iv["start"]) / 2)
        return max(0.0, t - 0.15)
    last = words[i + len(target) - 1]
    t = last["e"]
    near_pauses = [iv for iv in silences if last["s"] + 0.1 <= iv["start"] <= t + 1.5]
    if near_pauses:
        iv = min(near_pauses, key=lambda iv: iv["start"])
        return iv["start"] + min(MAX_TAIL, (iv["end"] - iv["start"]) / 2)
    return t + 0.15


def print_status(highlights, event, transcript):
    """One block per candidate: id, score, chosen or not, segment range, duration,
    flags, whether each cut edge sits on a pause, any manual adjustment, the hook,
    and if a preview exists its path and the M:SS.s in/out for the player."""
    print(f"Highlight candidates: {event}")
    print()
    for cand in sorted(highlights["candidates"], key=lambda c: c["id"]):
        cut = cand.get("cut")
        chosen = cand.get("chosen", False)
        marker = " [CHOSEN]" if chosen else ""
        print(f"Candidate {cand['id']}: score {cand['score']}{marker}")
        print(f"  segments: {cand['start_seg']} to {cand['end_seg']}")
        if cut:
            over = f", over {TOO_LONG_S:.0f}s: needs tighten --drop" if cut["duration"] > TOO_LONG_S else ""
            print(f"  range: {mmss(cut['start'])} to {mmss(cut['end'])} ({cut['duration']}s{over})")
            flags = ", ".join(cut["flags"]) if cut["flags"] else "none"
            print(f"  flags: {flags}")
            manual = cut.get("manual_shift") or {}
            edges = []
            for edge in ("start", "end"):
                if edge in manual:
                    edges.append(f"{edge} set by hand ({manual[edge]:+g}s from the snapped cut)")
                elif cut[f"{edge}_snapped"]:
                    edges.append(f"{edge} on a pause")
                else:
                    edges.append(f"{edge} NOT on a pause (check the preview)")
            print(f"  cut edges: {', '.join(edges)}")
            if cut.get("preview"):
                pv = cut["preview"]
                clip_in = cut["start"] - pv["origin"]
                clip_out = cut["end"] - pv["origin"]
                print(f"  preview: {pv['file']}")
                print(f"  in the player the clip runs from {clock(clip_in)} to {clock(clip_out)}")
        else:
            print("  range: not snapped yet")
        print(f"  needs visuals: {'yes' if cand['needs_visuals'] else 'no'}")
        print(f"  hook: {cand['hook']}")
        print("  transcript (what is inside the cut, plus one sentence of context either side):")
        for label, seg in candidate_segments(cand, transcript):
            print(f"    {label:9}[{seg['i']}] {seg['text']}")
        print()


def cmd_snap(args, root: Path, work_dir: Path):
    highlights_path = work_dir / "highlights.json"
    transcript_path = work_dir / "transcript.json"

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
    print_status(highlights, args.event, transcript)


def parse_id_phrases(pairs):
    """["2=mila euro", ...] -> {2: "mila euro"}."""
    phrases = {}
    for pair in pairs or []:
        id_str, sep, phrase = pair.partition("=")
        if not sep or not id_str.strip().isdigit() or not phrase.strip():
            fail(f"invalid argument (expected ID=WORDS): {pair}")
        phrases[int(id_str)] = phrase.strip()
    return phrases


def parse_id_times(pairs):
    """["2=0:12", ...] -> {2: 12.0}. Times are seconds or M:SS."""
    times = {}
    for pair in pairs or []:
        if "=" not in pair:
            fail(f"invalid argument (expected ID=TIME): {pair}")
        id_str, sec_str = pair.split("=", 1)
        try:
            cid = int(id_str)
            sec = parse_clock(sec_str)
        except ValueError:
            fail(f"invalid argument (expected ID=TIME): {pair}")
        times[cid] = sec
    return times


def parse_ids(text):
    try:
        return {int(x) for x in text.split(",") if x.strip()}
    except ValueError:
        fail(f"--ids must be a comma-separated list of integers, got: {text}")


def cmd_choose(args, root: Path, work_dir: Path):
    highlights_path = work_dir / "highlights.json"
    transcript_path = work_dir / "transcript.json"

    highlights = load_json(highlights_path, "highlights.json")
    validate_highlights(highlights, highlights_path)
    transcript = load_json(transcript_path, "transcript.json")
    validate_transcript(transcript, transcript_path)

    chosen_ids = None  # None = leave the chosen flags as they are
    if args.ids is not None:
        chosen_ids = parse_ids(args.ids)

    known_ids = {c["id"] for c in highlights["candidates"]}
    unknown = (chosen_ids or set()) - known_ids
    if unknown:
        fail(f"--ids references unknown candidate id(s): {sorted(unknown)} (known: {sorted(known_ids)})")

    at_start = parse_id_times(args.start)
    at_end = parse_id_times(args.end)
    words_start = parse_id_phrases(args.start_at)
    words_end = parse_id_phrases(args.end_after)
    silences = []
    if words_start or words_end:
        silences = load_json(work_dir / "silences.json", "silences cache (run snap first)")["intervals"]
    reset_ids = set()
    for text in args.reset or []:
        try:
            reset_ids.add(int(text))
        except ValueError:
            fail(f"--reset must be an integer candidate id, got: {text}")
    for cid in list(at_start) + list(at_end) + list(words_start) + list(words_end) + list(reset_ids):
        if cid not in known_ids:
            fail(f"unknown candidate id: {cid} (known: {sorted(known_ids)})")
    for cid in reset_ids:
        if cid in at_start or cid in at_end or cid in words_start or cid in words_end:
            fail(f"candidate {cid}: use either --reset or an edge option, not both")
    for cid in set(at_start) & set(words_start):
        fail(f"candidate {cid}: use either --start or --start-at, not both")
    for cid in set(at_end) & set(words_end):
        fail(f"candidate {cid}: use either --end or --end-after, not both")

    audio_path = work_dir / "audio.wav"
    duration_total = ffprobe_duration(audio_path) if audio_path.is_file() else None

    for cand in highlights["candidates"]:
        if chosen_ids is not None:
            cand["chosen"] = cand["id"] in chosen_ids
        cut = cand.get("cut")
        if cut is None:
            continue
        before = (cut["start"], cut["end"])
        manual = dict(cut.pop("manual_shift", {}))
        touched = False

        if cand["id"] in reset_ids:
            for edge in ("start", "end"):
                if edge in manual:
                    cut[edge] = round(cut[edge] - manual.pop(edge), 2)
                    touched = True

        # A shift is always relative to the snapped cut. An edge named in this call has
        # its earlier shift undone and replaced; edges not named keep what they had, so
        # candidates can be adjusted one at a time without losing earlier adjustments.
        for edge, given, phrases in (("start", at_start, words_start), ("end", at_end, words_end)):
            if cand["id"] not in given and cand["id"] not in phrases:
                continue
            touched = True
            if cand["id"] in given:
                # a time read off the preview player
                if not cut.get("preview"):
                    fail(f"candidate {cand['id']}: --{edge} needs a rendered preview; run the preview subcommand first")
                target = cut["preview"]["origin"] + given[cand["id"]]
            else:
                # words quoted from the transcript
                target = find_phrase_time(transcript, silences, phrases[cand["id"]],
                                          (cut["start"], cut["end"]), edge, cand["id"])
            snapped = round(cut[edge] - manual.pop(edge, 0.0), 2)
            shift = round(target - snapped, 2)
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
            render_preview(cand, transcript, work_dir, cut["preview"]["pad"], duration_total)

    apply_overlap_flags(highlights["candidates"])

    save_json(highlights_path, highlights)
    print(f"Wrote {highlights_path}", file=sys.stderr)
    print_status(highlights, args.event, transcript)


def cmd_preview(args, root: Path, work_dir: Path):
    highlights_path = work_dir / "highlights.json"
    transcript_path = work_dir / "transcript.json"
    highlights = load_json(highlights_path, "highlights.json")
    validate_highlights(highlights, highlights_path)
    transcript = load_json(transcript_path, "transcript.json")
    validate_transcript(transcript, transcript_path)

    wanted = None
    if args.ids:
        wanted = parse_ids(args.ids)
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
        render_preview(cand, transcript, work_dir, args.pad, duration_total)

    save_json(highlights_path, highlights)
    print_status(highlights, args.event, transcript)


def cmd_status(args, root: Path, work_dir: Path):
    highlights_path = work_dir / "highlights.json"
    transcript_path = work_dir / "transcript.json"
    highlights = load_json(highlights_path, "highlights.json")
    validate_highlights(highlights, highlights_path)
    transcript = load_json(transcript_path, "transcript.json")
    validate_transcript(transcript, transcript_path)
    print_status(highlights, args.event, transcript)


# ==================================================================
# cut
# ==================================================================

def cmd_cut(args, root: Path, work_dir: Path):
    highlights_path = work_dir / "highlights.json"
    source = work_dir / "source.mp4"
    out_dir = work_dir / "final"

    if not highlights_path.is_file():
        fail(f"{highlights_path} not found. Run the highlight-selection steps first.")
    if not source.exists():
        fail(f"{source} not found. Run ingest first.")

    highlights = load_json(highlights_path, "highlights.json")
    validate_highlights(highlights, highlights_path)

    if args.ids:
        wanted = parse_ids(args.ids)
        cuts = [(c["id"], c["cut"]["start"], c["cut"]["duration"])
                for c in highlights["candidates"] if c["id"] in wanted and c.get("cut")]
    else:
        cuts = [(c["id"], c["cut"]["start"], c["cut"]["duration"])
                for c in highlights["candidates"] if c.get("chosen") and c.get("cut")]

    if not cuts:
        fail("nothing to cut: no chosen candidates (run choose --ids ...) or no match for --ids")

    out_dir.mkdir(parents=True, exist_ok=True)

    for cid, start, duration in cuts:
        out = out_dir / f"clip_{cid}.mp4"
        if out.exists() and not args.force:
            fail(f"{out} already exists (use --force to overwrite)")
        print(f"Cutting candidate {cid}: start {start}s, duration {duration}s -> {out}")
        # -ss before -i plus a re-encode gives a frame-accurate cut. Never stream-copy here.
        proc = run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-ss", str(start), "-t", str(duration), "-i", str(source),
                    "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])
        if proc.returncode != 0:
            fail(f"ffmpeg failed cutting candidate {cid}")
        print(f"  wrote {out}")


# ==================================================================
# produce-clip (cut is above; align / burn)
# ==================================================================

def dtw_preset(model_path: Path) -> str:
    """ggml-large-v3-turbo.bin -> large.v3.turbo (the name whisper-cli --dtw expects)."""
    name = model_path.name
    name = re.sub(r"^ggml-", "", name)
    name = re.sub(r"\.bin$", "", name)
    name = re.sub(r"-q\d.*$", "", name)
    return name.replace("-", ".")


def words_from_raw(raw: dict):
    """Merge whisper tokens into words. Returns [{w, s, e}] in clip seconds."""
    words = []
    for seg in raw.get("transcription", []):
        seg_start = seg["offsets"]["from"] / 1000.0
        first_in_seg = True
        cur = None
        for tok in seg.get("tokens", []):
            text = tok.get("text", "")
            if re.match(r"^\[_.*\]$", text):
                continue
            t_dtw = tok.get("t_dtw", -1)
            if t_dtw is not None and t_dtw >= 0:
                start = max(0.0, t_dtw / 100.0 - DTW_LEAD)
            else:
                start = tok["offsets"]["from"] / 1000.0
            if cur is None or text.startswith(" "):
                if cur is not None:
                    words.append(cur)
                if first_in_seg:
                    start = seg_start
                    first_in_seg = False
                cur = {"w": text, "s": start}
            else:
                cur["w"] += text
        if cur is not None:
            words.append(cur)

    cleaned = []
    for word in words:
        word["w"] = word["w"].strip()
        if not word["w"]:
            continue
        # punctuation-only tokens attach to the previous word
        if cleaned and re.match(r"^[^\w]+$", word["w"]):
            cleaned[-1]["w"] += word["w"]
            continue
        cleaned.append(word)

    # monotonic starts, ends clamped to the next start
    for i, word in enumerate(cleaned):
        if i > 0 and word["s"] < cleaned[i - 1]["s"] + MIN_WORD:
            word["s"] = cleaned[i - 1]["s"] + MIN_WORD
    # Token end times come from a different clock than the DTW starts, so they are not
    # used. A word lasts until the next word starts, capped at a length-based estimate
    # so that a real pause still shows up as a gap between words.
    for i, word in enumerate(cleaned):
        word["e"] = word["s"] + 0.2 + 0.07 * len(word["w"])
        if i + 1 < len(cleaned):
            word["e"] = max(word["s"] + MIN_WORD, min(word["e"], cleaned[i + 1]["s"]))
    return cleaned


def clip_paths(work_dir: Path, clip_id: int):
    final = work_dir / "final"
    base = final / f"clip_{clip_id}"
    return final, base


def _align_one(root, work_dir, event, clip_id, model, language, force):
    final, base = clip_paths(work_dir, clip_id)
    clip = base.with_suffix(".mp4")
    words_path = Path(str(base) + ".words.tsv")
    if not clip.is_file():
        fail(f"{clip} not found. Run the cut subcommand first.")
    if words_path.exists() and not force:
        fail(f"{words_path} already exists and may hold human edits (use --force to overwrite)")
    if not model.is_file():
        fail(f"whisper model not found: {model} (see clips/README.md for the download)")

    wav = Path(str(base) + ".align.wav")
    raw_prefix = Path(str(base) + ".align")
    proc = run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip),
                "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
    if proc.returncode != 0:
        fail("ffmpeg failed extracting the clip audio")

    # No VAD on purpose: with VAD, token times lose the removed silence. DTW needs
    # flash attention off.
    proc = run(["whisper-cli", "-m", str(model), "-l", language, "-ojf",
                "-dtw", dtw_preset(model), "-nfa", "-of", str(raw_prefix), "-f", str(wav)],
               capture_output=True, text=True)
    raw_json = Path(str(raw_prefix) + ".json")
    if proc.returncode != 0 or not raw_json.is_file():
        fail(f"whisper-cli failed: {proc.stderr.strip()[-400:]}")

    words = words_from_raw(json.loads(raw_json.read_text(encoding="utf-8")))
    if not words:
        fail("whisper-cli returned no words for this clip")
    lines = [f"{w['s']:.2f}\t{w['e']:.2f}\t{w['w']}" for w in words]
    words_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if wav.exists():
        wav.unlink()
    print(f"Wrote {words_path} ({len(words)} words). Review and edit it, then run: clips.py burn "
          f"--event {event} --ids {clip_id}")


def cmd_align(args, root: Path, work_dir: Path):
    model = Path(args.model) if args.model else root / "clips" / "models" / "ggml-large-v3-turbo.bin"
    ids = _resolve_ids_default_chosen(args, work_dir)
    for clip_id in ids:
        _align_one(root, work_dir, args.event, clip_id, model, args.language, args.force)


def _resolve_ids_default_chosen(args, work_dir):
    if args.ids:
        return sorted(parse_ids(args.ids))
    highlights_path = work_dir / "highlights.json"
    if not highlights_path.is_file():
        fail(f"{highlights_path} not found and --ids was not given.")
    highlights = load_json(highlights_path, "highlights.json")
    validate_highlights(highlights, highlights_path)
    ids = sorted(c["id"] for c in highlights["candidates"] if c.get("chosen"))
    if not ids:
        fail("no chosen candidates and no --ids given")
    return ids


def read_words(path: Path):
    words = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            fail(f"{path}:{n}: expected start<TAB>end<TAB>word, got: {line!r}")
        try:
            start, end = float(parts[0]), float(parts[1])
        except ValueError:
            fail(f"{path}:{n}: start and end must be numbers, got: {line!r}")
        if end <= start:
            fail(f"{path}:{n}: end must be after start, got: {line!r}")
        words.append({"w": parts[2].strip(), "s": start, "e": end})
    for i in range(1, len(words)):
        if words[i]["s"] < words[i - 1]["s"]:
            fail(f"{path}: word starts must not go backwards ('{words[i]['w']}' at {words[i]['s']})")
    if not words:
        fail(f"{path} has no words")
    return words


# ------------------------------------------------------------------ tighten

def merge_quiet_runs(intervals, max_blip: float):
    """Join quiet runs that only a click (shorter than max_blip) keeps apart."""
    merged = []
    for iv in intervals:
        if merged and iv["start"] - merged[-1]["end"] <= max_blip:
            merged[-1]["end"] = max(merged[-1]["end"], iv["end"])
        else:
            merged.append(dict(iv))
    return merged


def plan_pauses(words, quiet_runs, duration, fps, video_start):
    """One cut per pause worth shortening, on the clip's video frame grid. The audio says
    where a pause is, the words only say how much of it to keep: more after a sentence end.
    Quiet runs touching the clip edges are left alone, those belong to snap and choose."""
    cuts = []
    for run_ in quiet_runs:
        start, end = run_["start"], run_["end"]
        if start <= 0.01 or end >= duration - 0.01 or end - start < TIGHT_MIN_PAUSE:
            continue
        before = [i for i, w in enumerate(words) if w["s"] <= start]
        after = before[-1] if before else None
        sentence_end = after is not None and words[after]["w"][-1:] in ".?!"
        keep = TIGHT_GAP_SENTENCE if sentence_end else TIGHT_GAP
        if end - start - keep < TIGHT_MIN_CUT:
            continue
        # Round inwards, so rounding never removes more than planned.
        a = math.ceil((start + keep * TIGHT_HEAD - video_start) * fps - 1e-6)
        b = math.floor((end - keep * (1 - TIGHT_HEAD) - video_start) * fps + 1e-6)
        if b <= a:
            continue
        cuts.append({
            "start_frame": a, "end_frame": b,
            "start": round(video_start + a / fps, 3), "end": round(video_start + b / fps, 3),
            "reason": "pause", "pause": round(end - start, 3),
            "after": after, "after_word": words[after]["w"] if after is not None else "",
            "apply": True,
        })
    return cuts


def find_words(words, phrase: str, start: int = 0):
    """Indices where the phrase begins in the words list, and its length in words."""
    def norm(word):
        return re.sub(r"[^\w']+", "", word.lower())
    target = [norm(w) for w in phrase.split() if norm(w)]
    normed = [norm(w["w"]) for w in words]
    hits = [i for i in range(start, len(words) - len(target) + 1)
            if target and normed[i:i + len(target)] == target]
    return hits, len(target)


def plan_drop(words, quiet_levels, fps, video_start, spec: str, clip_id):
    """A cut that removes a stretch of speech, given by its words: "first words ... last
    words", or one phrase to drop just that. Word times only find the two pauses, the
    edges then sit inside them, and between them the pause left over is as long as any
    other shortened pause."""
    first, sep, last = spec.partition(DROP_SEP)
    hits, n = find_words(words, first)
    if len(hits) != 1:
        fail(f"clip {clip_id}: \"{first.strip()}\" appears {len(hits)} times in the words file. Copy the "
             "words as written there, and use more of them if they repeat.")
    i = hits[0]
    j = i + n - 1
    if sep:
        hits, n = find_words(words, last, i)
        if not hits:
            fail(f"clip {clip_id}: \"{last.strip()}\" not found after \"{first.strip()}\" in the words file")
        j = hits[0] + n - 1
    if i == 0 or j >= len(words) - 1:
        fail(f"clip {clip_id}: this drop reaches the edge of the clip. Move the clip's start or end "
             "with choose instead.")

    def pause_near(t):
        """The pause that ends closest to t, the start of the first word after an edge.
        quiet_levels: the clip's quiet runs at SILENCE_NOISE, then at each louder floor."""
        for runs in quiet_levels:
            near = [r for r in runs if r["end"] >= t - DROP_BACK and r["start"] <= t + DROP_AHEAD]
            if near:
                return min(near, key=lambda r: abs(r["end"] - t))
        return None

    def first_pause_after(prev_word, t):
        """The first pause once prev_word is over, up to t. Any hesitation between it and
        the dropped words goes too."""
        for runs in quiet_levels:
            near = [r for r in runs if prev_word["s"] + 0.2 <= r["start"] <= t + DROP_AHEAD]
            if near:
                return min(near, key=lambda r: r["start"])
        return None

    before = first_pause_after(words[i - 1], words[i]["s"])
    after = pause_near(words[j + 1]["s"])
    if before is None or after is None:
        where = "before" if before is None else "after"
        fail(f"clip {clip_id}: no pause {where} \"{spec}\". A cut inside running speech splits words: "
             "widen the drop to the nearest pauses, or leave it in.")
    keep = TIGHT_GAP_SENTENCE if words[i - 1]["w"][-1:] in ".?!" else TIGHT_GAP
    start = min(before["start"] + keep * TIGHT_HEAD, before["end"])
    end = max(after["end"] - keep * (1 - TIGHT_HEAD), after["start"])
    a = math.ceil((start - video_start) * fps - 1e-6)
    b = math.floor((end - video_start) * fps + 1e-6)
    if b <= a:
        fail(f"clip {clip_id}: could not place the drop \"{spec}\" (word times out of order?)")
    shown = [w["w"] for w in words[i:j + 1]]
    return {
        "start_frame": a, "end_frame": b,
        "start": round(video_start + a / fps, 3), "end": round(video_start + b / fps, 3),
        "reason": "drop", "from_index": i, "to_index": j,
        "text": " ".join(shown) if len(shown) <= 8 else " ".join(shown[:4] + ["..."] + shown[-4:]),
        "after": i - 1, "after_word": words[i - 1]["w"],
        "apply": True,
    }


def dropped_words(cuts):
    """Indices of the words that applied drops remove."""
    gone = set()
    for cut in cuts:
        if cut["reason"] == "drop" and cut.get("apply", True):
            gone.update(range(cut["from_index"], cut["to_index"] + 1))
    return gone


def keep_ranges(cuts, frames: int):
    """Frame ranges [a, b) that survive the applied cuts, in order."""
    keeps, pos = [], 0
    for cut in sorted((c for c in cuts if c.get("apply", True)), key=lambda c: c["start_frame"]):
        a, b = max(cut["start_frame"], pos), min(cut["end_frame"], frames)
        if b <= a:
            continue
        if a > pos:
            keeps.append((pos, a))
        pos = b
    if pos < frames:
        keeps.append((pos, frames))
    return keeps


def tight_time(t: float, keeps, fps, video_start) -> float:
    """Clip time -> time in the tightened clip. A time inside a removed range lands on
    the splice."""
    out = 0.0
    for a, b in keeps:
        s, e = video_start + a / fps, video_start + b / fps
        if t < s:
            return out
        if t < e:
            return out + (t - s)
        out += e - s
    return out


def remap_words(words, keeps, fps, video_start, gone=()):
    """The words of words.tsv moved onto the tightened clip's timeline, without the
    dropped ones (gone: their indices)."""
    out = []
    for i, word in enumerate(words):
        if i in gone:
            continue
        s = tight_time(word["s"], keeps, fps, video_start)
        e = tight_time(word["e"], keeps, fps, video_start)
        out.append({"w": word["w"], "s": s, "e": max(e, s + MIN_WORD)})
    return out


def tighten_graph(keeps, fps, video_start) -> str:
    """trim/atrim each kept range and concat them. Video is trimmed half a frame early so a
    frame timestamp never sits on a boundary; audio uses the exact frame times, so both
    streams of a piece have the same length and concat has nothing to pad. (aselect would
    only cut on whole audio frames and drift.)"""
    n = len(keeps)
    lines = [f"[0:v]split={n}" + "".join(f"[v{i}]" for i in range(n)),
             f"[0:a]asplit={n}" + "".join(f"[a{i}]" for i in range(n))]
    for i, (a, b) in enumerate(keeps):
        vs = max(0.0, video_start + (a - 0.5) / fps)
        ve = video_start + (b - 0.5) / fps
        ts, te = video_start + a / fps, video_start + b / fps
        lines.append(f"[v{i}]trim=start={vs:.6f}:end={ve:.6f},setpts=PTS-STARTPTS[pv{i}]")
        fades = ""
        if i > 0:
            fades += f",afade=t=in:d={TIGHT_FADE}"
        if i < n - 1:
            fades += f",afade=t=out:st={te - ts - TIGHT_FADE:.6f}:d={TIGHT_FADE}"
        lines.append(f"[a{i}]atrim=start={ts:.6f}:end={te:.6f},asetpts=PTS-STARTPTS{fades}[pa{i}]")
    lines.append("".join(f"[pv{i}][pa{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    return ";\n".join(lines) + "\n"


def print_tighten_report(clip_id, words, plan, keeps):
    fps = plan["fps"]
    duration = plan["frames"] / fps
    applied = [c for c in plan["cuts"] if c.get("apply", True)]
    tight = sum(b - a for a, b in keeps) / fps
    removed = duration - tight
    print(f"Clip {clip_id}: {duration:.1f} s -> {tight:.1f} s "
          f"({removed:.1f} s out, {100 * removed / duration:.0f}%), "
          f"{len(applied)} of {len(plan['cuts'])} cuts applied")
    for n, cut in enumerate(plan["cuts"], 1):
        length = cut["end"] - cut["start"]
        if not cut.get("apply", True):
            state = "kept as it is"
        elif cut["reason"] == "drop":
            state = f"dropped \"{cut['text']}\", {length:.1f} s out"
        else:
            state = f"pause {cut['pause']:.2f} s -> {cut['pause'] - length:.2f} s"
        print(f"  [{n}] {clock(cut['start'])} after \"{cut['after_word']}\": {state}")
    marks = {}
    for n, cut in enumerate(plan["cuts"], 1):
        if cut.get("apply", True):
            marks.setdefault(cut["after"], []).append(f"[{n}]")
    gone = dropped_words(plan["cuts"])
    text = " ".join(marks.get(None, []))
    for i, word in enumerate(words):
        if i not in gone:
            text += " " + " ".join([word["w"]] + marks.get(i, []))
    print(textwrap.fill(text.strip(), width=100, initial_indent="  ", subsequent_indent="  "))
    if tight > TOO_LONG_S:
        print(f"  warning: still over {TOO_LONG_S:.0f} s. Drop more, or move the clip's start or end with choose.")


def _tighten_one(work_dir, event, clip_id, args):
    final, base = clip_paths(work_dir, clip_id)
    clip = base.with_suffix(".mp4")
    words_path = Path(str(base) + ".words.tsv")
    plan_path = Path(str(base) + ".tighten.json")
    graph_name = f"clip_{clip_id}.tighten.graph"
    out_name = f"clip_{clip_id}.tight.mp4"
    if not clip.is_file():
        fail(f"{clip} not found. Run the cut subcommand first.")
    if not words_path.is_file():
        fail(f"{words_path} not found. Run the align subcommand first.")

    words = read_words(words_path)
    fps, video_start, frames = ffprobe_video(clip)
    drops = [spec for cid, spec in args.drops if cid == clip_id]
    runs = None
    if plan_path.is_file() and not args.replan:
        plan = load_json(plan_path, "tighten plan")
        if plan.get("frames") != frames or plan.get("words", len(words)) != len(words):
            fail(f"{plan_path} was made for a different cut or words file of this clip (use --replan)")
        print(f"Using {plan_path} as it is, hand edits included (--replan recomputes it).")
    else:
        runs = merge_quiet_runs(detect_silences(clip, TIGHT_DETECT_S), TIGHT_MERGE_S)
        plan = {
            "fps": fps, "video_start": video_start, "frames": frames, "words": len(words),
            "cuts": plan_pauses(words, runs, ffprobe_duration(clip), fps, video_start),
        }
    if drops:
        levels = [merge_quiet_runs(detect_silences(clip, TIGHT_DETECT_S, noise), TIGHT_MERGE_S)
                  for noise in (SILENCE_NOISE,) + DROP_NOISE_LADDER]
    for spec in drops:
        drop = plan_drop(words, levels, fps, video_start, spec, clip_id)
        # The drop takes over any cut it overlaps, such as a pause inside the dropped stretch.
        plan["cuts"] = sorted(
            [c for c in plan["cuts"]
             if c["end_frame"] <= drop["start_frame"] or c["start_frame"] >= drop["end_frame"]] + [drop],
            key=lambda c: c["start_frame"])
    if drops or runs is not None:
        save_json(plan_path, plan)
        print(f"Wrote {plan_path}")

    keeps = keep_ranges(plan["cuts"], frames)
    print_tighten_report(clip_id, words, plan, keeps)
    if len(keeps) < 2:
        print("  nothing to tighten: burn will use the clip as it is.")
        return

    (final / graph_name).write_text(tighten_graph(keeps, fps, video_start), encoding="utf-8")
    # One decode, one encode, run inside final/ like burn. The graph goes in a file:
    # a long clip has dozens of pieces.
    proc = run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", clip.name,
                "-/filter_complex", graph_name, "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_name], cwd=final)
    if proc.returncode != 0:
        fail(f"ffmpeg failed tightening clip {clip_id}")
    print(f"Wrote {final / out_name}. Watch it, then run: clips.py burn --event {event} --ids {clip_id}")


def cmd_tighten(args, root: Path, work_dir: Path):
    args.drops = []
    for pair in args.drop or []:
        id_str, sep, spec = pair.partition("=")
        if not sep or not id_str.strip().isdigit() or not spec.strip():
            fail(f"invalid --drop (expected ID=first words{DROP_SEP}last words): {pair}")
        args.drops.append((int(id_str), spec.strip()))
    ids = _resolve_ids_default_chosen(args, work_dir)
    stray = sorted({cid for cid, _ in args.drops} - set(ids))
    if stray:
        fail(f"--drop names clip(s) {stray} that this run does not tighten (check --ids)")
    for clip_id in ids:
        _tighten_one(work_dir, args.event, clip_id, args)


def tightened_inputs(base: Path, clip: Path, words):
    """(clip, words) for burn: the tightened pair when tighten has shortened something."""
    plan_path = Path(str(base) + ".tighten.json")
    if not plan_path.is_file():
        return clip, words
    plan = load_json(plan_path, "tighten plan")
    keeps = keep_ranges(plan["cuts"], plan["frames"])
    if len(keeps) < 2:
        return clip, words
    tight = Path(str(base) + ".tight.mp4")
    expected = sum(b - a for a, b in keeps) / plan["fps"]
    if not tight.is_file() or abs(ffprobe_duration(tight) - expected) > 0.1:
        fail(f"{tight} is missing or older than {plan_path}. Run the tighten subcommand again, "
             f"or pass --no-tighten.")
    if plan.get("words", len(words)) != len(words):
        fail(f"{base}.words.tsv no longer has the word count {plan_path} was made for. "
             f"Run tighten --replan (and give the drops again).")
    return tight, remap_words(words, keeps, plan["fps"], plan["video_start"], dropped_words(plan["cuts"]))


# ------------------------------------------------------------------ burn

def make_chunks(words):
    chunks, cur = [], []
    for word in words:
        if cur:
            gap = word["s"] - cur[-1]["e"]
            length = sum(len(w["w"]) for w in cur) + len(cur) + len(word["w"])
            if (len(cur) >= MAX_WORDS or length > MAX_CHARS or gap >= GAP_BREAK
                    or cur[-1]["w"][-1:] in BREAK_AFTER):
                chunks.append(cur)
                cur = []
        cur.append(word)
    if cur:
        chunks.append(cur)
    return chunks


def display(word: str, style) -> str:
    text = word.rstrip(STRIP_PUNCT).lstrip("¿¡")
    text = text.replace("{", "(").replace("}", ")").replace("\\", "/")
    return text.upper() if style["uppercase"] else text


def build_ass(words, style, caption_top: int, footer: str, duration: float) -> str:
    """Captions hang from y = caption_top (below the picture); footer is static text
    at the bottom of the canvas for the whole clip."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {CANVAS_W}
PlayResY: {CANVAS_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{style['font']},{style['size']},{style['primary']},{style['primary']},{style['outline_colour']},&H80000000,0,0,0,0,100,100,0,0,1,{style['outline']},{style['shadow']},8,60,60,{int(caption_top)},1
Style: Footer,{style['footer_font']},{style['footer_size']},{style['active']},{style['active']},{style['outline_colour']},&H80000000,0,0,0,0,100,100,0,0,1,0,0,2,60,60,{style['footer_margin_v']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    active = "{\\c%s\\fscx%d\\fscy%d}" % (style["active"], style["active_scale"], style["active_scale"])
    events = []
    if footer:
        events.append(f"Dialogue: 0,{ass_time(0)},{ass_time(duration)},Footer,,0,0,0,,{footer}")
    chunks = make_chunks(words)
    for ci, chunk in enumerate(chunks):
        next_chunk_start = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else None
        for wi, word in enumerate(chunk):
            start = word["s"]
            if wi + 1 < len(chunk):
                end = chunk[wi + 1]["s"]
            else:
                end = max(word["e"], word["s"] + 0.15) + LAST_WORD_HOLD
                if next_chunk_start is not None:
                    end = min(end, next_chunk_start)
            if end <= start:
                continue
            parts = []
            for k, other in enumerate(chunk):
                shown = display(other["w"], style)
                parts.append(active + shown + "{\\r}" if k == wi else shown)
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(events) + "\n"


def read_burn_crops(highlights_path: Path, given_crop, ids, for_ids_only: bool):
    """{clip id: crop or None}. --crop is remembered in highlights.json under "burn": for the
    whole event ("crop"), or, when given together with --ids, for just those clips ("clips",
    for when the speaker stands somewhere else). A clip's own crop wins over the event's."""
    if given_crop and not re.match(r"^\d+:\d+:\d+:\d+$", given_crop):
        fail(f"--crop must be W:H:X:Y in source pixels, got: {given_crop}")
    if not highlights_path.is_file():
        return {i: given_crop for i in ids}  # nowhere to remember it
    highlights = load_json(highlights_path, "highlights.json")
    burn = highlights.setdefault("burn", {})
    if given_crop:
        if for_ids_only:
            burn.setdefault("clips", {}).update({str(i): given_crop for i in ids})
        else:
            burn["crop"] = given_crop
        save_json(highlights_path, highlights)
    own = burn.get("clips") or {}
    return {i: own.get(str(i), burn.get("crop")) for i in ids}


def _burn_one(root, work_dir, clip_id, args, crop):
    final, base = clip_paths(work_dir, clip_id)
    clip = base.with_suffix(".mp4")
    words_path = Path(str(base) + ".words.tsv")
    if not clip.is_file():
        fail(f"{clip} not found. Run the cut subcommand first.")
    if not words_path.is_file():
        fail(f"{words_path} not found. Run the align subcommand first.")

    style = dict(DEFAULT_STYLE)
    for key in ("font", "size", "active"):
        value = getattr(args, key)
        if value is not None:
            style[key] = value
    style["uppercase"] = not args.keep_case

    words = read_words(words_path)
    if not args.no_tighten:
        clip, words = tightened_inputs(base, clip, words)
    width, height = ffprobe_size(clip)
    encode = ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
              "-c:a", "copy", "-movflags", "+faststart"]

    # Vertical 1080x1920: brand background, logo on top, the (optionally cropped) picture
    # at full width, captions in the free space below it, link at the bottom.
    logo = root / "clips" / "config" / "brand" / "logo-dark-bg.png"
    if not logo.is_file():
        fail(f"brand logo not found: {logo}")
    crop_filter = "null"
    if crop:
        crop_filter = f"crop={crop}"
        width, height = (int(x) for x in crop.split(":")[:2])
    video_h = int(round(CANVAS_W * height / width / 2)) * 2
    video_y = max(LOGO_Y + 200, (CANVAS_H - video_h) // 2 - 140)
    ass_name = f"clip_{clip_id}.ass"
    out_name = f"clip_{clip_id}.final.mp4"
    duration = ffprobe_duration(clip)
    if duration > TOO_LONG_S:
        # snap lets a cut run to RAW_TOO_LONG_S on the promise that tighten shortens it
        print(f"warning: clip {clip_id} is {duration:.0f} s, over {TOO_LONG_S:.0f} s. Drop stretches with tighten first.")
    (final / ass_name).write_text(
        build_ass(words, style, caption_top=video_y + video_h + 120,
                  footer=args.footer, duration=duration),
        encoding="utf-8-sig")
    graph = (
        f"[0:v]{crop_filter},scale={CANVAS_W}:{video_h}[v];"
        f"color=c=0x{style['background']}:s={CANVAS_W}x{CANVAS_H}:r={ffprobe_fps(clip)}[bg];"
        f"[1:v]scale={LOGO_W}:-1[lg];"
        f"[bg][v]overlay=0:{video_y}:shortest=1[a];"
        f"[a][lg]overlay=(W-w)/2:{LOGO_Y}[b];"
        f"[b]ass={ass_name}[out]"
    )
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", clip.name, "-i", str(logo),
           "-filter_complex", graph, "-map", "[out]", "-map", "0:a?"] + encode + [out_name]

    # Run inside final/ so the ass filter gets a bare file name (no escaping needed).
    proc = run(cmd, cwd=final)
    if proc.returncode != 0:
        fail("ffmpeg failed burning the captions (is the ass filter available? use ffmpeg-full on macOS)")
    print(f"Wrote {final / out_name}")


def cmd_burn(args, root: Path, work_dir: Path):
    highlights_path = work_dir / "highlights.json"
    ids = _resolve_ids_default_chosen(args, work_dir)
    crops = read_burn_crops(highlights_path, args.crop, ids, for_ids_only=bool(args.ids))
    for clip_id in ids:
        _burn_one(root, work_dir, clip_id, args, crops[clip_id])


# ==================================================================
# argument parsing
# ==================================================================

def add_common(p):
    p.add_argument("--event", required=True, help="Event date, YYYY-MM-DD.")
    p.add_argument("--work-dir", help=argparse.SUPPRESS)  # hidden testing override for clips/work/<event>


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clips.py",
        description="Mantova Dev clipping pipeline, one command-line tool.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="Normalize a source recording into source.mp4 + audio.wav.")
    add_common(p)
    p.add_argument("--source", required=True, help="Path to the source recording (mp4, mkv, mov).")
    p.add_argument("--audio-track", default="0", help="0-based index among audio streams to extract (default: 0).")
    p.add_argument("--force", action="store_true", help="Overwrite existing outputs if present.")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("transcribe", help="Run whisper.cpp and derive a compact transcript.json.")
    add_common(p)
    p.add_argument("--model", default=None, help="Whisper ggml model (default: clips/models/ggml-large-v3-turbo.bin).")
    p.add_argument("--vad-model", default=None, help="Silero VAD ggml model (default: clips/models/ggml-silero-v5.1.2.bin).")
    p.add_argument("--language", default="it", help="Whisper language code (default: it).")
    p.add_argument("--threads", type=int, help="Number of CPU threads to pass to whisper-cli.")
    p.add_argument("--no-vad", action="store_true", help="Run without VAD (skips the VAD model requirement).")
    p.add_argument("--force", action="store_true", help="Overwrite existing outputs if present.")
    p.add_argument("--compact-only", action="store_true",
                   help="Skip whisper-cli and rebuild transcript.json from the existing transcript.raw.json.")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("render", help="Print transcript.json as numbered, mm:ss-stamped lines.")
    add_common(p)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("snap", help="Compute cut points for all candidates in highlights.json.")
    add_common(p)
    p.add_argument("--refresh-silence", action="store_true", help="Ignore the cached silences.json and re-run silencedetect.")
    p.set_defaults(func=cmd_snap)

    p = sub.add_parser("preview", help="Render a captioned, padded review file per candidate.")
    add_common(p)
    p.add_argument("--ids", help="Comma-separated candidate ids (default: all).")
    p.add_argument("--pad", type=float, default=PREVIEW_PAD, help=f"Seconds of padding before and after the cut (default: {PREVIEW_PAD}).")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("choose", help="Mark candidates as chosen and optionally adjust their cut points.")
    add_common(p)
    p.add_argument("--ids", help="Comma-separated candidate ids to mark chosen, e.g. 1,3 (all others become "
                                 "unchosen). Omit to leave the chosen flags unchanged while adjusting cuts.")
    p.add_argument("--start", action="append", metavar="ID=TIME",
                   help="Set a candidate's cut start from a time read off its preview file (seconds or M:SS). "
                        "Needs a rendered preview. Repeatable.")
    p.add_argument("--end", action="append", metavar="ID=TIME",
                   help="Set a candidate's cut end from a time read off its preview file (seconds or M:SS). "
                        "Needs a rendered preview. Repeatable.")
    p.add_argument("--start-at", action="append", metavar="ID=WORDS",
                   help="Start the clip at these words, quoted from the transcript, e.g. 2=\"mi sono dimenticato\". "
                        "Lands on a nearby pause when there is one. Repeatable.")
    p.add_argument("--end-after", action="append", metavar="ID=WORDS",
                   help="End the clip right after these words, e.g. 2=\"mila euro\". Repeatable.")
    p.add_argument("--reset", action="append", metavar="ID",
                   help="Return a candidate's cut to its snapped edges, dropping any manual adjustment. Repeatable.")
    p.set_defaults(func=cmd_choose)

    p = sub.add_parser("status", help="Print one block per candidate: id, score, chosen, range, flags, hook, preview.")
    add_common(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("cut", help="Cut every chosen candidate from source.mp4 into final/clip_<id>.mp4.")
    add_common(p)
    p.add_argument("--ids", help="Comma-separated candidate ids. Default: every chosen candidate.")
    p.add_argument("--force", action="store_true", help="Overwrite existing clips.")
    p.set_defaults(func=cmd_cut)

    p = sub.add_parser("align", help="Transcribe cut clip(s) on their own and write an editable words.tsv.")
    add_common(p)
    p.add_argument("--ids", help="Comma-separated candidate ids. Default: every chosen candidate.")
    p.add_argument("--model", help="Whisper ggml model (default: clips/models/ggml-large-v3-turbo.bin).")
    p.add_argument("--language", default="it")
    p.add_argument("--force", action="store_true", help="Overwrite an existing words file (loses edits).")
    p.set_defaults(func=cmd_align)

    p = sub.add_parser("tighten", help="Shorten the pauses inside cut clip(s): writes a plan and final/clip_<id>.tight.mp4.")
    add_common(p)
    p.add_argument("--ids", help="Comma-separated candidate ids. Default: every chosen candidate.")
    p.add_argument("--drop", action="append", metavar="ID=WORDS",
                   help="Remove a stretch of speech, quoted from the words file: "
                        "3=\"first words ... last words\", or one phrase to drop just that. Each edge "
                        "must land on a pause, or the drop is refused. Added to the plan. Repeatable.")
    p.add_argument("--replan", action="store_true",
                   help="Recompute an existing plan (loses its drops and hand edits such as \"apply\": false).")
    p.set_defaults(func=cmd_tighten)

    p = sub.add_parser("burn", help="Build the .ass from the words file and burn it into the final vertical clip.")
    add_common(p)
    p.add_argument("--ids", help="Comma-separated candidate ids. Default: every chosen candidate.")
    p.add_argument("--font", help=f"Font family (default: {DEFAULT_STYLE['font']}).")
    p.add_argument("--size", type=int, help=f"Caption font size (default: {DEFAULT_STYLE['size']}).")
    p.add_argument("--active", help=f"Highlight colour as ASS &HAABBGGRR (default: {DEFAULT_STYLE['active']}).")
    p.add_argument("--keep-case", action="store_true", help="Keep the words as written instead of showing them in upper case.")
    p.add_argument("--crop", metavar="W:H:X:Y",
                   help="Crop the source picture first (source pixels), e.g. 1440:1080:0:0. Remembered in "
                        "highlights.json: for the whole event, or with --ids for just those clips (their "
                        "own crop then wins). Omit to reuse the stored value.")
    p.add_argument("--footer", default="https://mantova.dev", help="Text at the bottom of the canvas (default: https://mantova.dev).")
    p.add_argument("--no-tighten", action="store_true", help="Burn the clip as cut, even if tighten has shortened it.")
    p.set_defaults(func=cmd_burn)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    validate_event(args.event)

    root = repo_root()
    work_dir = work_dir_for(root, args.event, getattr(args, "work_dir", None))

    if args.command == "transcribe":
        if args.model is None:
            args.model = str(root / "clips" / "models" / "ggml-large-v3-turbo.bin")
        if args.vad_model is None:
            args.vad_model = str(root / "clips" / "models" / "ggml-silero-v5.1.2.bin")

    args.func(args, root, work_dir)


if __name__ == "__main__":
    main()
