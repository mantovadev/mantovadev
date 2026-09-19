# transcript-extract

## Trigger

An event has been through `ingest` (so `clips/work/<event>/audio.wav` exists) and
needs a word-level transcript for highlight selection and captions.

## What it does

Runs `whisper.cpp` (`whisper-cli`) on the extracted audio with Silero VAD and full
JSON output (segments with punctuation, plus per-token sub-word timing), then merges
tokens back into punctuated words with `jq` (see `compact.jq`) to derive a compact
transcript with both sentence-like segments and word timestamps.

VAD stays on by default. Measured on the 2026-09-17 recording (68 min, 942 segments,
91% ending in sentence punctuation, 7176 words, about 2 min 15 s wall time on this
Mac): running the same audio with `--no-vad` degraded the text badly (2331 fragment
segments, only 226 sentence-ending words, and a hallucinated "applausi" at the end).
`--no-vad` is not recommended for full talks.

With `--vad`, whisper-cli reports segment offsets on the original audio timeline but
token (word) offsets on the VAD-compressed timeline (silence stripped out). Left
uncorrected, word times drift earlier and earlier through the talk (over 1100 s of
drift by the end of the 68-minute recording). `compact.jq` corrects this by shifting
each segment's tokens so its first real token lands on the segment start; silence
removed from *inside* a segment is not recovered by this shift. See `compact.jq`'s
header comments for the algorithm.

There is no vocabulary/prompt seed by default. An earlier version passed a static
"Mantova Dev" vocabulary file as a `--prompt`; measured on the 2026-09-17 recording it
did not fix the community name (still transcribed as "Mantua Dev" / "mantuadev") and
was judged not worth the maintenance burden of keeping a term list in sync. `--vocab`
still exists for ad hoc experiments but has no default, so a normal run passes none.

## Inputs

- `--event <YYYY-MM-DD>`: event date. Required.
- `--model <path>`: whisper ggml model. Default: `clips/models/ggml-large-v3-turbo.bin`.
- `--vad-model <path>`: Silero VAD ggml model. Default:
  `clips/models/ggml-silero-v5.1.2.bin`.
- `--vocab <path>`: optional prompt seed file (one term per line, `#` comments
  allowed). No default: omit this flag and no `--prompt` is passed to `whisper-cli`.
- `--language <code>`: whisper language code. Default: `it`.
- `--threads <n>`: CPU threads for `whisper-cli`.
- `--no-vad`: run without VAD (skips the VAD model check).
- `--force`: overwrite existing outputs.

Model files are never downloaded automatically. If a model is missing, the script
prints where to get it and exits non-zero:

- `ggml-large-v3-turbo.bin`: https://huggingface.co/ggerganov/whisper.cpp
- Silero VAD ggml model: https://huggingface.co/ggml-org/whisper-vad

## Outputs

- `clips/work/<event>/transcript.raw.json`: the raw whisper.cpp `-ojf` output
  (large: one entry per segment, each with its `tokens[]`).
- `clips/work/<event>/transcript.json`: compact transcript:
  ```json
  {
    "event": "2026-09-17",
    "language": "it",
    "model": "ggml-large-v3-turbo.bin",
    "segments": [ { "i": 0, "s": 0.0, "e": 3.2, "text": "Ciao a tutti." } ],
    "words": [ { "w": "Ciao", "s": 0.0, "e": 0.3, "seg": 0 } ]
  }
  ```
  Built by `compact.jq` (see that file for the token-merge algorithm and comments):
  segment text and boundaries come straight from whisper's punctuated
  `transcription[]` entries; words are rebuilt by merging each segment's `tokens[]`
  (a token starting with a space begins a new word, otherwise it is appended,
  so sub-word pieces and punctuation like `,` or `'` reattach correctly, e.g.
  `"dell'ambiente"`).

  **Timing contract for consumers:** segment (sentence) times are the reliable
  level. Word times are only rough: compared against `ffmpeg silencedetect` as a
  proxy (nobody listened to confirm it), 8.9% of word start times land inside
  detected silence, and word start times from a VAD run vs. a no-VAD run of the
  same audio disagree on matching long words by a median of 0.66 s (p90 2.3 s).
  Word **end** times are additionally clamped (end = min(raw end, next word's
  start, own start + 1.5s)) because whisper's raw word-end timestamps absorb
  trailing silence (spans of tens of seconds were observed before this clamp), so
  they are approximate on top of the general word-timing roughness above.

  Consequences for the later steps:
  - Step 3 (`highlight-selection`) works on segments/sentences, not word-precise
    cuts. Treat a word time as a rough cut position only and snap the actual cut
    point to nearby silence found with `ffmpeg silencedetect`; a human can still
    adjust the cut point interactively before cutting.
  - Step 5 (`caption-burn`) must **not** use these full-talk word times for
    word-by-word captions. It re-aligns words per chosen clip instead (15 to 60 s
    of audio is cheap to re-run): candidates are a short per-clip whisper pass
    without VAD (optionally with `-dtw large.v3.turbo`, which requires
    `--no-flash-attn` and also changes the decoded text, so accuracy vs. plain
    decoding is unverified) or a forced aligner. The approach is to be decided in
    caption-burn's own work package.

## How

```
clips/skills/transcript-extract/transcribe.sh --event 2026-09-17
```

The script prints a segment count, word count and the first/last word timestamps,
then a reminder to verify the output. Verification steps, from `clips/PLANNING.md`:

- Check the first and last minutes of the transcript for hallucinations: whisper can
  transcribe silence, applause or music as speech.
- Segment times are reliable; word times are rough (see the timing contract above),
  and word end times are additionally clamped in `compact.jq`. Do not treat word
  times as precise enough for word-by-word captions without a per-clip re-alignment.
- If accuracy turns out too poor: fall back to `mlx-whisper` (macOS) or Parakeet v3
  for transcription, or add a forced-alignment pass (WhisperX-style) on top of a text
  transcript to get better word boundaries.
