# Clips pipeline

Turns the local recording of a Mantova Dev meetup into a few short vertical clips
(1080x1920, 15 to 60 s) with word-by-word captions in the community look, ready for
YouTube Shorts, Instagram Reels and TikTok.

## How it works

One tool, `clips/clips.py`, and one skill per step. An agent reads the skill's
`SKILL.md` before running that step.

| Step | Skill | Commands | Human decides |
|------|-------|----------|---------------|
| 1 | `skills/ingest` | `ingest` | which file, which audio track |
| 2 | `skills/transcript-extract` | `transcribe`, `render` | nothing (agent sanity-checks the text) |
| 3 | `skills/highlight-selection` | `snap`, `preview`, `choose`, `status` | which moments from the agent's long list to preview (plus your own), then which to keep and where each starts and ends |
| 4 | `skills/produce-clip` | `cut`, `align`, `burn` | caption text fixes, final approval, post text |

To start, tell the agent something like: "make clips from `/path/to/recording.mp4`
for event 2026-09-17, follow `clips/README.md`". Every command takes
`--event <YYYY-MM-DD>`; `clips/clips.py <command> --help` lists the options.

The review loops are interactive: the agent proposes, opens a preview file for you,
you answer in plain words ("start at 0:12", "drop this one", "it's CPU, not cp"), it
applies the change and shows you the result again.

## Prerequisites

Ask before installing or downloading anything.

```
brew install ffmpeg-full whisper-cpp
```

- Homebrew's plain `ffmpeg` has no `libass`, which the captions need: use
  `ffmpeg-full`. `whisper-cpp` (now an alias of `whisper.cpp`) provides `whisper-cli`.
- Python 3.9 or later, standard library only.
- Whisper models, downloaded by hand into `clips/models/` (gitignored):
  `ggml-large-v3-turbo.bin` (about 1.5 GB) from
  https://huggingface.co/ggerganov/whisper.cpp and `ggml-silero-v5.1.2.bin` from
  https://huggingface.co/ggml-org/whisper-vad.
- The Aeonik Pro font family installed on the machine (brand font, proprietary, not
  in the repo). Without it captions fall back to a default font.

## Where things live

- `clips/clips.py`, `clips/skills/`, `clips/schemas/`, `clips/config/`: the process.
  This is all that is committed.
- `clips/work/<event>/`: everything a run produces (media, transcript, candidates,
  previews, captions). Gitignored. The finished clips are
  `clips/work/<event>/final/clip_<id>.final.mp4`.
- `clips/models/`: Whisper models. Gitignored.
- `events/`: never written by the pipeline.

## Recording checklist (for whoever streams the event)

- Record locally in OBS while streaming ("Automatically record when streaming", or
  press Start Recording). The local file is better than anything downloaded later.
- Use a separate recording encoder if the machine can afford it; "Same as stream"
  caps the quality at the stream bitrate.
- Format: Hybrid MP4 or MKV (both survive a crash). MKV if there are several audio
  tracks.
- If practical, put the speaker's microphone on its own audio track in addition to
  the full mix: it transcribes better.
- Keep the camera fixed, with the speaker and the slides both in frame: one crop
  rectangle then serves the whole talk.

## Before publishing

- A human watches every clip to the end before it goes out.
