#!/usr/bin/env python3
"""clips.py: Mantova Dev clipping pipeline, one command-line tool.

Standard library only. Runs on Python 3.9. Needs ffmpeg (with libass) and
whisper-cli on PATH. See clips/README.md for the overview and the SKILL.md files
in clips/skills/ for how and when to run each step. Everything a run produces goes
in clips/work/<event>/ (gitignored).

Subcommands:
  ingest      link or remux the recording to source.mp4, extract audio.wav
  transcribe  run whisper.cpp and derive transcript.json
  render      print the transcript as numbered, mm:ss-stamped lines
  preview     place the cut of new candidates and render a review file
  choose      mark candidates chosen and move their cut edges
  status      print each candidate: range, edges, preview, text
  cut         cut a chosen clip and align its words for the captions
  tighten     shorten long pauses and drop stretches inside a cut clip
  burn        render the final vertical clip with captions
  join        join finished clips (any events) and an end card into a montage

Run `clips.py <subcommand> --help` for each subcommand's options.
"""

import argparse
import json
import math
import os
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
LANGUAGE = "it"         # whisper language of the talks
# transcribe / render / status: a sentence repeated this many times in a row is flagged as
# likely invented (whisper loops over noise, applause or a pause).
SUSPECT_REPEAT = 3

# preview: candidate duration limits (status warns outside them), and edge placement.
TOO_SHORT_S = 15.0      # shorter works only as a piece of a montage
TOO_LONG_S = 60.0       # the finished clip
RAW_TOO_LONG_S = 120.0  # the cut itself may run longer, if tighten then drops stretches from it
MAX_LEAD = 0.35
MAX_TAIL = 0.45
# What counts as a pause (ffmpeg silencedetect). The right noise floor depends on the
# room: raise it (e.g. -30dB) for a noisy room, lower it for a quiet one.
SILENCE_NOISE = "-35dB"
SILENCE_MIN_S = 0.4
PREVIEW_PAD = 5.0
PHRASE_SEARCH_S = 30.0  # --start-at / --end-after look this far around the current cut
PREVIEW_CAPTION_WORDS = 10
# preview / choose: each cut edge is checked against a fresh alignment of the audio around
# it (the same whisper DTW run cut uses): word times in the full transcript are off by
# up to a second, more where whisper split the talk into even one-second segments.
EDGE_PAD = 5.0      # seconds of audio aligned on each side of an edge's rough time
EDGE_NEAR = 3.0     # the edge's words must be found this close to the rough time
EDGE_MATCH = 3      # words matched at that end of the quote (fewer if they are not found)
EDGE_GUARD = 0.05   # with no pause at an edge, the cut sits this far from the next word

# cut: word timing for the captions. DTW token times tend to land a little after the
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
# quiet run found by silencedetect on the clip itself, at the same noise floor as preview.
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

# burn: loudness, in LUFS (the loudness unit the platforms normalize to; -14 is what
# YouTube plays at). Every clip is brought to it, so clips and montage pieces match.
LOUDNESS_TARGET = -14
LOUDNESS_PEAK = -1.5     # true peak ceiling, dBTP

CANVAS_W, CANVAS_H = 1080, 1920   # output is always vertical
LOGO_W = 640             # logo width and top offset on the canvas
LOGO_Y = 150
FOOTER = "https://mantova.dev"   # static text at the bottom of every clip

# join: montage frame rate, audio fades at each join, and the end card layout.
MONTAGE_FPS = 30
JOIN_FADE_IN = 0.08
JOIN_FADE_OUT = 0.12
CARD_S = 3.0
CARD_FADE = 0.3
CARD_LOGO_W, CARD_LOGO_Y = 760, 560
CARD_LINES_Y = 845       # first line; each next one CARD_LINE_STEP lower
CARD_LINE_STEP = 130
CARD_LINK_Y = 1585

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


# ---------------------------------------------------------------- shared helpers

def fail(msg: str) -> NoReturn:
    print(f"clips.py: error: {msg}", file=sys.stderr)
    sys.exit(1)


def repo_root() -> Path:
    # this file lives at <repo>/clips/clips.py
    return Path(__file__).resolve().parents[1]


def run(cmd, **kwargs):
    """Print the command, then run it."""
    print("Running: " + " ".join(str(c) for c in cmd), file=sys.stderr)
    return subprocess.run(cmd, **kwargs)


def work_dir_for(root: Path, event: str, override: Optional[str] = None) -> Path:
    return Path(override).resolve() if override else root / "clips" / "work" / event


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def ffprobe(path: Path, entries: str, streams: Optional[str] = "v:0") -> str:
    """ffprobe's csv output for these entries, of these streams (None: the file)."""
    select = ["-select_streams", streams] if streams else []
    return subprocess.run(["ffprobe", "-v", "error", *select, "-show_entries", entries, "-of", "csv=p=0",
                           str(path)], capture_output=True, text=True).stdout.strip()


def ffprobe_duration(path: Path) -> float:
    return float(ffprobe(path, "format=duration", None))


def ffprobe_size(path: Path):
    width, height = ffprobe(path, "stream=width,height").split(",")[:2]
    return int(width), int(height)


def ffprobe_fps(path: Path) -> str:
    """Frame rate of the first video stream as ffmpeg prints it, e.g. "30/1" or "30000/1001"."""
    return ffprobe(path, "stream=r_frame_rate").split(",")[0]


def ffprobe_video(path: Path):
    """(fps, start time, frame count) of the first video stream. The start time matters:
    a cut clip's first frame often sits one frame after the audio start."""
    rate, start, frames = ffprobe(path, "stream=r_frame_rate,start_time,nb_frames").split(",")[:3]
    num, den = (int(x) for x in rate.split("/"))
    return num / den, float(start), int(frames)


def mmss(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def clock(seconds: float) -> str:
    """M:SS.s, used for times read off the preview player."""
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def parse_clock(text: str) -> float:
    """Plain seconds ("63.5") or M:SS(.s) ("1:03.5")."""
    minutes, _, seconds = text.strip().rpartition(":")
    return int(minutes or 0) * 60 + float(seconds)


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
    """source.mp4 (a symlink to an .mp4 source, else a stream-copy remux) and audio.wav
    (one audio track, 16 kHz mono), plus ingest.json saying where they came from."""
    source = Path(args.source).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    out_mp4, out_wav = work_dir / "source.mp4", work_dir / "audio.wav"
    out_mp4.unlink(missing_ok=True)
    if source.suffix.lower() == ".mp4":
        out_mp4.symlink_to(source)
    elif run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
              "-c", "copy", "-map", "0", "-movflags", "+faststart", str(out_mp4)]).returncode:
        fail("remux to mp4 failed")

    print("Audio streams in source (pick a mic-only one with --audio-track):")
    print(ffprobe(source, "stream=index,codec_name,channels:stream_tags=title", "a"))
    if run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-map",
            f"0:a:{args.audio_track}", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)]).returncode:
        fail(f"audio extraction failed for track a:{args.audio_track}")

    # A recording can hold an audio track shorter than the video: transcribing it silently
    # loses the rest of the talk.
    duration, source_duration = ffprobe_duration(out_wav), ffprobe_duration(source)
    if abs(duration - source_duration) > 1.0:
        fail(f"audio.wav lasts {duration:.1f}s, the source {source_duration:.1f}s: try another --audio-track")
    save_json(work_dir / "ingest.json", {"source": str(source), "audio_track": args.audio_track,
                                         "duration": round(duration, 2)})
    print(f"Wrote {out_mp4} and {out_wav} ({duration:.1f}s)")


# ==================================================================
# transcribe (whisper-cli, then the compact transcript)
# ==================================================================

def ms_to_s(value_ms) -> float:
    return round(value_ms / 1000.0, 2)


SPECIAL_TOKEN_RE = re.compile(r"^\[_.*\]$")


def seg_shift(seg) -> float:
    """VAD timeline fix: with --vad, whisper-cli (as of 1.9.4) reports segment offsets on
    the original audio timeline but token offsets on the VAD-compressed timeline (silence
    removed), so token times fall behind by all the silence removed before them. Shift
    each segment's tokens so its first real token lands on the segment start. Silence
    removed INSIDE a segment is not recovered, so words after an internal pause can be
    early by the length of that pause."""
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


def build_compact(raw: dict):
    segs = raw.get("transcription") or []

    raw_words = []
    for segidx, seg in enumerate(segs):
        shift = seg_shift(seg)
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

    return {"segments": segments, "words": words}


def cmd_transcribe(args, root: Path, work_dir: Path):
    """whisper-cli with VAD on audio.wav, then the compact transcript.json. Without VAD,
    whisper fragments the segments, loses punctuation and invents text over silence."""
    raw_json = work_dir / "transcript.raw.json"
    if not args.compact_only:
        vad_model = root / "clips" / "models" / "ggml-silero-v5.1.2.bin"
        if run(["whisper-cli", "-m", str(default_model(root)), "-l", LANGUAGE, "-ojf",
                "-of", str(work_dir / "transcript.raw"), "-f", str(work_dir / "audio.wav"),
                "--vad", "--vad-model", str(vad_model)]).returncode:
            fail("whisper-cli failed")
    compact = build_compact(load_json(raw_json))
    save_json(work_dir / "transcript.json", compact)
    print(f"Wrote {work_dir / 'transcript.json'} ({len(compact['segments'])} segments)")
    suspect = suspect_ranges(compact["segments"])
    starts = {seg["i"]: seg["s"] for seg in compact["segments"]}
    print(f"Likely invented text (render marks it with ~): {len(suspect)} stretch(es)")
    for first, last, why in suspect:
        print(f"  [{first}] to [{last}] from {mmss(starts[first])}: {why}")
    print("Also read the first and last minutes: whisper can invent text over silence or applause.")


def suspect_ranges(segments):
    """[(first i, last i, why)] for stretches of the transcript that are likely invented:
    the same sentence over and over, or a run of back-to-back segments lasting whole
    seconds (whisper's filler timing when it hears no words, e.g. over applause or noise)."""
    segs = sorted(segments, key=lambda g: g["i"])
    out = []

    def runs(test, min_len, why):
        start = None
        for n in range(len(segs) + 1):
            if n < len(segs) and test(n):
                start = n if start is None else start
                continue
            if start is not None and n - start >= min_len:
                out.append((segs[start]["i"], segs[n - 1]["i"], why))
            start = None

    runs(lambda n: n > 0 and normalize(segs[n]["text"]) == normalize(segs[n - 1]["text"])
         or n + 1 < len(segs) and normalize(segs[n]["text"]) == normalize(segs[n + 1]["text"]),
         SUSPECT_REPEAT, "the same sentence repeated")
    return sorted(out)


# ==================================================================
# render
# ==================================================================

def cmd_render(args, root: Path, work_dir: Path):
    """One line per segment, `[i] mm:ss text`; `[i]~` marks likely invented text."""
    segs = load_json(work_dir / "transcript.json")["segments"]
    suspect = {i for first, last, _ in suspect_ranges(segs) for i in range(first, last + 1)}
    for seg in segs:
        print(f"[{seg['i']}]{'~' if seg['i'] in suspect else ''} {mmss(math.floor(seg['s']))} {seg['text']}")


# ==================================================================
# highlight-selection (preview / choose / status)
# ==================================================================

def segments_by_index(transcript):
    return {seg["i"]: seg for seg in transcript["segments"]}


def words_for_segment(transcript, seg_i):
    return [w for w in transcript["words"] if w["seg"] == seg_i]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def norm_word(word: str) -> str:
    """A word for matching: lower case, punctuation stripped (apostrophes stay)."""
    return re.sub(r"[^\w']+", "", word.lower())


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


def silences(work_dir: Path):
    """Pauses in audio.wav at SILENCE_NOISE, cached in silences.json (a full scan takes a while)."""
    cache = work_dir / "silences.json"
    if not cache.is_file():
        save_json(cache, {"intervals": detect_silences(work_dir / "audio.wav")})
    return load_json(cache)["intervals"]


def set_edge(cut, edge: str, time: float, kind: str):
    cut[edge] = round(max(0.0, time), 2)
    cut["edges"][edge] = kind
    cut["duration"] = round(cut["end"] - cut["start"], 2)


def compute_cut(cand, transcript, work_dir: Path, model: Path):
    """The cut for a candidate: each edge placed on its quote's words, in a fresh alignment
    of the audio around it (see refine_edge); where the words are not found, just outside
    the segment ("rough")."""
    segs = segments_by_index(transcript)
    first, last = segs[cand["start_seg"]], segs[cand["end_seg"]]
    if (normalize(cand["start_quote"]) != normalize(first["text"])
            or normalize(cand["end_quote"]) != normalize(last["text"])):
        fail(f"candidate {cand['id']}: start_quote and end_quote must be the exact text of segments "
             f"{cand['start_seg']} and {cand['end_seg']}")
    last_words = words_for_segment(transcript, cand["end_seg"])
    cut = {"segs": [cand["start_seg"], cand["end_seg"]], "start": 0.0, "end": 0.0, "edges": {}}
    for edge, rough, fallback, quote in (
            ("start", first["s"], first["s"] - 0.2, cand["start_quote"]),
            ("end", last_words[-1]["s"] if last_words else last["e"], last["e"] + 0.2, cand["end_quote"])):
        set_edge(cut, edge, *(refine_edge(work_dir, rough, quote, edge, model) or (fallback, "rough")))
    return cut


def duration_note(duration: float) -> str:
    if duration < TOO_SHORT_S:
        return f", under {TOO_SHORT_S:.0f}s: works only as a piece of a montage"
    if duration > RAW_TOO_LONG_S:
        return f", over {RAW_TOO_LONG_S:.0f}s: too long, narrow the span"
    if duration > TOO_LONG_S:
        return f", over {TOO_LONG_S:.0f}s: needs tighten --drop"
    return ""


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


def render_preview(cand, transcript, work_dir: Path, pad: float):
    cut = cand["cut"]
    origin = max(0.0, cut["start"] - pad)
    total = round(cut["end"] + pad - origin, 2)
    previews_dir = work_dir / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    name = f"candidate_{cand['id']}"
    (previews_dir / f"{name}.srt").write_text(build_preview_srt(cut, transcript, origin, total), encoding="utf-8")
    # Run inside previews_dir so the subtitles filter gets a bare file name (no escaping).
    if run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{origin:.2f}", "-t", f"{total:.2f}", "-i", str((work_dir / "source.mp4").resolve()),
            "-vf", f"scale=-2:480,subtitles={name}.srt:force_style='FontSize=20,Outline=2,MarginV=24'",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", f"{name}.mp4"], cwd=previews_dir).returncode:
        fail(f"ffmpeg failed rendering the preview of candidate {cand['id']}")
    cut["preview"] = {"file": f"previews/{name}.mp4", "origin": round(origin, 2)}


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


def phrase_edge(work_dir: Path, transcript, cut, phrase: str, edge: str, model: Path):
    """(time, kind) for an edge given by words quoted from the transcript: the start of
    the phrase or the end of it, searched around the current cut."""
    words = [w for w in transcript["words"]
             if cut["start"] - PHRASE_SEARCH_S <= w["s"] <= cut["end"] + PHRASE_SEARCH_S]
    hits, n = find_words(words, phrase)
    if not hits:
        fail(f"\"{phrase}\" not found near this clip: copy the words exactly from the transcript")
    w = words[hits[0]] if edge == "start" else words[hits[0] + n - 1]
    fallback = w["s"] - 0.15 if edge == "start" else w["e"] + 0.15
    return refine_edge(work_dir, w["s"], phrase, edge, model) or (fallback, "rough")


EDGE_KINDS = {
    "hand": "set by hand",
    "pause": "on a pause",
    "tight": "on the words, no pause there (check the preview)",
    "rough": "NOT checked against the words (check the preview)",
}


def default_model(root: Path) -> Path:
    return root / "clips" / "models" / "ggml-large-v3-turbo.bin"


def edge_window(work_dir: Path, t0: float, t1: float, model: Path):
    """Words (DTW start times) and quiet runs of the source audio between t0 and t1, in
    source seconds. Quiet runs come at SILENCE_NOISE, then at each louder floor, like
    tighten --drop uses them. Cached in edges/, so re-running preview or choose does not run
    whisper again."""
    t0, t1 = max(0.0, round(t0, 1)), round(t1, 1)
    cache_dir = work_dir / "edges"
    name = f"{t0:.1f}-{t1:.1f}"
    cache = cache_dir / f"{name}.json"
    if cache.is_file():
        return load_json(cache)
    cache_dir.mkdir(parents=True, exist_ok=True)
    wav = cache_dir / f"{name}.wav"
    proc = run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t0:.2f}",
                "-t", f"{t1 - t0:.2f}", "-i", str(work_dir / "audio.wav"), "-c:a", "pcm_s16le", str(wav)])
    if proc.returncode != 0:
        fail(f"ffmpeg failed extracting {name} from audio.wav")
    raw_prefix = cache_dir / f"{name}.raw"
    quiet = [[{"start": r["start"] + t0, "end": r["end"] + t0}
              for r in merge_quiet_runs(detect_silences(wav, TIGHT_DETECT_S, noise), TIGHT_MERGE_S)]
             for noise in (SILENCE_NOISE,) + DROP_NOISE_LADDER]
    words = []
    for w in dtw_words(model, wav, raw_prefix):
        word = {"w": w["w"], "s": w["s"] + t0, "e": w["e"] + t0}
        # The first word of a whisper segment starts at the segment start, which often
        # sits at the start of the pause before the word; its DTW time runs late. When a
        # pause starts right at the segment start, the word starts where that pause ends.
        if "dtw" in w:
            dtw = w["dtw"] + t0
            gaps = [r for runs in quiet for r in runs
                    if abs(r["start"] - word["s"]) <= 0.2 and r["end"] <= dtw + 0.3]
            if gaps:
                word["s"] = min(max(r["end"] for r in gaps), dtw)
        words.append(word)
    data = {"words": words, "quiet": quiet}
    save_json(cache, data)
    wav.unlink()
    Path(str(raw_prefix) + ".json").unlink(missing_ok=True)
    return data


def locate_words(words, phrase: str, edge: str, near: float):
    """Index in words of the first (edge "start") or last (edge "end") word of phrase.
    Matches up to EDGE_MATCH words at that end of the phrase, then fewer, down to one word
    if it is long enough not to be a filler ("e", "di", "che"), taking the hit nearest to
    near. The two transcriptions often differ by a word, hence the fallback. None if not
    found."""
    target = [norm_word(w) for w in phrase.split() if norm_word(w)]
    normed = [norm_word(w["w"]) for w in words]
    for k in range(min(EDGE_MATCH, len(target)), 0, -1):
        part = target[:k] if edge == "start" else target[-k:]
        if k == 1 and len(target) > 1 and len(part[0]) < 4:
            break
        hits = [i for i in range(len(normed) - k + 1) if normed[i:i + k] == part]
        if edge == "end":
            hits = [i + k - 1 for i in hits]
        hits = [i for i in hits if abs(words[i]["s"] - near) <= EDGE_NEAR]
        if hits:
            return min(hits, key=lambda i: abs(words[i]["s"] - near))
    return None


def place_edge(data, i: int, edge: str):
    """(source time, "pause" or "tight") for a cut edge at words[i]: the start of that word
    or the end of it. On a pause between it and its neighbour when there is one, at the
    quietest floor that shows one; otherwise tight against the neighbouring word."""
    words = data["words"]
    if edge == "start":
        t = words[i]["s"]
        lo = words[i - 1]["s"] + 0.15 if i > 0 else t - EDGE_PAD
        for runs in data["quiet"]:
            near = [r for r in runs if r["end"] > lo and t - 0.5 <= r["end"] and r["start"] < t + 0.1]
            if near:
                r = max(near, key=lambda r: r["end"] - max(r["start"], lo))
                return r["end"] - min(MAX_LEAD, (r["end"] - max(r["start"], lo)) / 2), "pause"
        return max(0.0, t - EDGE_GUARD), "tight"
    s = words[i]["s"]
    nxt = words[i + 1]["s"] if i + 1 < len(words) else None
    hi = nxt if nxt is not None else s + EDGE_PAD
    # A next word that starts before this one can be over has a DTW time that ran early:
    # look for the pause up to the word after it.
    if nxt is not None and nxt < s + 0.3 + 0.07 * len(words[i]["w"]) and i + 2 < len(words):
        hi = words[i + 2]["s"]
    for runs in data["quiet"]:
        near = [r for r in runs if s + 0.15 <= r["start"] < hi]
        if near:
            r = max(near, key=lambda r: min(r["end"], hi) - r["start"])
            return r["start"] + min(MAX_TAIL, (min(r["end"], hi) - r["start"]) / 2), "pause"
    if nxt is not None:
        return nxt - EDGE_GUARD, "tight"
    return None


def refine_edge(work_dir: Path, rough: float, phrase: str, edge: str, model: Path):
    """(source time, "pause" or "tight") for an edge at the start or end of phrase, whose
    rough time comes from the full transcript; None when the words are not found."""
    # The window starts and ends on a pause when there is one near: whisper aligns a
    # stretch that starts mid-sentence much worse.
    t0, t1 = rough - EDGE_PAD, rough + EDGE_PAD
    intervals = silences(work_dir)
    before = [iv["end"] for iv in intervals if rough - EDGE_PAD - 3 <= iv["end"] <= rough - 2]
    after = [iv["start"] for iv in intervals if rough + 2 <= iv["start"] <= rough + EDGE_PAD + 3]
    if before:
        t0 = max(before) - 0.2
    if after:
        t1 = min(after) + 0.2
    data = edge_window(work_dir, t0, t1, model)
    i = locate_words(data["words"], phrase, edge, rough)
    return None if i is None else place_edge(data, i, edge)


def print_status(highlights, event, transcript, ids=None):
    """One block per candidate (only those in ids, when given): id, chosen or not, note,
    segment range, duration, how each cut edge was placed, where the clip sits in its
    preview, and the text inside the cut plus one sentence either side."""
    print(f"Highlight candidates: {event}")
    print()
    suspect = suspect_ranges(transcript["segments"])
    for cand in sorted(highlights["candidates"], key=lambda c: c["id"]):
        if ids is not None and cand["id"] not in ids:
            continue
        cut = cand.get("cut")
        print(f"Candidate {cand['id']}{' [CHOSEN]' if cand.get('chosen') else ''}")
        if cand.get("note"):
            print(f"  note: {cand['note']}")
        print(f"  segments: {cand['start_seg']} to {cand['end_seg']}")
        for first, last, why in suspect:
            if first <= cand["end_seg"] and cand["start_seg"] <= last:
                print(f"  warning: segments {first} to {last} look invented ({why}): check the preview")
        if cut:
            print(f"  range: {mmss(cut['start'])} to {mmss(cut['end'])} ({cut['duration']}s{duration_note(cut['duration'])})")
            print(f"  cut edges: start {EDGE_KINDS[cut['edges']['start']]}, end {EDGE_KINDS[cut['edges']['end']]}")
            if cut.get("preview"):
                origin = cut["preview"]["origin"]
                print(f"  preview: {cut['preview']['file']}, the clip runs from "
                      f"{clock(cut['start'] - origin)} to {clock(cut['end'] - origin)} in the player")
        print("  transcript (what is inside the cut, plus one sentence of context either side):")
        for label, seg in candidate_segments(cand, transcript):
            print(f"    {label:9}[{seg['i']}] {seg['text']}")
        print()


def load_selection(work_dir: Path):
    return load_json(work_dir / "highlights.json"), load_json(work_dir / "transcript.json")


def parse_ids(text) -> set:
    return {int(x) for x in (text or "").split(",") if x.strip()}


def id_pairs(values):
    """["2=mila euro", ...] -> [(2, "mila euro"), ...]"""
    return [(int(cid), value.strip()) for cid, _, value in (v.partition("=") for v in values or [])]


def cmd_preview(args, root: Path, work_dir: Path):
    """Compute the cut of every new or re-spanned candidate and render its preview (and
    the preview of the --ids candidates)."""
    highlights, transcript = load_selection(work_dir)
    wanted = parse_ids(args.ids)
    for cand in highlights["candidates"]:
        if cand.get("cut", {}).get("segs") != [cand["start_seg"], cand["end_seg"]]:
            cand["cut"] = compute_cut(cand, transcript, work_dir, default_model(root))
            wanted.add(cand["id"])
    for cand in highlights["candidates"]:
        if cand["id"] in wanted:
            render_preview(cand, transcript, work_dir, args.pad)
    save_json(work_dir / "highlights.json", highlights)
    print_status(highlights, args.event, transcript, wanted)


def cmd_choose(args, root: Path, work_dir: Path):
    """Mark candidates chosen or not, and move cut edges: to a time read off the preview
    player, or onto words quoted from the transcript. Moved edges re-render the preview."""
    highlights, transcript = load_selection(work_dir)
    cands = {c["id"]: c for c in highlights["candidates"]}
    model = default_model(root)
    touched, moved = parse_ids(args.ids) | parse_ids(args.unchoose), set()
    for cid in touched:
        cands[cid]["chosen"] = cid in parse_ids(args.ids)
    for cid in parse_ids(args.reset):
        cands[cid]["cut"] = compute_cut(cands[cid], transcript, work_dir, model)
        moved.add(cid)
    for edge, times, phrases in (("start", args.start, args.start_at), ("end", args.end, args.end_after)):
        for cid, value in id_pairs(times):
            cut = cands[cid]["cut"]
            set_edge(cut, edge, cut["preview"]["origin"] + parse_clock(value), "hand")
            moved.add(cid)
        for cid, phrase in id_pairs(phrases):
            cut = cands[cid]["cut"]
            set_edge(cut, edge, *phrase_edge(work_dir, transcript, cut, phrase, edge, model))
            moved.add(cid)
    for cid in moved:
        render_preview(cands[cid], transcript, work_dir, PREVIEW_PAD)
    save_json(work_dir / "highlights.json", highlights)
    print_status(highlights, args.event, transcript, touched | moved)


def cmd_status(args, root: Path, work_dir: Path):
    highlights, transcript = load_selection(work_dir)
    print_status(highlights, args.event, transcript, parse_ids(args.ids) or None)


# ==================================================================
# cut
# ==================================================================

def cmd_cut(args, root: Path, work_dir: Path):
    """Cut each clip from source.mp4 and align its words for the captions (glossary applied)."""
    cands = {c["id"]: c for c in load_json(work_dir / "highlights.json")["candidates"]}
    glossary = load_glossary(root)
    for cid in clip_ids(args, work_dir):
        final, base = clip_paths(work_dir, cid)
        final.mkdir(parents=True, exist_ok=True)
        out = base.with_suffix(".mp4")
        if out.exists() and not args.force:
            fail(f"{out} already exists: --force overwrites it and its words file (hand edits included)")
        cut = cands[cid]["cut"]
        # -ss before -i plus a re-encode gives a frame-accurate cut. Never stream-copy here.
        if run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                "-ss", str(cut["start"]), "-t", str(cut["duration"]), "-i", str(work_dir / "source.mp4"),
                "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]).returncode:
            fail(f"ffmpeg failed cutting candidate {cid}")
        print(f"Wrote {out}")
        align_clip(base, default_model(root), glossary)


# ==================================================================
# produce-clip (cut is above; word alignment, tighten, burn)
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
                dtw_start = start
                if first_in_seg:
                    start = seg_start
                    first_in_seg = False
                cur = {"w": text, "s": start}
                if start != dtw_start:
                    cur["dtw"] = dtw_start  # kept for edge_window, which checks it against the pauses
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


def dtw_words(model: Path, wav: Path, raw_prefix: Path):
    """Words with DTW start times for a 16 kHz mono wav, in seconds from its start.
    No VAD on purpose: with VAD, token times lose the removed silence. DTW needs flash
    attention off. Writes <raw_prefix>.json."""
    proc = run(["whisper-cli", "-m", str(model), "-l", LANGUAGE, "-ojf",
                "-dtw", dtw_preset(model), "-nfa", "-of", str(raw_prefix), "-f", str(wav)],
               capture_output=True, text=True)
    if proc.returncode:
        fail(f"whisper-cli failed: {proc.stderr.strip()[-400:]}")
    return words_from_raw(load_json(Path(str(raw_prefix) + ".json")))


def clip_paths(work_dir: Path, clip_id: int):
    final = work_dir / "final"
    base = final / f"clip_{clip_id}"
    return final, base


def load_glossary(root: Path):
    """[(wrong words, right text)] from clips/config/glossary.tsv, longest first: names
    whisper keeps getting wrong, fixed in every words file cut writes."""
    lines = (root / "clips" / "config" / "glossary.tsv").read_text(encoding="utf-8").splitlines()
    entries = [(wrong.split(), right) for wrong, _, right in
               (line.partition("\t") for line in lines if line.strip() and not line.startswith("#"))]
    return [([norm_word(w) for w in wrong], right) for wrong, right in sorted(entries, key=lambda e: -len(e[0]))]


def apply_glossary(words, glossary):
    """Each glossary match becomes one entry spanning the words it replaces (it may hold a
    space, e.g. "Mantova Dev"), keeping their trailing punctuation."""
    normed = [norm_word(w["w"]) for w in words]
    out, i = [], 0
    while i < len(words):
        hit = next(((len(wrong), right) for wrong, right in glossary if normed[i:i + len(wrong)] == wrong), None)
        if hit is None:
            out.append(words[i])
            i += 1
            continue
        k, right = hit
        tail = re.search(r"[^\w']*$", words[i + k - 1]["w"]).group()
        out.append({"w": right + tail, "s": words[i]["s"], "e": words[i + k - 1]["e"]})
        i += k
    return out


def align_clip(base: Path, model: Path, glossary):
    """Transcribe the cut clip on its own (DTW word starts) into <base>.words.tsv."""
    wav = Path(str(base) + ".align.wav")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(base.with_suffix(".mp4")),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
    words = apply_glossary(dtw_words(model, wav, Path(str(base) + ".align")), glossary)
    wav.unlink()
    words_path = Path(str(base) + ".words.tsv")
    words_path.write_text("".join(f"{w['s']:.2f}\t{w['e']:.2f}\t{w['w']}\n" for w in words), encoding="utf-8")
    print(f"Wrote {words_path}")


def clip_ids(args, work_dir):
    """--ids, else every chosen candidate."""
    if args.ids:
        return sorted(parse_ids(args.ids))
    return sorted(c["id"] for c in load_json(work_dir / "highlights.json")["candidates"] if c.get("chosen"))


def read_words(path: Path):
    """The words file: start<TAB>end<TAB>word per line."""
    rows = [line.split("\t") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [{"s": float(start), "e": float(end), "w": word.strip()} for start, end, word in rows]


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
    Quiet runs touching the clip edges are left alone, those belong to preview and choose."""
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
        })
    return cuts


def find_words(words, phrase: str, start: int = 0):
    """Indices where the phrase begins in the words list, and its length in words."""
    target = [norm_word(w) for w in phrase.split() if norm_word(w)]
    normed = [norm_word(w["w"]) for w in words]
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
    }


def dropped_words(cuts):
    """Indices of the words that applied drops remove."""
    gone = set()
    for cut in cuts:
        if cut["reason"] == "drop":
            gone.update(range(cut["from_index"], cut["to_index"] + 1))
    return gone


def keep_ranges(cuts, frames: int):
    """Frame ranges [a, b) that survive the applied cuts, in order."""
    keeps, pos = [], 0
    for cut in sorted(cuts, key=lambda c: c["start_frame"]):
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
    tight = sum(b - a for a, b in keeps) / fps
    removed = duration - tight
    print(f"Clip {clip_id}: {duration:.1f} s -> {tight:.1f} s "
          f"({removed:.1f} s out, {100 * removed / duration:.0f}%), {len(plan['cuts'])} cuts")
    for n, cut in enumerate(plan["cuts"], 1):
        length = cut["end"] - cut["start"]
        if cut["reason"] == "drop":
            state = f"dropped \"{cut['text']}\", {length:.1f} s out"
        else:
            state = f"pause {cut['pause']:.2f} s -> {cut['pause'] - length:.2f} s"
        print(f"  [{n}] {clock(cut['start'])} after \"{cut['after_word']}\": {state}")
    marks = {}
    for n, cut in enumerate(plan["cuts"], 1):
        marks.setdefault(cut["after"], []).append(f"[{n}]")
    gone = dropped_words(plan["cuts"])
    text = " ".join(marks.get(None, []))
    for i, word in enumerate(words):
        if i not in gone:
            text += " " + " ".join([word["w"]] + marks.get(i, []))
    print(textwrap.fill(text.strip(), width=100, initial_indent="  ", subsequent_indent="  "))
    if tight > TOO_LONG_S:
        print(f"  warning: still over {TOO_LONG_S:.0f} s. Drop more, or move the clip's start or end with choose.")


def cmd_tighten(args, root: Path, work_dir: Path):
    """Plan the cuts (long pauses shortened, --drop stretches removed) from scratch, write
    the plan and render the tightened clip, without captions."""
    drops = id_pairs(args.drop)
    for clip_id in clip_ids(args, work_dir):
        final, base = clip_paths(work_dir, clip_id)
        clip = base.with_suffix(".mp4")
        words = read_words(Path(str(base) + ".words.tsv"))
        fps, video_start, frames = ffprobe_video(clip)
        levels = [merge_quiet_runs(detect_silences(clip, TIGHT_DETECT_S, noise), TIGHT_MERGE_S)
                  for noise in (SILENCE_NOISE,) + DROP_NOISE_LADDER]
        cuts = plan_pauses(words, levels[0], ffprobe_duration(clip), fps, video_start)
        for cid, spec in drops:
            if cid != clip_id:
                continue
            drop = plan_drop(words, levels, fps, video_start, spec, clip_id)
            # The drop takes over any cut it overlaps, such as a pause inside the dropped stretch.
            cuts = sorted([c for c in cuts if c["end_frame"] <= drop["start_frame"]
                           or c["start_frame"] >= drop["end_frame"]] + [drop], key=lambda c: c["start_frame"])
        plan = {"fps": fps, "video_start": video_start, "frames": frames, "words": len(words), "cuts": cuts}
        save_json(Path(str(base) + ".tighten.json"), plan)
        keeps = keep_ranges(cuts, frames)
        print_tighten_report(clip_id, words, plan, keeps)
        if len(keeps) < 2:
            print("  nothing to tighten: burn will use the clip as it is.")
            continue
        graph_name, out_name = f"clip_{clip_id}.tighten.graph", f"clip_{clip_id}.tight.mp4"
        (final / graph_name).write_text(tighten_graph(keeps, fps, video_start), encoding="utf-8")
        # One decode, one encode, run inside final/ like burn. The graph goes in a file:
        # a long clip has dozens of pieces.
        if run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", clip.name,
                "-/filter_complex", graph_name, "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_name], cwd=final).returncode:
            fail(f"ffmpeg failed tightening clip {clip_id}")
        print(f"Wrote {final / out_name}")


def tightened_inputs(base: Path, clip: Path, words):
    """(clip, words) for burn: the tightened pair when tighten has shortened something."""
    plan_path = Path(str(base) + ".tighten.json")
    if not plan_path.is_file():
        return clip, words
    plan = load_json(plan_path)
    keeps = keep_ranges(plan["cuts"], plan["frames"])
    if len(keeps) < 2:
        return clip, words
    if plan["words"] != len(words):
        fail("the words file gained or lost a line since tighten: run tighten again (with the drops)")
    return (Path(str(base) + ".tight.mp4"),
            remap_words(words, keeps, plan["fps"], plan["video_start"], dropped_words(plan["cuts"])))


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


def ass_header(*styles) -> str:
    """An .ass file header for the vertical canvas. Each style is (name, font, size, colour,
    outline, shadow, alignment, margin_v). Aeonik Pro Bold is not a family of its own but
    the bold of Aeonik Pro, so a font name ending in " Bold" sets the bold flag instead."""
    lines = [f"Style: {name},{font.removesuffix(' Bold')},{size},{colour},{colour},&H00000000,&H80000000,"
             f"{-1 if font.endswith(' Bold') else 0},0,0,0,100,100,0,0,1,{outline},{shadow},{align},60,60,{margin_v},1"
             for name, font, size, colour, outline, shadow, align, margin_v in styles]
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {CANVAS_W}
PlayResY: {CANVAS_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{chr(10).join(lines)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(words, style, caption_top: int, footer: str, duration: float) -> str:
    """Captions hang from y = caption_top (below the picture); footer is static text
    at the bottom of the canvas for the whole clip."""
    header = ass_header(
        ("Cap", style["font"], style["size"], style["primary"], style["outline"], style["shadow"], 8, int(caption_top)),
        ("Footer", style["footer_font"], style["footer_size"], style["active"], 0, 0, 2, style["footer_margin_v"]))
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


def clip_crops(work_dir: Path, given, ids):
    """{clip id: crop or None}. A crop belongs to a clip, because the speaker and the
    camera can move between clips: --crop is stored on every candidate this run burns
    ("none" clears it) and later burns of that clip reuse it. No crop shows the whole frame."""
    highlights = load_json(work_dir / "highlights.json")
    cands = {c["id"]: c for c in highlights["candidates"]}
    if given:
        for i in ids:
            cands[i].pop("crop", None)
            if given != "none":
                cands[i]["crop"] = given
        save_json(work_dir / "highlights.json", highlights)
    return {i: cands[i].get("crop") for i in ids}


def _burn_one(root, work_dir, clip_id, crop):
    final, base = clip_paths(work_dir, clip_id)
    clip, words = tightened_inputs(base, base.with_suffix(".mp4"), read_words(Path(str(base) + ".words.tsv")))
    style = DEFAULT_STYLE
    width, height = ffprobe_size(clip)
    encode = ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]

    # Vertical 1080x1920: brand background, logo on top, the (optionally cropped) picture
    # at full width, captions in the free space below it, link at the bottom.
    logo = root / "clips" / "config" / "brand" / "logo-dark-bg.png"
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
        # preview lets a cut run to RAW_TOO_LONG_S on the promise that tighten shortens it
        print(f"warning: clip {clip_id} is {duration:.0f} s, over {TOO_LONG_S:.0f} s. Drop stretches with tighten first.")
    (final / ass_name).write_text(
        build_ass(words, style, caption_top=video_y + video_h + 120,
                  footer=FOOTER, duration=duration),
        encoding="utf-8-sig")
    graph = (
        f"[0:v]{crop_filter},scale={CANVAS_W}:{video_h}[v];"
        f"color=c=0x{style['background']}:s={CANVAS_W}x{CANVAS_H}:r={ffprobe_fps(clip)}[bg];"
        f"[1:v]scale={LOGO_W}:-1[lg];"
        f"[bg][v]overlay=0:{video_y}:shortest=1[a];"
        f"[a][lg]overlay=(W-w)/2:{LOGO_Y}[b];"
        f"[b]ass={ass_name}[out];"
        f"[0:a]{loudnorm_filter(clip)}[au]"
    )
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", clip.name, "-i", str(logo),
           "-filter_complex", graph, "-map", "[out]", "-map", "[au]"] + encode + [out_name]

    # Run inside final/ so the ass filter gets a bare file name (no escaping needed).
    proc = run(cmd, cwd=final)
    if proc.returncode != 0:
        fail("ffmpeg failed burning the captions (is the ass filter available? use ffmpeg-full on macOS)")
    print(f"Wrote {final / out_name}")


def loudnorm_filter(clip: Path) -> str:
    """Two-pass loudness normalization to LOUDNESS_TARGET, so every clip, and every piece
    of a montage, plays equally loud: this measures the clip, the returned filter applies
    one gain, or ffmpeg's dynamic mode where one gain would push the peaks over
    LOUDNESS_PEAK."""
    target = f"I={LOUDNESS_TARGET}:TP={LOUDNESS_PEAK}:LRA=11"
    proc = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(clip), "-vn",
                "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"],
               capture_output=True, text=True)
    found = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr)
    if proc.returncode != 0 or not found:
        fail(f"could not measure the loudness of {clip}")
    got = json.loads(found[-1])
    return (f"loudnorm={target}:measured_I={got['input_i']}:measured_TP={got['input_tp']}:"
            f"measured_LRA={got['input_lra']}:measured_thresh={got['input_thresh']}:"
            f"offset={got['target_offset']}:linear=true,aresample=48000")


def cmd_burn(args, root: Path, work_dir: Path):
    ids = clip_ids(args, work_dir)
    crops = clip_crops(work_dir, args.crop, ids)
    for clip_id in ids:
        _burn_one(root, work_dir, clip_id, crops[clip_id])


# ==================================================================
# join (montage of finished clips, from one or more events)
# ==================================================================

def build_card(out_dir: Path, card, root: Path) -> Path:
    """card.mp4: CARD_S of brand background, logo, the card's lines (the last one in the
    accent colour), an optional subtitle and the link, with silent audio."""
    st = DEFAULT_STYLE
    lines = card["lines"]
    events = [(CARD_LINES_Y + n * CARD_LINE_STEP, "Line", ("{\\c%s}" % st["active"] if n == len(lines) - 1 else "") + text)
              for n, text in enumerate(lines)]
    if card.get("subtitle"):
        events.append((CARD_LINES_Y + len(lines) * CARD_LINE_STEP + 80, "Sub", card["subtitle"]))
    events.append((CARD_LINK_Y, "Footer", card.get("link", FOOTER)))
    ass = ass_header(("Line", st["font"], 120, st["primary"], 0, 0, 8, 0),
                     ("Sub", "Aeonik Pro", 60, st["primary"], 0, 0, 8, 0),
                     ("Footer", st["footer_font"], 84, st["active"], 0, 0, 8, 0))
    ass += "".join(f"Dialogue: 0,{ass_time(0)},{ass_time(CARD_S)},{name},,0,0,0,,{{\\an8\\pos({CANVAS_W // 2},{y})}}{text}\n"
                   for y, name, text in events)
    (out_dir / "card.ass").write_text(ass, encoding="utf-8-sig")
    graph = (f"color=c=0x{st['background']}:s={CANVAS_W}x{CANVAS_H}:r={MONTAGE_FPS}:d={CARD_S}[bg];"
             f"[0:v]scale={CARD_LOGO_W}:-1[lg];[bg][lg]overlay=(W-w)/2:{CARD_LOGO_Y}[b];"
             f"[b]ass=card.ass,fade=t=in:d={CARD_FADE}[v]")
    if run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(root / "clips" / "config" / "brand" / "logo-dark-bg.png"),
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-filter_complex", graph,
            "-map", "[v]", "-map", "1:a", "-t", str(CARD_S), "-c:v", "libx264", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "card.mp4"], cwd=out_dir).returncode:
        fail("ffmpeg failed drawing the end card")
    return out_dir / "card.mp4"


def cmd_join(args, root: Path, work_dir):
    """Join finished clips (burned, so already equally loud) and an optional end card into
    clips/work/<slug>/<slug>.mp4, as listed in clips/work/<slug>/montage.json:
    {"pieces": ["YYYY-MM-DD:id", ...], "card": {"lines": [...], "subtitle": "...", "link": "..."}}"""
    out_dir = root / "clips" / "work" / args.montage
    montage = load_json(out_dir / "montage.json")
    inputs = [work_dir_for(root, event) / "final" / f"clip_{cid}.final.mp4"
              for event, _, cid in (piece.partition(":") for piece in montage["pieces"])]
    if montage.get("card"):
        inputs.append(build_card(out_dir, montage["card"], root))
    # Plain cuts: one frame rate, and a few milliseconds of audio fade each side of a join
    # so it does not click.
    graph = ""
    for n, clip in enumerate(inputs):
        fade_out = ffprobe_duration(clip) - JOIN_FADE_OUT
        graph += (f"[{n}:v]fps={MONTAGE_FPS},setsar=1[v{n}];[{n}:a]aresample=48000,afade=t=in:d={JOIN_FADE_IN},"
                  f"afade=t=out:st={fade_out:.3f}:d={JOIN_FADE_OUT}[a{n}];")
    graph += "".join(f"[v{n}][a{n}]" for n in range(len(inputs))) + f"concat=n={len(inputs)}:v=1:a=1[v][a]"
    out = out_dir / f"{args.montage}.mp4"
    if run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *[a for c in inputs for a in ("-i", str(c))],
            "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]).returncode:
        fail("ffmpeg failed joining the montage")
    print(f"Wrote {out} ({ffprobe_duration(out):.1f}s)")


# ==================================================================
# argument parsing
# ==================================================================

def build_parser():
    parser = argparse.ArgumentParser(prog="clips.py", description="Mantova Dev clipping pipeline.")
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, func, help_text, ids=None):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=func)
        if name != "join":
            p.add_argument("--event", required=True, help="Event date, YYYY-MM-DD.")
            p.add_argument("--work-dir", help=argparse.SUPPRESS)  # testing override for clips/work/<event>
        if ids:
            p.add_argument("--ids", help=ids)
        return p

    chosen = "Comma-separated candidate ids (default: every chosen candidate)."
    p = command("ingest", cmd_ingest, "Link or remux the recording to source.mp4 and extract audio.wav.")
    p.add_argument("--source", required=True, help="Path to the recording (mp4, mkv, mov).")
    p.add_argument("--audio-track", default="0", help="0-based index among the audio streams (default: 0).")
    p = command("transcribe", cmd_transcribe, "Run whisper.cpp and derive transcript.json.")
    p.add_argument("--compact-only", action="store_true", help="Rebuild transcript.json from transcript.raw.json.")
    command("render", cmd_render, "Print the transcript as `[i] mm:ss text` lines ([i]~: likely invented).")
    p = command("preview", cmd_preview, "Place the cut of new or re-spanned candidates and render their previews.",
                ids="Also render these candidates' previews again.")
    p.add_argument("--pad", type=float, default=PREVIEW_PAD, help="Seconds shown before and after the cut.")
    p = command("choose", cmd_choose, "Mark candidates chosen and move their cut edges.",
                ids="Candidate ids to mark chosen.")
    p.add_argument("--unchoose", metavar="IDS", help="Candidate ids to mark not chosen.")
    for flag, what in (("--start", "start"), ("--end", "end")):
        p.add_argument(flag, action="append", metavar="ID=TIME", help=f"Move the {what} to a time read off the preview player.")
    p.add_argument("--start-at", action="append", metavar="ID=WORDS", help="Start the clip at these words from the transcript.")
    p.add_argument("--end-after", action="append", metavar="ID=WORDS", help="End the clip right after these words.")
    p.add_argument("--reset", metavar="IDS", help="Compute these candidates' cuts again.")
    command("status", cmd_status, "Print each candidate: range, edges, preview, text.", ids="Only these candidates.")
    p = command("cut", cmd_cut, "Cut chosen clips and align their words.tsv for the captions.", ids=chosen)
    p.add_argument("--force", action="store_true", help="Overwrite an existing clip and its words file.")
    p = command("tighten", cmd_tighten, "Shorten long pauses and drop stretches: final/clip_<id>.tight.mp4.", ids=chosen)
    p.add_argument("--drop", action="append", metavar="ID=WORDS",
                   help=f"Remove a stretch quoted from the words file: 3=\"first words{DROP_SEP}last words\".")
    p = command("burn", cmd_burn, "Render the final vertical clip with captions.", ids=chosen)
    p.add_argument("--crop", metavar="W:H:X:Y", help="Show only this part of the picture (stored; \"none\" clears it).")
    p = command("join", cmd_join, "Join finished clips and an end card as listed in clips/work/<slug>/montage.json.")
    p.add_argument("--montage", required=True, metavar="SLUG", help="Montage name, e.g. chi-siamo.")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    root = repo_root()
    work_dir = work_dir_for(root, args.event, args.work_dir) if hasattr(args, "event") else None
    try:
        args.func(args, root, work_dir)
        sys.stdout.flush()
    except BrokenPipeError:
        # the reader went away (render | head): stop quietly
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)

if __name__ == "__main__":
    main()
