# ingest

## Trigger

A human hands over the local recording of an event and wants clips from it. First step.

## What it does

Prepares `clips/work/<event>/`: `source.mp4` (a symlink to an `.mp4` source, else a
stream-copy remux, never re-encoded), `audio.wav` (one audio track, 16 kHz mono) and
`ingest.json` (which file and track were used). It lists the audio streams, and stops
if the extracted audio is shorter than the recording: a track that covers only part
of the talk would silently lose the rest.

## How

```
clips/clips.py ingest --event <event> --source /path/to/recording.mp4 [--audio-track <n>]
```

- The source path is always given by the human.
- If the stream list shows a mic-only track next to the full mix, prefer it: it
  transcribes better.
- If video and audio arrive as two files, mux them first without re-encoding
  (`ffmpeg -i video.mp4 -i audio.webm -map 0:v:0 -map 1:a:0 -c copy
  clips/work/<event>/input.mkv`) and ingest the result.

Next step: `transcript-extract`.
