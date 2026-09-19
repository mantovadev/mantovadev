#!/bin/sh
# ingest.sh: normalize a local recording into work/<event>/source.mp4 and audio.wav
# Part of the Mantova Dev clipping pipeline. See clips/PLANNING.md and SKILL.md in this folder.
set -eu

usage() {
    cat <<'EOF'
Usage: ingest.sh --source <path> --event <YYYY-MM-DD> [--audio-track <n>] [--force]

Options:
  --source <path>       Path to the source recording (mp4, mkv, mov). Required.
  --event <YYYY-MM-DD>  Event date, used as the output folder name. Required.
  --audio-track <n>     0-based index among audio streams to extract (default: 0).
  --force               Overwrite existing outputs if present.
  -h, --help            Show this help and exit.

Outputs (under <repo>/clips/work/<event>/):
  source.mp4            Remuxed copy of the source (or a symlink if already .mp4).
  audio.wav             16 kHz mono PCM s16le, extracted from the chosen audio track.
EOF
}

fail() {
    echo "ingest.sh: error: $1" >&2
    exit 1
}

SOURCE=""
EVENT=""
AUDIO_TRACK="0"
FORCE="no"

while [ $# -gt 0 ]; do
    case "$1" in
        --source)
            [ $# -ge 2 ] || fail "--source requires a value"
            SOURCE="$2"
            shift 2
            ;;
        --event)
            [ $# -ge 2 ] || fail "--event requires a value"
            EVENT="$2"
            shift 2
            ;;
        --audio-track)
            [ $# -ge 2 ] || fail "--audio-track requires a value"
            AUDIO_TRACK="$2"
            shift 2
            ;;
        --force)
            FORCE="yes"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ingest.sh: unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [ -z "$SOURCE" ] || [ -z "$EVENT" ]; then
    echo "ingest.sh: --source and --event are required" >&2
    usage >&2
    exit 1
fi

case "$EVENT" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
    *) fail "--event must match YYYY-MM-DD, got: $EVENT" ;;
esac

[ -e "$SOURCE" ] || fail "source not found: $SOURCE"

command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg not found on PATH"
command -v ffprobe >/dev/null 2>&1 || fail "ffprobe not found on PATH"

echo "Checking ffmpeg for libass support (needed later for caption burn-in)..."
if ffmpeg -hide_banner -filters 2>/dev/null | grep -E '\bass\b' >/dev/null 2>&1; then
    echo "  ok: ass filter present"
else
    echo "WARNING: ffmpeg is missing the 'ass' filter (no libass). Caption burn-in (step 5) will fail." >&2
    echo "WARNING: on macOS, install Homebrew ffmpeg-full instead of plain ffmpeg." >&2
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../../.." && pwd)
OUT_DIR="$REPO_ROOT/clips/work/$EVENT"
SOURCE_ABS=$(cd "$(dirname "$SOURCE")" && pwd)/$(basename "$SOURCE")

echo "Output directory: $OUT_DIR"
mkdir -p "$OUT_DIR"

OUT_MP4="$OUT_DIR/source.mp4"
OUT_WAV="$OUT_DIR/audio.wav"

if [ "$FORCE" = "no" ]; then
    if [ -e "$OUT_MP4" ]; then
        fail "$OUT_MP4 already exists (use --force to overwrite)"
    fi
    if [ -e "$OUT_WAV" ]; then
        fail "$OUT_WAV already exists (use --force to overwrite)"
    fi
fi

case "$SOURCE_ABS" in
    *.mp4|*.MP4)
        echo "Source is already .mp4: creating a symlink instead of copying."
        command rm -f "$OUT_MP4"
        ln -s "$SOURCE_ABS" "$OUT_MP4"
        echo "  linked $OUT_MP4 -> $SOURCE_ABS"
        ;;
    *)
        echo "Remuxing $SOURCE_ABS to $OUT_MP4 (stream copy, no re-encode)..."
        if ! ffmpeg -y -hide_banner -loglevel error -i "$SOURCE_ABS" -c copy -map 0 -movflags +faststart "$OUT_MP4"; then
            command rm -f "$OUT_MP4"
            fail "remux to mp4 failed. Some codecs (e.g. certain subtitle or data streams) do not fit in an mp4 container. Re-run with an mp4 or mkv OBS recording, or remux to mkv manually and adjust this script."
        fi
        echo "  wrote $OUT_MP4"
        ;;
esac

echo "Audio streams in source:"
ffprobe -v error -select_streams a \
    -show_entries stream=index,codec_name,channels:stream_tags=title \
    -of csv=p=0 "$SOURCE_ABS" | awk -F',' '{ printf "  a:%d  index=%s  codec=%s  channels=%s  title=%s\n", NR-1, $1, $2, $3, ($4 == "" ? "(none)" : $4) }'

echo "Extracting audio track a:$AUDIO_TRACK to 16 kHz mono WAV..."
if ! ffmpeg -y -hide_banner -loglevel error -i "$SOURCE_ABS" -map "0:a:$AUDIO_TRACK" -ac 1 -ar 16000 -c:a pcm_s16le "$OUT_WAV"; then
    fail "audio extraction failed for track a:$AUDIO_TRACK. Check the audio stream list above and pick a valid --audio-track."
fi
echo "  wrote $OUT_WAV"

DURATION=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT_WAV" 2>/dev/null || echo "unknown")

echo ""
echo "Ingest complete for event $EVENT:"
echo "  video: $OUT_MP4"
echo "  audio: $OUT_WAV (duration: ${DURATION}s)"
