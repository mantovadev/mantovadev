#!/usr/bin/env python3
"""captions.py: word-by-word ("TikTok style") captions for a cut clip.

Part of the Mantova Dev clipping pipeline. See SKILL.md in this folder.
Standard library only. Needs ffmpeg (with libass) and whisper-cli on PATH.

Subcommands:
  align --event <YYYY-MM-DD> --id <n> [--model <path>] [--language it] [--force]
        Transcribe clips/work/<event>/final/clip_<n>.mp4 on its own (no VAD, DTW token
        timestamps) and write clip_<n>.words.tsv: one word per line, start<TAB>end<TAB>word,
        times in seconds from the start of the clip. This file is meant to be edited by
        a human or an agent: fix words, nudge times, delete lines.
  burn  --event <YYYY-MM-DD> --id <n> [--crop W:H:X:Y] [--footer text] [style options]
        Build clip_<n>.ass from clip_<n>.words.tsv and render clip_<n>.final.mp4: a
        vertical 1080x1920 canvas (brand background, logo on top, the picture at full
        width, captions below it, a link at the bottom). Re-run after every edit of
        the words file.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Timing. DTW token times tend to land a little after the audible word onset, so they
# are pulled earlier by DTW_LEAD. The first word of each whisper segment uses the
# segment start instead, which sits right on the speech onset after a pause.
DTW_LEAD = 0.12
MIN_WORD = 0.08          # minimum spacing between consecutive word starts
LAST_WORD_HOLD = 0.6     # how long the final word of a chunk stays if nothing follows

# Chunking: a chunk is what is on screen at once.
MAX_WORDS = 3
MAX_CHARS = 14          # upper case Aeonik Black at size 100: 15 chars fill about 80% of the width
GAP_BREAK = 0.45         # a pause this long always starts a new chunk
BREAK_AFTER = ".?!,;:"   # punctuation that ends a chunk
STRIP_PUNCT = ".,;:"     # punctuation not shown on screen ("?" and "!" stay)

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


def fail(msg: str):
    print(f"captions.py: error: {msg}", file=sys.stderr)
    sys.exit(1)


def repo_root() -> Path:
    # this file lives at <repo>/clips/skills/caption-burn/captions.py
    return Path(__file__).resolve().parents[3]


def run(cmd, **kwargs):
    print("Running: " + " ".join(str(c) for c in cmd), file=sys.stderr)
    try:
        return subprocess.run(cmd, **kwargs)
    except FileNotFoundError:
        fail(f"{cmd[0]} not found on PATH")


def clip_paths(root: Path, event: str, clip_id: int):
    final = root / "clips" / "work" / event / "final"
    base = final / f"clip_{clip_id}"
    return final, base


# ---------------------------------------------------------------- align

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
            end = tok["offsets"]["to"] / 1000.0
            if cur is None or text.startswith(" "):
                if cur is not None:
                    words.append(cur)
                if first_in_seg:
                    start = seg_start
                    first_in_seg = False
                cur = {"w": text, "s": start, "e": end}
            else:
                cur["w"] += text
                cur["e"] = end
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


def cmd_align(args, root: Path):
    final, base = clip_paths(root, args.event, args.id)
    clip = base.with_suffix(".mp4")
    words_path = Path(str(base) + ".words.tsv")
    if not clip.is_file():
        fail(f"{clip} not found. Run clips/skills/clip-cutter/cut.sh first.")
    if words_path.exists() and not args.force:
        fail(f"{words_path} already exists and may hold human edits (use --force to overwrite)")
    model = Path(args.model) if args.model else root / "clips" / "models" / "ggml-large-v3-turbo.bin"
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
    proc = run(["whisper-cli", "-m", str(model), "-l", args.language, "-ojf",
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
    wav.unlink(missing_ok=True)
    print(f"Wrote {words_path} ({len(words)} words). Review and edit it, then run: captions.py burn "
          f"--event {args.event} --id {args.id}")


# ---------------------------------------------------------------- burn

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


def make_chunks(words):
    chunks, cur = [], []
    for i, word in enumerate(words):
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


def ass_time(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def display(word: str, style) -> str:
    text = word.rstrip(STRIP_PUNCT).lstrip("¿¡")
    text = text.replace("{", "(").replace("}", ")").replace("\\", "/")
    return text.upper() if style["uppercase"] else text


def build_ass(words, style, caption_top: int, footer: str, duration: float) -> str:
    """Captions hang from y = caption_top (below the picture); footer is static text
    at the bottom of the canvas for the whole clip."""
    width, height, scale = CANVAS_W, CANVAS_H, 1.0
    size = style["size"]
    alignment, margin_v = 8, int(caption_top)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{style['font']},{size},{style['primary']},{style['primary']},{style['outline_colour']},&H80000000,0,0,0,0,100,100,0,0,1,{style['outline'] * scale:.1f},{style['shadow'] * scale:.1f},{alignment},60,60,{margin_v},1
Style: Footer,{style['footer_font']},{int(round(style['footer_size'] * scale))},{style['active']},{style['active']},{style['outline_colour']},&H80000000,0,0,0,0,100,100,0,0,1,0,0,2,60,60,{int(round(style['footer_margin_v'] * scale))},1

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


def probe_duration(clip: Path) -> float:
    proc = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
               capture_output=True, text=True)
    try:
        return float(proc.stdout.strip())
    except ValueError:
        fail(f"could not read the duration of {clip}")


def probe_size(clip: Path):
    proc = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                "stream=width,height", "-of", "csv=p=0", str(clip)], capture_output=True, text=True)
    try:
        width, height = (int(x) for x in proc.stdout.strip().split(",")[:2])
        return width, height
    except ValueError:
        fail(f"could not read the video size of {clip}")


def cmd_burn(args, root: Path):
    final, base = clip_paths(root, args.event, args.id)
    clip = base.with_suffix(".mp4")
    words_path = Path(str(base) + ".words.tsv")
    if not clip.is_file():
        fail(f"{clip} not found. Run clips/skills/clip-cutter/cut.sh first.")
    if not words_path.is_file():
        fail(f"{words_path} not found. Run the align subcommand first.")

    style = dict(DEFAULT_STYLE)
    for key in ("font", "size", "active"):
        value = getattr(args, key)
        if value is not None:
            style[key] = value
    style["uppercase"] = not args.keep_case

    words = read_words(words_path)
    width, height = probe_size(clip)
    encode = ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
              "-c:a", "copy", "-movflags", "+faststart"]

    # Vertical 1080x1920: brand background, logo on top, the (optionally cropped) picture
    # at full width, captions in the free space below it, link at the bottom.
    logo = root / "clips" / "config" / "brand" / "logo-dark-bg.png"
    if not logo.is_file():
        fail(f"brand logo not found: {logo}")
    crop = "null"
    if args.crop:
        if not re.match(r"^\d+:\d+:\d+:\d+$", args.crop):
            fail(f"--crop must be W:H:X:Y in source pixels, got: {args.crop}")
        crop = f"crop={args.crop}"
        width, height = (int(x) for x in args.crop.split(":")[:2])
    video_h = int(round(CANVAS_W * height / width / 2)) * 2
    video_y = max(LOGO_Y + 200, (CANVAS_H - video_h) // 2 - 140)
    ass_name = f"clip_{args.id}.ass"
    out_name = f"clip_{args.id}.final.mp4"
    (final / ass_name).write_text(
        build_ass(words, style, caption_top=video_y + video_h + 120,
                  footer=args.footer, duration=probe_duration(clip)),
        encoding="utf-8-sig")
    graph = (
        f"[0:v]{crop},scale={CANVAS_W}:{video_h}[v];"
        f"color=c=0x{style['background']}:s={CANVAS_W}x{CANVAS_H}:r=30[bg];"
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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="captions.py", description="Word-by-word captions for a cut clip.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_align = sub.add_parser("align", help="Transcribe the clip and write its editable words file.")
    p_align.add_argument("--event", required=True)
    p_align.add_argument("--id", required=True, type=int)
    p_align.add_argument("--model", help="Whisper ggml model (default: clips/models/ggml-large-v3-turbo.bin).")
    p_align.add_argument("--language", default="it")
    p_align.add_argument("--force", action="store_true", help="Overwrite an existing words file (loses edits).")
    p_align.set_defaults(func=cmd_align)

    p_burn = sub.add_parser("burn", help="Build the .ass from the words file and burn it in.")
    p_burn.add_argument("--event", required=True)
    p_burn.add_argument("--id", required=True, type=int)
    p_burn.add_argument("--font", help=f"Font family (default: {DEFAULT_STYLE['font']}).")
    p_burn.add_argument("--size", type=int, help=f"Caption font size (default: {DEFAULT_STYLE['size']}).")
    p_burn.add_argument("--active", help=f"Highlight colour as ASS &HAABBGGRR (default: {DEFAULT_STYLE['active']}).")
    p_burn.add_argument("--keep-case", action="store_true", help="Keep the words as written instead of showing them in upper case.")
    p_burn.add_argument("--crop", metavar="W:H:X:Y", help="Crop the source picture first (source pixels) so it shows bigger on the canvas, e.g. 1440:1080:0:0.")
    p_burn.add_argument("--footer", default="mantova.dev", help="Text at the bottom of the canvas (default: mantova.dev).")
    p_burn.set_defaults(func=cmd_burn)

    args = parser.parse_args(argv)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.event):
        fail(f"--event must match YYYY-MM-DD, got: {args.event}")
    args.func(args, repo_root())


if __name__ == "__main__":
    main()
