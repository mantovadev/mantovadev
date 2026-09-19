# produce-clip

## Trigger

`highlight-selection` is done: the human has chosen candidates (`chosen: true` in
`clips/work/<event>/highlights.json`) and their cut points are settled.

## What it does

Turns each chosen candidate into a finished vertical clip, ready for Shorts, Reels
and TikTok:

1. `cut`: frame-accurate cut of the source at full quality.
2. `align`: transcribes that clip on its own for precise word timing and writes an
   editable words file.
3. `burn`: renders a 1080x1920 canvas in the Mantova Dev look: dark brand background,
   logo on top, the picture at full width, word-by-word captions below it (upper
   case, up to 3 words at a time, the spoken word highlighted in brand turquoise) and
   `mantova.dev` at the bottom.

The human reviews the words and the result. You never publish anything.

## Inputs

- `--event <YYYY-MM-DD>`. Required by every subcommand.
- `--ids 1,3`: optional everywhere; the default is every chosen candidate.
- `burn --crop W:H:X:Y`: crop the source picture (in source pixels) before placing
  it, so that speaker and slides show bigger. Remembered per event once given.
- `burn --footer`, `--font`, `--size`, `--active`, `--keep-case`: style overrides.
  The defaults are the agreed look; change them only if the human asks.
- The brand font is Aeonik Pro (Black for captions, Bold for the footer). It is
  proprietary and not in the repo: it must be installed on the machine. If it is
  missing, captions silently render in a fallback font, so check the first result
  and tell the human.
- The logo is `clips/config/brand/logo-dark-bg.png`, a PNG export of
  `assets/svg/logo-orizzontale-chiaro.svg`.

## Outputs

In `clips/work/<event>/final/`, per clip:

- `clip_<id>.mp4`: the plain cut.
- `clip_<id>.words.tsv`: one word per line, `start<TAB>end<TAB>word`, seconds from
  the start of the clip. Meant to be edited.
- `clip_<id>.ass`, `clip_<id>.align.json`: generated, do not edit.
- `clip_<id>.final.mp4`: **the file to publish.**

## How

1. Cut:

   ```
   clips/clips.py cut --event <event>
   ```

2. Once per event, choose the crop. Extract one frame
   (`ffmpeg -ss 10 -i clips/work/<event>/final/clip_<id>.mp4 -frames:v 1 frame.jpg`),
   look at it, and pick the rectangle that keeps the speaker and the slides and drops
   dead space (for the 2026-09-17 room: `1440:1080:0:0`). With a fixed camera one
   rectangle serves the whole event. No crop is fine too: the whole frame is shown,
   smaller.

3. Align:

   ```
   clips/clips.py align --event <event>
   ```

4. Read every `clip_<id>.words.tsv` and fix what Whisper mis-heard: names
   ("Mantua Dev" is "Mantova Dev"), jargon, acronyms ("cp" is "CPU"), command-line
   flags. Edit the word, keep the times. Show the human the full text of each clip
   and ask for corrections; they know the talk.

5. Burn (pass `--crop` the first time):

   ```
   clips/clips.py burn --event <event> --crop 1440:1080:0:0
   ```

6. Open each `clip_<id>.final.mp4` for the human (`open` on macOS, `xdg-open` on
   Linux), one at a time, and ask about text, caption timing and look. Apply the
   feedback and burn again (a burn takes seconds):
   - wrong word: edit the words file;
   - one caption early or late: change that word's start time in the words file;
   - all captions consistently early or late: adjust `DTW_LEAD` at the top of
     `clips/clips.py` and re-run `align --force`.

   Repeat until the human approves.

Order matters: settle the cut first. If the human changes a clip's start or end now,
go back to `choose --start/--end` in `highlight-selection`, then `cut --force`,
`align --force` and `burn` for that clip. `align --force` overwrites the words file,
so hand edits made before it are lost and must be redone.

Why per-clip alignment: word times in the full-talk transcript are only good to about
half a second. Re-transcribing the short clip without VAD and with DTW token
timestamps (`-dtw`, which needs flash attention off) put no word start inside a
detected pause in our test, against 12 to 14% with the default timestamps.
