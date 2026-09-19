#!/bin/sh
# render_transcript.sh: print clips/work/<event>/transcript.json as numbered,
# mm:ss-stamped lines so an agent can read a full talk in one pass.
# Part of the Mantova Dev clipping pipeline. See clips/PLANNING.md and SKILL.md in this folder.
set -eu

usage() {
    cat <<'EOF'
Usage: render_transcript.sh --event <YYYY-MM-DD>

Options:
  --event <YYYY-MM-DD>  Event date. Reads clips/work/<event>/transcript.json. Required.
  -h, --help            Show this help and exit.

Prints one line per segment to stdout:
  [<i>] <mm:ss> <text>

<i> is the segment index (matches transcript.json's segments[].i and highlights.json's
start_seg/end_seg), <mm:ss> is the segment start time.
EOF
}

fail() {
    echo "render_transcript.sh: error: $1" >&2
    exit 1
}

EVENT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --event)
            [ $# -ge 2 ] || fail "--event requires a value"
            EVENT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "render_transcript.sh: unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

[ -n "$EVENT" ] || { echo "render_transcript.sh: --event is required" >&2; usage >&2; exit 1; }

case "$EVENT" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
    *) fail "--event must match YYYY-MM-DD, got: $EVENT" ;;
esac

command -v jq >/dev/null 2>&1 || fail "jq not found on PATH. Install with: brew install jq"

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../../.." && pwd)
TRANSCRIPT="$REPO_ROOT/clips/work/$EVENT/transcript.json"

[ -f "$TRANSCRIPT" ] || fail "$TRANSCRIPT not found. Run transcribe.sh for event $EVENT first."

jq -r '
  .segments[]
  | (.s | tostring | tonumber | floor) as $t
  | ($t / 60 | floor) as $m
  | ($t % 60) as $sec
  | "[\(.i)] \($m | tostring | if length < 2 then "0" + . else . end):\($sec | tostring | if length < 2 then "0" + . else . end) \(.text)"
' "$TRANSCRIPT"
