# produce-clip

## Trigger

The human has kept a candidate in `highlight-selection` (`chosen: true`) and its cut
is settled. This skill takes that one clip to the finish before another is started.
It also joins finished clips into a montage.

## What it does

1. `cut`: frame-accurate cut of the source, then a fresh transcription of the clip
   for precise word timing, written to an editable words file with the preset's
   glossary applied.
2. `tighten` (optional): drops stretches the human agreed to lose and shortens long
   pauses. This is how a raw cut of up to 120 s becomes a clip under 60 s.
3. `burn`: the 1080x1920 clip in the preset's look (background, logo, the picture at
   full width, word-by-word captions, the footer text at the bottom),
   brought to a common loudness (-14 LUFS). Everything stays clear of the TikTok and
   Shorts UI (top ~230 px, bottom ~345 px, action buttons on the right).
4. `cover`: a 1080x1920 still to upload as the post's cover (Instagram, TikTok,
   YouTube Studio): logo, a frame of the clip and a large title, kept inside the 3:4
   and 4:5 crops of profile grids and feeds.
5. `join`: the end card every post ends with, and montages of finished clips from
   any events.

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
   when nothing the clip needs falls outside; keep the rectangle's height at most 0.85
   of its width (920 px on the canvas), or `burn` refuses it. Other clips of the event
   show earlier crops (`crop` in `highlights.json`): reuse one if the camera did not
   move.

3. Read the words file and fix what whisper mis-heard: jargon, acronyms ("cp" is
   "CPU"), flags. Edit the word, keep the times. A name it keeps getting wrong goes in
   the preset's `glossary.tsv` instead. Show the human the clip's text and ask for
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
   - Pauses of 0.4 s or more are shortened to about 0.3 s. Estimate the gain from the
     pauses in the report, not from gaps in the words file. Drops are what shortens a
     clip.
   - `--fillers` also cuts hesitations (eh, ehm, mmh) between words; the report lists
     each one. Pass it on every run once the human wants them out.
   - The report flags sound with no words (a hesitation or a false start whisper left
     out). Ask the human; to cut it, `--drop '<id>=<start>-<end>'` in clip seconds, as
     the report prints them. Report times are the tightened player's.
   - Every run plans from scratch: give all the drops each time. The clip's start and
     end are not `tighten`'s job: move them with `choose`, then `cut --force`.
   - Open `clip_<id>.tight.mp4` and ask about each splice.

   Optional, when the human wants to fine-tune the cut by hand in a video editor:

   ```
   clips/clips.py edit --event <event> --ids <id>
   ```

   It writes `clip_<id>.otio`, a timeline of the cut and the tighten plan, on
   `clip_<id>.edit.mp4`: the recording from 5 s before the cut to 5 s after it, with
   plain captions. The human opens the `.otio` in their editor,
   trims, deletes, drags edges into the extra seconds, and exports the timeline as
   `clip_<id>.edited.otio` in the same folder. From then on `burn` and `cover` use that
   export (the audio track decides the cut) instead of the tighten plan; delete it to
   go back. After `choose` and `cut --force`, `burn` refuses an old export: rename it, run
   `edit` again and redo the edits. A fixed word in the words file shows in the editor after `edit` runs again.

5. Burn, with `--crop W:H:X:Y` if you decided one (it is stored; `none` clears it):

   ```
   clips/clips.py burn --event <event> --ids <id> [--crop <W:H:X:Y>]
   ```

6. Open `clip_<id>.final.mp4` and ask about text, caption timing and look. A wrong word
   or one late caption: edit the words file (for a tightened clip, find the word by its
   text; the player shows tightened times) and burn again. Repeat until approved.

7. Propose the post text in the chat, as the preset's `conventions.md` says (no
   preset conventions: ask the human for language and tone). Only what the clip says,
   no hype, nothing dated.

8. Cover. Propose a cover title with the post text: 1 to 3 short lines (at most 12
   characters each), the topic in a few words, not the post title verbatim. Then pick a
   frame: the speaker still (no motion blur), facing the room, ideally with a slide
   that matches the title. A contact sheet helps the human choose:

   ```
   ffmpeg -i clips/work/<event>/final/clip_<id>.final.mp4 -vf "select='isnan(prev_selected_t)+gte(t-prev_selected_t,1.5)',scale=384:-2,tile=5x8" -frames:v 1 clips/work/<event>/final/clip_<id>.frames.png
   ```

   ```
   clips/clips.py cover --event <event> --ids <id> --at <seconds> --title "Mantova Dev" --title "Chi siamo?" [--accent 1]
   ```

   Tile n of the sheet (from 0, row by row) is at n × 1.5 s. `--at` is in the final
   clip's time, as its player shows it; `--accent` is the line in the accent colour
   (default the last). Open `clip_<id>.cover.png` and iterate until approved. For a
   montage, draw it from one of its pieces.

9. Save the post outside the repo, in a folder of its own, as the preset's
   `conventions.md` says: the montage with its end card, the cover and the approved
   text. Ask where, and ask before replacing an existing folder.

10. Ask whether they want another clip; if so, back to `highlight-selection`.

## Montage

Every post ends with an end card, so a single clip is a montage of one piece. For a
montage (e.g. "chi siamo"), finish each piece as above, then write
`clips/work/<slug>/montage.json` and run `clips/clips.py join --montage <slug>`:

```json
{
  "pieces": ["2026-06-18:1", "2026-09-17:3", "2026-04-09:7"],
  "card": {"lines": ["COS'È", "MANTOVA DEV?"], "subtitle": "Vieni al prossimo incontro!"}
}
```

Card lines: the clip's topic or the montage's message, as short as the cover title.
Subtitle: a short payoff picked for each post with the human (e.g. "Game dev con
Godot", "Vieni al prossimo incontro!"), or none.

Pieces play in order, as plain cuts, at the same loudness. The end card shows
the logo, the lines (the last in the accent colour, or line `"accent": n`), the subtitle and the link
(default the preset's footer) for 3 s. The result is `clips/work/<slug>/<slug>.mp4`. Pieces
can be short (8 to 25 s each); keep the whole under 90 s and the middle brisk.

Optional, to rearrange by hand: `join` also writes `clips/work/<slug>/<slug>.otio`, the
finished pieces and the end card on one timeline. The human reorders, trims or drops
pieces in their editor and exports it as `<slug>.edited.otio` in the same folder; from
then on `join` follows that export (its audio track) instead of the order in
`montage.json`. Delete it to go back. After a piece is burned again or the piece list
changes, `join` refuses the old export. Work on a piece's own cut (edges past it,
captions, crop) happens on that piece, with `edit` and `burn`.
