# Clips pipeline

Turns the local recording of a talk into short vertical clips (1080x1920, 15 to 60 s)
with word-by-word captions, ready for YouTube Shorts, Instagram Reels and TikTok.
Clips from several events can also be joined into one montage with an end card.

## How it works

One tool, `clips/clips.py`, and one skill per step. An agent reads the skill's
`SKILL.md` before running that step.

| Step | Skill | Commands | Human decides |
|------|-------|----------|---------------|
| 1 | `skills/ingest` | `ingest` | which file, which audio track |
| 2 | `skills/transcript-extract` | `transcribe`, `render` | nothing (agent sanity-checks the text) |
| 3 | `skills/highlight-selection` | `preview`, `choose`, `status` | which moment to work on, whether to keep it, where it starts and ends |
| 4 | `skills/produce-clip` | `cut`, `tighten`, `edit`, `burn`, `cover`, `join` | caption fixes, what to drop, final approval, cover, post text |

To start, tell the agent something like: "make clips from `/path/to/recording.mp4`
for event <YYYY-MM-DD>, follow `clips/README.md`". Every command but `join` takes
`--event <YYYY-MM-DD>`; `clips/clips.py <command> --help` lists the options.

The look (fonts, colours, logo, footer text) and the language come from a preset,
`clips/presets/<name>/`: a `preset.json` with what differs from the built-in default
(listed in `PRESET` in `clips.py`), the files it names, and an optional
`conventions.md` for the agent (post text, delivery). `ingest --preset <name>` records
it for the event. Without one, clips are in English with a plain look.

Clips are made one at a time: steps 3 and 4 run once per clip, and after each clip you
decide whether to make another. The agent proposes, opens a preview for you, you
answer in plain words ("start at 0:12", "drop this one", "it's CPU, not cp"), it
applies the change and shows you the result again.

The tool does the mechanical work: it places cut edges on the quoted words (on a pause
when there is one), fixes known names in the captions (the preset's `glossary.tsv`),
brings every clip to the same loudness, flags transcript stretches whisper likely
invented. The human and the agent only decide.

Optionally, cuts can be fine-tuned by hand in any video editor that imports and exports
OpenTimelineIO (`.otio`): `edit` writes a clip's timeline, and `join` a montage's.
Export the edited timeline next to it as `<name>.edited.otio`, and `burn` or `join`
follows it. The produce-clip skill has the details.

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
- The fonts the preset names, installed on the machine. Without them captions fall
  back to a default font.

## Where things live

- `clips/clips.py`, `clips/skills/`, `clips/schemas/`, `clips/presets/`: the process.
  This is all that is committed.
- `clips/work/<event>/`: everything a run produces. Gitignored and disposable. The
  finished clips are `clips/work/<event>/final/clip_<id>.final.mp4`; a montage is
  `clips/work/<slug>/<slug>.mp4`.
- `clips/models/`: Whisper models. Gitignored.
- `events/`: never written by the pipeline.
- Delivery: each approved post goes outside the repo in its own folder, as the
  preset's `conventions.md` says.

## Recording checklist (for whoever streams the event)

- Record locally in OBS while streaming. The local file is better than anything
  downloaded later. Use a separate recording encoder if the machine can afford it.
- Format: Hybrid MP4 or MKV (both survive a crash). MKV if there are several audio
  tracks. If practical, put the speaker's microphone on its own track too.
- Keep the camera fixed, with the speaker and the slides both in frame.

A human watches every clip to the end before it goes out.
