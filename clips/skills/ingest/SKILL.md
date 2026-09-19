# ingest

## Trigger

A new event recording needs to enter the clipping pipeline: a human (or an agent
following `clips/README.md`) has a local source file (OBS recording, or a YouTube
Studio download) and wants to prepare it for transcription and clipping.

## What it does

Normalizes the source recording into a working copy plus an extracted audio track:

- Remuxes the source to MP4 if it is not already MP4 (stream copy, no re-encode). If
  the source is already `.mp4`, it symlinks instead of copying, since copying a
  multi-gigabyte file is wasteful.
- Lists the audio streams in the source (index, codec, channels, title tag) so a human
  or agent can spot a mic-only track.
- Extracts the chosen audio stream to a 16 kHz mono `audio.wav` for transcription.
- Warns (without failing) if `ffmpeg` lacks the `ass` filter, since that is needed
  later by the caption-burn step, not by ingest itself.

## Inputs

- `--source <path>`: local path to the recording (mp4, mkv, mov). Required.
- `--event <YYYY-MM-DD>`: event date, used as the output folder name. Required.
- `--audio-track <n>`: 0-based index among audio streams to extract. Default: 0.
- `--force`: overwrite existing outputs.

## Outputs

Written to `clips/work/<event>/` (gitignored, resolved relative to the repo root, not
the caller's working directory):

- `source.mp4`: the normalized video (remuxed copy, or a symlink to the original if it
  was already MP4).
- `audio.wav`: 16 kHz mono PCM s16le audio, extracted from the chosen audio stream.

## How

```
clips/skills/ingest/ingest.sh --source /path/to/recording.mp4 --event 2026-09-17
```

Run it once per event. If outputs already exist, pass `--force` to redo the step.

### Multi-track note

If the OBS recording has a mic-only audio track in addition to the full mix, prefer
the mic-only track for transcription: it is cleaner for ASR than a mix with room
audio, music or other speakers. Use the printed audio stream list to find its index,
then pass `--audio-track <n>`.

MKV is the safe container choice when the recording has multiple audio tracks.
Whether OBS's Hybrid MP4 format handles multi-track audio the same way is
unverified: check the printed stream list for the recording in hand.
