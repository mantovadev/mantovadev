# Clips pipeline: usage

Turns a local Mantova Dev meetup recording into short captioned clips for Shorts,
Reels and TikTok. Clips keep the source 16:9 framing: the pipeline only cuts and burns
captions, with no cropping or reframing. See [`PLANNING.md`](./PLANNING.md) for the
decisions, the full pipeline and the work packages. Steps 1 to 3 (ingest,
transcript-extract, highlight-selection) are implemented so far.

## Prerequisites

```
brew install ffmpeg-full whisper-cpp
```

Homebrew's plain `ffmpeg` formula lacks `libass`, which the caption-burn step (not
yet implemented) needs. Use `ffmpeg-full`. The Homebrew formula `whisper-cpp` (now
an alias of `whisper.cpp`) installs the `whisper-cli` binary.

Whisper models are not installed by Homebrew and are never committed. Download them
manually into `clips/models/` (gitignored):

- `ggml-large-v3-turbo.bin` from https://huggingface.co/ggerganov/whisper.cpp
- Silero VAD ggml model (`ggml-silero-v5.1.2.bin`) from
  https://huggingface.co/ggml-org/whisper-vad

## Quick start

Using event `2026-09-17` and a local recording as an example:

```
clips/skills/ingest/ingest.sh --source /path/to/recording.mp4 --event 2026-09-17
clips/skills/transcript-extract/transcribe.sh --event 2026-09-17
clips/skills/highlight-selection/render_transcript.sh --event 2026-09-17
```

`ingest.sh` prints the audio streams it finds. If the recording has a separate
mic-only track, pick it with `--audio-track <n>` for a cleaner transcript.

After `render_transcript.sh`, an agent follows
`clips/skills/highlight-selection/SKILL.md` to read the whole talk and write
`clips/work/2026-09-17/highlights.json` (candidates by segment index and
quote, never by timestamp). Then:

```
clips/skills/highlight-selection/snap_clip.py snap --event 2026-09-17
```

snaps each candidate to a cut point (using silence detection on
`audio.wav`) and writes `clips/work/2026-09-17/candidates.md` for human
review. Once a human has picked which candidates to keep:

```
clips/skills/highlight-selection/snap_clip.py choose --event 2026-09-17 --ids 1,3
```

marks those candidates `chosen` in `highlights.json`. See the SKILL.md for the
full procedure, including how to fix flagged candidates (`quote_mismatch`,
`too_short`, `too_long`, overlaps).

## Where outputs land

All run outputs for an event live under `clips/work/2026-09-17/` (gitignored):

- `source.mp4`, `audio.wav`: heavy working media from `ingest`.
- `transcript.raw.json`: the raw whisper output.
- `transcript.json`: compact segment + word-level transcript. Segment (sentence)
  times are reliable; word timing is only rough (see
  `clips/skills/transcript-extract/SKILL.md`). Captions get their word timing from a
  separate per-clip re-alignment pass, not from this file's word times.
- `highlights.json`: clip candidates (written by the highlight-selection agent
  step) plus their computed cut points and chosen status (written by
  `snap_clip.py`).
- `candidates.md`: human-readable review sheet generated from `highlights.json`,
  one section per candidate with its time range, flags, hook, reason, transcript
  text and a preview `ffplay` command.
- `silences.json`: cached silence detection used by `snap_clip.py`.

Finished clips will later land in `clips/work/2026-09-17/final/`.

`clips/models/` holds whisper model files, also gitignored.

## Committed vs ignored

Nothing produced by a pipeline run is committed. Every per-event file lives in the
gitignored `clips/work/<event>/`. The repo only holds the process: scripts,
`SKILL.md` files, schemas, config and docs. The pipeline never writes into
`events/`; that folder stays human-only (event `README.md` and talk materials).

## Not implemented yet

- **[4] clip-cutter**: ffmpeg frame-accurate cut and fixed-layout vertical reframe.
- **[5] caption-burn**: generates a per-word `.ass` file and burns it in.
- **[6] social-copy**: per-clip, per-platform title/hook/caption/hashtags in Italian.

See `clips/PLANNING.md` for the full step details and the work package plan.
