# highlight-selection

## Trigger

An event has a transcript (`clips/work/<event>/transcript.json`) and the human wants a
clip from it: the first one, or one more. Also for a montage: a piece is a candidate
like any other, from any event.

## What it does

A funnel, cheap steps first, then one clip at a time:

1. You read the whole talk and give the human a **long list** of every moment that
   could work as a clip, in the chat only.
2. The human **picks the moment**, from the list or from memory.
3. You define it as a candidate in `clips/work/<event>/highlights.json`, by segment
   index and exact quote, never by timestamp. `preview` places each cut edge on the
   quoted words (on a pause when there is one) and renders a small review file.
4. The human reviews the preview with you: keep, adjust or drop. A kept candidate is
   marked with `choose` and goes on to `produce-clip`.
5. Then the human decides whether to make another: back to 2, the long list stays
   valid.

You never compute timestamps: `clips.py` works from segment indices and quoted words.

## How

1. Run `clips/clips.py render --event <event>` and read the **entire** output before
   picking anything. One line per segment: `[<i>] <mm:ss> <text>`. `<i>` is the index
   you reference; `<mm:ss>` only helps navigate and tell the human when a moment
   happens. `[<i>]~` marks text whisper likely invented: avoid building on it.
   Skip the organisational intro, Q&A whose question is inaudible, and closing
   logistics, unless the human asks for exactly those (e.g. a "chi siamo" montage).

2. Give the human the **long list**, in the chat only: every moment that could work,
   in talk order, up to about 12 for a one hour talk (fewer if the talk has fewer: do
   not pad). Cover the whole talk. Look for a contiguous span that works in under
   60 s, and for a longer span (up to 120 s) whose hook and payoff are kept apart by
   something droppable (a tangent, a stretch that needs the screen, a repetition).
   Start with one table:

   | # | When | Moment | Raw | After drops | Drops | Score | Best uncut |
   |---|------|--------|-----|-------------|-------|-------|------------|

   `Raw` is the span's length, `After drops` its length once the drops are out,
   `Score` (0-100) your confidence it works on its own, `Best uncut` the score of the
   best contiguous span of 60 s or less in the same moment. Lengths are rough, to
   the nearest 5 s, read off the stamps. Star the ones you would pick (about 5). Below
   the table, per moment: one or two sentences, a short key quote, what you would drop,
   caveats (needs the slides, garbled transcript, depends on earlier context). Then
   ask which moment to work on and whether they remember others: they were in the
   room, and their picks outrank yours. If they describe a moment loosely, find it,
   quote it back and have them confirm it.

3. Define the candidate the human picked, appended to `candidates` (leave the others
   as they are). `id` is the next free number and never changes: every file of a clip
   is named after it.

   ```json
   {
     "event": "<YYYY-MM-DD>",
     "source_transcript": "clips/work/<YYYY-MM-DD>/transcript.json",
     "candidates": [
       {
         "id": 1,
         "start_seg": 42,
         "end_seg": 47,
         "start_quote": "<exact text of segment 42>",
         "end_quote": "<exact text of segment 47>",
         "note": "<optional, English: planned drops, doubts to check>"
       }
     ]
   }
   ```

   `start_quote` / `end_quote` are the **exact text** of the first and last segment,
   copied from `render`: they are a checksum, and the edges are placed on their words.
   `clips.py` adds `cut`, `chosen` and `crop`; never write those.

   What makes a good candidate, in rough priority order:
   - Self-contained: makes sense with zero prior context. The first segment must work
     for someone who heard nothing before it: no dangling pronoun, no tail of the
     previous thought, no connective pointing back ("Infatti", "Però", "Quindi")
     unless it opens a complete rhetorical question.
   - Has a hook: a surprising claim, a concrete number, a short story.
   - Works without the slides where possible.
   - Ending: read the two or three segments after `end_seg`; if the punchline is
     there, include it. When a span runs long, cut setup, not payoff.
   - Length: 15 to 60 s, aiming at 25 to 50 s, with margin under 60. A span up to
     120 s only when drops are planned (name them in `note`): one to three large
     drops of whole sentences, each starting and ending on a real pause.
   - Nothing dated or false: no "bentornati dalle vacanze", "ci vediamo a settembre"
     or next-event dates, no claims that are not true today ("non c'era niente di
     simile": other groups exist). A clip lives for months.
   - A recorded sign-off rarely works as an ending (it trails into logistics): end on
     a complete sentence and let the montage end card close.

4. Run:

   ```
   clips/clips.py preview --event <event>
   ```

   It computes the cut of every new or re-spanned candidate, renders its preview
   (`previews/candidate_<id>.mp4`: sentence captions, 5 s of padding either side
   marked "OUTSIDE CUT") and prints its status. `cut edges` says how each edge was
   placed: on a pause; on the words with no pause there (tight against the next word:
   listen to it); not checked against the words (check it in the preview). Status also
   warns when the span is under 15 s (fine only as a montage piece), over 60 s (needs
   drops) or covers text whisper likely invented. A quote that does not match the
   transcript stops the run: copy it again from `render`.

5. Review **with the human**; never decide for them.
   - Open the preview (`open clips/work/<event>/previews/candidate_<id>.mp4`), one
     `open` per file. In the same message: the duration, your honest opinion in a
     line or two, the transcript from the status output, and where the clip starts
     and ends in the player. For planned drops, strike them out in the text and give
     the rough length that remains.
   - The human answers with player times or with words. Apply either; the preview is
     rendered again, open it again and ask again:

     ```
     clips/clips.py choose --event <event> --start 2=0:12 --end 2=0:58
     clips/clips.py choose --event <event> --start-at 2="mi sono dimenticato" --end-after 2="mila euro"
     ```

     `--start` / `--end` take a player time (seconds or `M:SS`, may point into the
     padding). `--start-at` / `--end-after` take words copied from the transcript and
     place the edge on them like `preview` does. `--reset 2` computes the cut again.
   - For a different span rather than a nudge, edit `start_seg` / `end_seg` and the
     quotes, and run `preview` again.

6. When the human keeps it: `clips/clips.py choose --event <event> --ids <id>`
   (`--unchoose <id>` takes it back). Next: `produce-clip` for this clip. If they drop
   it, offer the next moment from the long list.

`clips/clips.py status --event <event> [--ids <id>]` prints the state of the
candidates at any time.
