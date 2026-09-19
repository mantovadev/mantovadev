#!/bin/sh
# transcribe.sh: run whisper.cpp on work/<event>/audio.wav and derive a compact
# clips/work/<event>/transcript.json. See clips/PLANNING.md and SKILL.md in this folder.
set -eu

usage() {
    cat <<'EOF'
Usage: transcribe.sh --event <YYYY-MM-DD> [--model <path>] [--vad-model <path>]
                      [--vocab <path>] [--language it] [--threads <n>]
                      [--no-vad] [--force]

Options:
  --event <YYYY-MM-DD>  Event date. Reads clips/work/<event>/audio.wav. Required.
  --model <path>        Whisper ggml model. Default: clips/models/ggml-large-v3-turbo.bin
  --vad-model <path>    Silero VAD ggml model. Default: clips/models/ggml-silero-v5.1.2.bin
  --vocab <path>        Optional prompt seed file (one term per line, '#' comments
                         allowed). No default: omit this flag and no --prompt is
                         passed to whisper-cli.
  --language <code>     Whisper language code. Default: it
  --threads <n>         Number of CPU threads to pass to whisper-cli.
  --no-vad              Run without VAD (skips the VAD model requirement).
  --force               Overwrite existing outputs if present.
  -h, --help            Show this help and exit.

Outputs:
  clips/work/<event>/transcript.raw.json      raw whisper.cpp -ojf output
  clips/work/<event>/transcript.json          compact segment+word transcript
EOF
}

fail() {
    echo "transcribe.sh: error: $1" >&2
    exit 1
}

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../../.." && pwd)

EVENT=""
MODEL="$REPO_ROOT/clips/models/ggml-large-v3-turbo.bin"
VAD_MODEL="$REPO_ROOT/clips/models/ggml-silero-v5.1.2.bin"
VOCAB=""
LANGUAGE="it"
THREADS=""
USE_VAD="yes"
FORCE="no"

while [ $# -gt 0 ]; do
    case "$1" in
        --event)
            [ $# -ge 2 ] || fail "--event requires a value"
            EVENT="$2"
            shift 2
            ;;
        --model)
            [ $# -ge 2 ] || fail "--model requires a value"
            MODEL="$2"
            shift 2
            ;;
        --vad-model)
            [ $# -ge 2 ] || fail "--vad-model requires a value"
            VAD_MODEL="$2"
            shift 2
            ;;
        --vocab)
            [ $# -ge 2 ] || fail "--vocab requires a value"
            VOCAB="$2"
            shift 2
            ;;
        --language)
            [ $# -ge 2 ] || fail "--language requires a value"
            LANGUAGE="$2"
            shift 2
            ;;
        --threads)
            [ $# -ge 2 ] || fail "--threads requires a value"
            THREADS="$2"
            shift 2
            ;;
        --no-vad)
            USE_VAD="no"
            shift
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
            echo "transcribe.sh: unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

[ -n "$EVENT" ] || { echo "transcribe.sh: --event is required" >&2; usage >&2; exit 1; }

case "$EVENT" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
    *) fail "--event must match YYYY-MM-DD, got: $EVENT" ;;
esac

command -v whisper-cli >/dev/null 2>&1 || fail "whisper-cli not found on PATH. Install with: brew install whisper-cpp"
command -v jq >/dev/null 2>&1 || fail "jq not found on PATH. Install with: brew install jq"

WORK_DIR="$REPO_ROOT/clips/work/$EVENT"
AUDIO="$WORK_DIR/audio.wav"
[ -f "$AUDIO" ] || fail "$AUDIO not found. Run ingest.sh for event $EVENT first."

[ -f "$MODEL" ] || fail "whisper model not found: $MODEL
Download ggml-large-v3-turbo.bin manually from https://huggingface.co/ggerganov/whisper.cpp
and place it at: $MODEL"

if [ "$USE_VAD" = "yes" ]; then
    [ -f "$VAD_MODEL" ] || fail "VAD model not found: $VAD_MODEL
Download the Silero VAD ggml model manually from https://huggingface.co/ggml-org/whisper-vad
and place it at: $VAD_MODEL
(or pass --no-vad to transcribe without VAD)"
fi

if [ -n "$VOCAB" ]; then
    [ -f "$VOCAB" ] || fail "vocabulary file not found: $VOCAB"
fi

OUT_RAW_PREFIX="$WORK_DIR/transcript.raw"
OUT_RAW_JSON="$OUT_RAW_PREFIX.json"
OUT_COMPACT="$WORK_DIR/transcript.json"

if [ "$FORCE" = "no" ]; then
    if [ -e "$OUT_RAW_JSON" ]; then
        fail "$OUT_RAW_JSON already exists (use --force to overwrite)"
    fi
    if [ -e "$OUT_COMPACT" ]; then
        fail "$OUT_COMPACT already exists (use --force to overwrite)"
    fi
fi

set -- -m "$MODEL" -l "$LANGUAGE" -ojf -of "$OUT_RAW_PREFIX" -f "$AUDIO"

if [ -n "$VOCAB" ]; then
    echo "Building prompt from vocabulary file: $VOCAB"
    PROMPT=$(grep -v '^#' "$VOCAB" | grep -v '^[[:space:]]*$' | tr '\n' ' ' | sed 's/[[:space:]]*$//')
    echo "  prompt: $PROMPT"
    set -- "$@" --prompt "$PROMPT"
fi

if [ "$USE_VAD" = "yes" ]; then
    set -- "$@" --vad --vad-model "$VAD_MODEL"
fi

if [ -n "$THREADS" ]; then
    set -- "$@" -t "$THREADS"
fi

echo "Running: whisper-cli $*"
if ! whisper-cli "$@"; then
    fail "whisper-cli failed. See output above."
fi

[ -f "$OUT_RAW_JSON" ] || fail "expected whisper-cli output not found: $OUT_RAW_JSON"

echo "Deriving compact transcript: $OUT_COMPACT"

MODEL_BASENAME=$(basename "$MODEL")

# With VAD, token offsets are on the silence-removed timeline; compact.jq shifts them back.
if [ "$USE_VAD" = "yes" ]; then VAD_JSON=true; else VAD_JSON=false; fi

jq --arg event "$EVENT" --arg language "$LANGUAGE" --arg model "$MODEL_BASENAME" \
    --argjson vad "$VAD_JSON" \
    -f "$SCRIPT_DIR/compact.jq" "$OUT_RAW_JSON" > "$OUT_COMPACT"

WORD_COUNT=$(jq '.words | length' "$OUT_COMPACT")
SEGMENT_COUNT=$(jq '.segments | length' "$OUT_COMPACT")
FIRST_TS=$(jq '.words[0].s // null' "$OUT_COMPACT")
LAST_TS=$(jq '.words[-1].e // null' "$OUT_COMPACT")

echo ""
echo "Transcription complete for event $EVENT:"
echo "  raw whisper output: $OUT_RAW_JSON"
echo "  compact transcript: $OUT_COMPACT"
echo "  segments: $SEGMENT_COUNT"
echo "  words: $WORD_COUNT"
echo "  first word starts at: ${FIRST_TS}s, last word ends at: ${LAST_TS}s"
echo ""
echo "REMINDER: check the first and last minutes for hallucinations (silence, applause,"
echo "music transcribed as speech) and spot-check word timing against the audio before"
echo "trusting it for karaoke-style captions."
