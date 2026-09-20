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
3. `tighten` (optional, per clip): drops stretches of speech the human agreed to lose
   (a tangent, a stretch that needs the screen, a false start) and shortens long
   pauses. This is how a raw cut of up to 110 s becomes a clip under 60 s.
4. `burn`: renders a 1080x1920 canvas in the Mantova Dev look: dark brand background,
   logo on top, the picture at full width, word-by-word captions below it (upper
   case, up to 3 words at a time, the spoken word highlighted in brand turquoise) and
   `https://mantova.dev` at the bottom.

The human reviews the words and the result. The step ends with a proposed post text
for each approved clip, given in the chat. You never publish anything.

## Inputs

- `--event <YYYY-MM-DD>`. Required by every subcommand.
- `--ids 1,3`: optional everywhere; the default is every chosen candidate.
- `tighten --drop 'ID=first words ... last words'`: remove that stretch of speech
  (one phrase without ` ... ` drops just that phrase). Repeatable. `--replan`: start
  the plan over. How pauses are shortened is set by the `TIGHT_*` values at the top of
  `clips/clips.py`.
- `burn --crop W:H:X:Y`: crop the source picture (in source pixels) before placing
  it, so that speaker and slides show bigger. Without `--ids` it is remembered for the
  whole event; with `--ids` only for those clips, whose own crop then wins (for when
  the speaker stands somewhere else, as in a Q&A).
- `burn --no-tighten`: burn the plain cut even though a tightened one exists.
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
- `clip_<id>.tighten.json`: the tighten plan, one entry per cut with a reason (`drop`
  or `pause`). Set `"apply": false` on a cut to keep that stretch after all, then run
  `tighten` again.
- `clip_<id>.tight.mp4`: the tightened clip, without captions. `burn` uses it
  automatically when it exists and moves the caption times itself: the words file
  stays the only file you edit, always in the plain cut's times.
- `clip_<id>.ass`, `clip_<id>.align.json`, `clip_<id>.tighten.graph`: generated, do
  not edit.
- `clip_<id>.final.mp4`: **the file to publish.**

## How

1. Cut:

   ```
   clips/clips.py cut --event <event>
   ```

2. Choose the crop, once per event. Extract one frame
   (`ffmpeg -ss 10 -i clips/work/<event>/final/clip_<id>.mp4 -frames:v 1 frame.jpg`),
   look at it, and pick the rectangle that keeps the speaker and the slides and drops
   dead space (for the 2026-09-17 room: `1440:1080:0:0`). With a fixed camera one
   rectangle serves the whole event. No crop is fine too: the whole frame is shown,
   smaller. Check one frame of every clip all the same: when the speaker has moved
   (standing for the Q&A, away from the desk), give that clip its own rectangle with
   `burn --ids <id> --crop ...`.

3. Align:

   ```
   clips/clips.py align --event <event>
   ```

4. Read every `clip_<id>.words.tsv` and fix what Whisper mis-heard: names
   ("Mantua Dev" is "Mantova Dev"), jargon, acronyms ("cp" is "CPU"), command-line
   flags. Edit the word, keep the times. Show the human the full text of each clip
   and ask for corrections; they know the talk.

5. Tighten, for the clips that need it: every clip over 60 s, and any clip where the
   human wants something out. Skip it for the others.

   ```
   clips/clips.py tighten --event <event> --ids 4 \
     --drop '4=Diciamo che se anche ... non gliela faccio vedere.'
   ```

   - What to drop is an editorial choice the human makes. Propose it in the chat as
     the clip's text with the stretches struck out, and say what each one is (tangent,
     needs the screen, repetition, false start). Prefer one to three large drops: every
     drop is a visible jump, because the camera is fixed and wide. Do not drop single
     filler words. Never reorder or reword what the speaker says, and re-read the text
     that remains: it must still say what they meant.
   - Quote the words as written in the words file (after your fixes in step 4).
   - Both edges of a drop must land on a real pause, otherwise the cut splits words
     and `tighten` refuses it. Then widen the drop to the nearest pauses, which can
     cost a sentence you wanted to keep, or leave the stretch in. The report prints the
     remaining text: read it again after widening.
   - Pauses of 0.7 s or more are shortened to about half a second. Shorter ones are
     speech rhythm and stay: cutting them sounded rushed when we tried. On a fluent
     speaker this gains a few percent, so it is polish, not the point.
   - The clip's start and end are not `tighten`'s job. If the cut opens on the tail
     of the previous sentence or ends mid-thought, fix it with `choose` (see "Order
     matters" below). A clip that ends on the speaker's own closing line and a real
     pause beats a shorter one that stops abruptly.
   - Open `clip_<id>.tight.mp4` for the human and ask about each splice: does it sound
     clean, does the jump look acceptable. `tighten` prints a warning while the result
     is still over 60 s.

6. Burn (pass `--crop` the first time):

   ```
   clips/clips.py burn --event <event> --crop 1440:1080:0:0
   ```

7. Open each `clip_<id>.final.mp4` for the human (`open` on macOS, `xdg-open` on
   Linux), one at a time, and ask about text, caption timing and look. Apply the
   feedback and burn again (a burn takes seconds):
   - wrong word: edit the words file;
   - one caption early or late: change that word's start time in the words file (for
     a tightened clip the player shows tightened times: find the word by its text and
     move its start by the amount the human gives);
   - all captions consistently early or late: adjust `DTW_LEAD` at the top of
     `clips/clips.py` and re-run `align --force`.

   Repeat until the human approves.

8. When the human approves a clip, propose the post text for it in the chat. Do not
   write it to a file: nothing about a run is kept in the repo, and the human copies
   what they like.
   - Italian, informal and welcoming, like the root `README.md`. No hype and no
     invented facts: use only what is said in the clip and what is in
     `events/<event>/README.md` (talk title, speaker, links).
   - Per clip: a title of at most 60 characters (needed for YouTube Shorts, works as
     the first line elsewhere); a caption of 2 or 3 short sentences (the hook, what
     the clip shows, an invitation to the next meetup with `https://mantova.dev`);
     5 to 8 hashtags mixing community ones (`#MantovaDev` `#Mantova`) and topic ones.
   - Name the speaker only if the event README names them.
   - One proposal, then adjust on feedback. The same text serves all three platforms
     unless the human asks for per-platform versions.

Order matters: settle the cut first. If the human changes a clip's start or end now,
go back to `choose --start/--end` in `highlight-selection`, then `cut --force`,
`align --force`, `tighten --replan` (with the drops again) and `burn` for that clip.
`align --force` overwrites the words file, so hand edits made before it are lost and
must be redone. Whisper also words things a little differently on each run: copy the
`--drop` quotes from the new words file. Adding or removing a line in the words file
after `tighten` needs `tighten --replan` too; changing a word or a time does not.

Why drops need pauses: a cut inside running speech clips consonants however good the
word times are, and on this footage the jump is visible too. Pauses are measured on
the clip's audio, first at the noise floor `snap` uses, then at two louder floors,
because a noisy stretch (Q&A, audience) never gets that quiet. We tried a zoom change
at each drop to make the jump look deliberate: it added little, so it was left out.

Why per-clip alignment: word times in the full-talk transcript are only good to about
half a second. Re-transcribing the short clip without VAD and with DTW token
timestamps (`-dtw`, which needs flash attention off) put no word start inside a
detected pause in our test, against 12 to 14% with the default timestamps.
