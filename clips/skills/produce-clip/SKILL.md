# produce-clip

## Trigger

The human has kept a candidate in `highlight-selection` (`chosen: true`) and its cut
is settled. This skill takes that one clip to the finish before another is started.
It also joins finished clips into a montage.

## What it does

1. `cut`: frame-accurate cut of the source, then a fresh transcription of the clip
   for precise word timing, written to an editable words file with the glossary
   (`clips/config/glossary.tsv`) applied.
2. `tighten` (optional): drops stretches the human agreed to lose and shortens long
   pauses. This is how a raw cut of up to 120 s becomes a clip under 60 s.
3. `burn`: the 1080x1920 clip in the Mantova Dev look (brand background, logo, the
   picture at full width, word-by-word captions, `https://mantova.dev` at the bottom),
   brought to a common loudness (-14 LUFS).
4. `join` (montages only): finished clips from any events plus an end card.

Pass `--ids <id>` on every command: without it a command acts on every chosen
candidate. Files are in `clips/work/<event>/final/`: `clip_<id>.mp4` (the cut),
`clip_<id>.words.tsv` (`start<TAB>end<TAB>word`, clip seconds: the file you edit),
`clip_<id>.tight.mp4` (tightened, no captions) and `clip_<id>.final.mp4` (the result).

## How

1. Cut and align:

   ```
   clips/clips.py cut --event <event> --ids <id>
   ```

   `--force` cuts again after the edges changed; it also rewrites the words file, so
   hand edits are lost and `tighten` must run again.

2. Decide the crop. Look at a frame
   (`ffmpeg -ss 10 -i clips/work/<event>/final/clip_<id>.mp4 -frames:v 1
   clips/work/<event>/final/clip_<id>.jpg`). The picture always fills the canvas
   width, so cutting dead space off the sides makes speaker and slides bigger. Crop
   when nothing the clip needs falls outside; keep the rectangle at least as wide as it
   is tall. Other clips of the event show earlier crops (`crop` in `highlights.json`):
   reuse one if the camera did not move.

3. Read the words file and fix what whisper mis-heard: jargon, acronyms ("cp" is
   "CPU"), flags. Edit the word, keep the times. A name it keeps getting wrong goes in
   `clips/config/glossary.tsv` instead. Show the human the clip's text and ask for
   corrections.

4. Tighten, when the clip is over 60 s or the human wants something out:

   ```
   clips/clips.py tighten --event <event> --ids <id> --drop '<id>=<first words> ... <last words>'
   ```

   - What to drop is the human's call. Propose it as the clip's text with the
     stretches struck out, one to three large drops; never single filler words, never
     reorder or reword. Quote the words as in the words file.
   - Both edges of a drop must land on a real pause, or `tighten` refuses it: widen it
     to the nearest pauses, or leave the stretch in.
   - Pauses of 0.7 s or more are shortened to about half a second. On a fluent
     speaker this gains little: estimate the gain from the pauses in the report, not
     from gaps in the words file. Drops are what shortens a clip.
   - Every run plans from scratch: give all the drops each time. The clip's start and
     end are not `tighten`'s job: move them with `choose`, then `cut --force`.
   - Open `clip_<id>.tight.mp4` and ask about each splice.

5. Burn, with `--crop W:H:X:Y` if you decided one (it is stored; `none` clears it):

   ```
   clips/clips.py burn --event <event> --ids <id> [--crop <W:H:X:Y>]
   ```

6. Open `clip_<id>.final.mp4` and ask about text, caption timing and look. A wrong word
   or one late caption: edit the words file (for a tightened clip, find the word by its
   text; the player shows tightened times) and burn again. Repeat until approved.

7. Propose the post text in the chat: Italian, informal, no hype, only what the clip
   says and what `events/<event>/README.md` holds. A title of at most 60 characters,
   a caption of 2 or 3 short sentences ending with an invitation to the next meetup and
   `https://mantova.dev`, 5 to 8 hashtags (`#MantovaDev` `#Mantova` plus topic ones).
   Name the speaker only if the event README does. Nothing dated. When approved, and
   if the human asks, save video and text outside the repo as
   `YYYYMMDD-mantovadev-<slug>.mp4` and `.txt` (e.g. in `~/Movies/MD`).

8. Ask whether they want another clip; if so, back to `highlight-selection`.

## Montage

For a montage (e.g. "chi siamo"), finish each piece as above, then write
`clips/work/<slug>/montage.json` and run `clips/clips.py join --montage <slug>`:

```json
{
  "pieces": ["2026-06-18:1", "2026-09-17:3", "2026-04-09:7"],
  "card": {"lines": ["CI VEDIAMO", "AL PROSSIMO", "INCONTRO!"], "subtitle": "Gratuito e aperto a tutti"}
}
```

Pieces play in order, as plain cuts, at the same loudness. The optional end card shows
the logo, the lines (the last in turquoise), the subtitle and the link (default
`https://mantova.dev`) for 3 s. The result is `clips/work/<slug>/<slug>.mp4`. Pieces
can be short (8 to 25 s each); keep the whole under 90 s and the middle brisk.
