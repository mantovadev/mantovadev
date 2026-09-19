# ingest

## Trigger

A human hands over the local recording of an event (OBS recording or any other local
video file) and wants clips from it. This is the first step.

## What it does

Prepares `clips/work/<event>/` with a working copy of the video and the audio track
that Whisper needs:

- `.mp4` sources are symlinked (no multi-gigabyte copy). Other containers are remuxed
  to MP4 by stream copy, never re-encoded.
- Lists the audio streams (index, codec, channels, title) so a mic-only track can be
  spotted.
- Extracts one audio stream to 16 kHz mono WAV.
- Warns if `ffmpeg` has no `ass` filter (needed later by `burn`; on macOS install
  `ffmpeg-full`).

## Inputs

- `--source <path>`: the recording (mp4, mkv, mov). Always given by the human.
- `--event <YYYY-MM-DD>`: the event date. Names the work folder.
- `--audio-track <n>`: 0-based index among the audio streams. Default 0.
- `--force`: overwrite existing outputs.

## Outputs

In `clips/work/<event>/` (gitignored): `source.mp4` and `audio.wav`.

## How

```
clips/clips.py ingest --source /path/to/recording.mp4 --event <event>
```

- If the video and the audio arrive as two separate files, mux them first without
  re-encoding (`ffmpeg -i video.mp4 -i audio.webm -map 0:v:0 -map 1:a:0 -c copy
  clips/work/<event>/input.mkv`) and ingest the result.
- If the stream list shows a mic-only track next to the full mix, prefer it
  (`--audio-track <n>`): it transcribes better than a mix with room noise. MKV is the
  safe container for multi-track recordings; multi-track in OBS Hybrid MP4 is
  unverified.
- Next step: `transcript-extract`.
