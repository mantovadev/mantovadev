# transcript-extract

## Trigger

`ingest` is done (`clips/work/<event>/audio.wav` exists) and the talk needs a
transcript for highlight selection.

## What it does

Runs `whisper-cli` (whisper.cpp, `large-v3-turbo`, Italian, Silero VAD) on the full
audio and derives a compact transcript: punctuated sentence-like segments with
reliable times, plus rough word times. About 2 min 15 s for a 68 minute talk on the
maintainers' Mac.

## Inputs

- `--event <YYYY-MM-DD>`. Required.
- `--model`, `--vad-model`: default to `clips/models/ggml-large-v3-turbo.bin` and
  `clips/models/ggml-silero-v5.1.2.bin`. Models are never downloaded automatically;
  if one is missing the tool prints where to get it (ask the human before
  downloading: the main model is about 1.5 GB).
- `--language` (default `it`), `--threads`, `--force`.
- `--compact-only`: rebuild `transcript.json` from the existing raw output without
  running Whisper again.
- `--no-vad` exists as an escape hatch. Do not use it for a normal run (see below).

## Outputs

In `clips/work/<event>/`:

- `transcript.raw.json`: raw `whisper-cli -ojf` output (large).
- `transcript.json`:
  `{event, language, model, segments: [{i, s, e, text}], words: [{w, s, e, seg}]}`,
  times in seconds.

**Timing contract.** Segment times are reliable. Word times are rough (about half a
second at best) and word ends are clamped, so later steps never depend on them:
cuts are placed on detected pauses and reviewed by a human, and captions get their
own per-clip word alignment in `produce-clip`.

## How

```
clips/clips.py transcribe --event <event>
```

Then check the result before moving on:

- Read the first and the last minute of `clips/clips.py render --event <event>`:
  Whisper can turn silence, applause or music into invented text.
- Skim a stretch in the middle for fluency. Mis-heard names and jargon are expected
  and get fixed per clip later; whole garbled passages are not.

Why the defaults are what they are (measured on the 2026-09-17 talk):

- VAD stays on. Without it the text degraded badly (fragmented segments, most
  sentence punctuation lost, invented text at the end).
- With VAD, `whisper-cli` 1.9.4 reports token times on the silence-removed timeline
  while segment times are on the real one. The tool shifts each segment's words back
  onto the real timeline; uncorrected, word times drifted by over 1100 s.
- A vocabulary prompt was tried and did not fix even the community name ("Mantua
  Dev"), so the tool has none. Mis-heard terms are fixed per clip in `produce-clip`.
- If quality is too poor for a recording, the fallbacks to evaluate are `mlx-whisper`
  (macOS) or Parakeet v3. Unverified.

Next step: `highlight-selection`.
