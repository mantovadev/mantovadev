#!/bin/sh
# cut.sh: cut every chosen candidate from work/<event>/source.mp4 into
# work/<event>/final/clip_<id>.mp4. Plain cut: no cropping, scaling or reframing.
# Part of the Mantova Dev clipping pipeline. See SKILL.md in this folder.
set -eu

usage() {
    cat <<'EOF'
Usage: cut.sh --event <YYYY-MM-DD> [--ids 1,3] [--force]

Options:
  --event <YYYY-MM-DD>  Event date. Reads clips/work/<event>/highlights.json. Required.
  --ids <list>          Comma-separated candidate ids. Default: every chosen candidate.
  --force               Overwrite existing clips.
  -h, --help            Show this help and exit.

Output: clips/work/<event>/final/clip_<id>.mp4 (H.264 CRF 18 + AAC, source framing).
EOF
}

fail() {
    echo "cut.sh: error: $1" >&2
    exit 1
}

EVENT=""
IDS=""
FORCE="no"

while [ $# -gt 0 ]; do
    case "$1" in
        --event)
            [ $# -ge 2 ] || fail "--event requires a value"
            EVENT="$2"
            shift 2
            ;;
        --ids)
            [ $# -ge 2 ] || fail "--ids requires a value"
            IDS="$2"
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
            echo "cut.sh: unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

[ -n "$EVENT" ] || { echo "cut.sh: --event is required" >&2; usage >&2; exit 1; }
case "$EVENT" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
    *) fail "--event must match YYYY-MM-DD, got: $EVENT" ;;
esac

command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg not found on PATH"
command -v jq >/dev/null 2>&1 || fail "jq not found on PATH"

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../../.." && pwd)
WORK_DIR="$REPO_ROOT/clips/work/$EVENT"
HIGHLIGHTS="$WORK_DIR/highlights.json"
SOURCE="$WORK_DIR/source.mp4"
OUT_DIR="$WORK_DIR/final"

[ -f "$HIGHLIGHTS" ] || fail "$HIGHLIGHTS not found. Run the highlight-selection skill first."
[ -e "$SOURCE" ] || fail "$SOURCE not found. Run ingest.sh first."

# One line per clip to cut: "<id> <start> <duration>".
if [ -n "$IDS" ]; then
    CUTS=$(jq -r --arg ids "$IDS" '
        ($ids | split(",") | map(tonumber)) as $want
        | .candidates[] | select(.id as $i | $want | index($i)) | select(.cut)
        | "\(.id) \(.cut.start) \(.cut.duration)"' "$HIGHLIGHTS")
else
    CUTS=$(jq -r '.candidates[] | select(.chosen == true) | select(.cut)
        | "\(.id) \(.cut.start) \(.cut.duration)"' "$HIGHLIGHTS")
fi
[ -n "$CUTS" ] || fail "nothing to cut: no chosen candidates (run snap_clip.py choose --ids ...) or no match for --ids"

mkdir -p "$OUT_DIR"

echo "$CUTS" | while read -r ID START DURATION; do
    OUT="$OUT_DIR/clip_$ID.mp4"
    if [ -e "$OUT" ] && [ "$FORCE" = "no" ]; then
        fail "$OUT already exists (use --force to overwrite)"
    fi
    echo "Cutting candidate $ID: start ${START}s, duration ${DURATION}s -> $OUT"
    # -ss before -i plus a re-encode gives a frame-accurate cut. Never stream-copy here.
    ffmpeg -y -hide_banner -loglevel error -nostdin \
        -ss "$START" -t "$DURATION" -i "$SOURCE" \
        -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p \
        -c:a aac -b:a 160k -movflags +faststart "$OUT" \
        || fail "ffmpeg failed cutting candidate $ID"
    echo "  wrote $OUT"
done
