# transcript-extract

## Trigger

`ingest` is done and the talk needs a transcript for highlight selection.

## What it does

Runs `whisper-cli` (`large-v3-turbo`, Italian, Silero VAD) on the full audio and
writes `clips/work/<event>/transcript.json`: punctuated sentence-like segments
(`i`, `s`, `e`, `text`) and rough word times (`w`, `s`, `e`, `seg`). A few minutes for
a one hour talk. `--compact-only` rebuilds `transcript.json` from the raw output
without running whisper again.

Times are rough: word times about half a second at best, and whole stretches can
come out as even one-second segments. Later steps never cut on them: `preview` and
`choose` align the audio around each cut edge again, and `cut` aligns each clip's
words for the captions.

## How

```
clips/clips.py transcribe --event <event>
```

The report lists the stretches whisper likely invented (the same sentence three or
more times in a row, typical over applause or noise); `render` marks their segments
`[i]~`. Then read the first and the last minute of `clips/clips.py render --event
<event>`: whisper can also invent text over silence that is not repeated. Mis-heard
names and jargon are expected and get fixed per clip later.

Models are never downloaded automatically: if one is missing, see
`clips/README.md` and ask the human.

Next step: `highlight-selection`.
