# Clips pipeline

Turns the local recording of a Mantova Dev meetup into short vertical clips
(1080x1920, 15 to 60 s) with word-by-word captions in the community look, ready for
YouTube Shorts, Instagram Reels and TikTok. Clips from several events can also be
joined into one montage with an end card.

## How it works

One tool, `clips/clips.py`, and one skill per step. An agent reads the skill's
`SKILL.md` before running that step.

| Step | Skill | Commands | Human decides |
|------|-------|----------|---------------|
| 1 | `skills/ingest` | `ingest` | which file, which audio track |
| 2 | `skills/transcript-extract` | `transcribe`, `render` | nothing (agent sanity-checks the text) |
| 3 | `skills/highlight-selection` | `preview`, `choose`, `status` | which moment to work on, whether to keep it, where it starts and ends |
| 4 | `skills/produce-clip` | `cut`, `tighten`, `burn`, `join` | caption fixes, what to drop, final approval, post text |

To start, tell the agent something like: "make clips from `/path/to/recording.mp4`
for event <YYYY-MM-DD>, follow `clips/README.md`". Every command but `join` takes
`--event <YYYY-MM-DD>`; `clips/clips.py <command> --help` lists the options.

Clips are made one at a time: steps 3 and 4 run once per clip, and after each clip you
decide whether to make another. The agent proposes, opens a preview for you, you
answer in plain words ("start at 0:12", "drop this one", "it's CPU, not cp"), it
applies the change and shows you the result again.

The tool does the mechanical work: it places cut edges on the quoted words (on a pause
when there is one), fixes known names in the captions (`clips/config/glossary.tsv`),
brings every clip to the same loudness, flags transcript stretches whisper likely
invented. The human and the agent only decide.

## Prerequisites

Ask before installing or downloading anything.

```
brew install ffmpeg-full whisper-cpp
```

- `ffmpeg-full`, not plain `ffmpeg`: the captions need `libass`.
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
- `clips/work/<event>/`: everything a run produces. Gitignored and disposable. The
  finished clips are `clips/work/<event>/final/clip_<id>.final.mp4`; a montage is
  `clips/work/<slug>/<slug>.mp4`.
- `clips/models/`: Whisper models. Gitignored.
- `events/`: never written by the pipeline.
- Delivery: the human keeps approved videos outside the repo, named
  `YYYYMMDD-mantovadev-<slug>.mp4` (the date it was made) with the post text next to
  it in `YYYYMMDD-mantovadev-<slug>.txt`, e.g. in `~/Movies/MD`.

## Recording checklist (for whoever streams the event)

- Record locally in OBS while streaming. The local file is better than anything
  downloaded later. Use a separate recording encoder if the machine can afford it.
- Format: Hybrid MP4 or MKV (both survive a crash). MKV if there are several audio
  tracks. If practical, put the speaker's microphone on its own track too.
- Keep the camera fixed, with the speaker and the slides both in frame.

A human watches every clip to the end before it goes out.
