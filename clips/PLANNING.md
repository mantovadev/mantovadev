# Mantova Dev: Local Clipping Pipeline (plan v2)

Status: planning. Nothing in this folder is implemented yet.
Supersedes the original `PLANNING.md` draft (v1). Changes from v1 are listed under "Decisions".

## Goal

Mantova Dev runs one meetup per month, live-streamed to YouTube via OBS. This project turns each
recording into a small set of short vertical clips (15 to 60 s, 1080x1920) for YouTube Shorts,
Instagram Reels and TikTok, with animated word-by-word captions and minimal manual editing.

Constraints chosen by the maintainers:
- Local and free. No SaaS uploads of speakers' footage.
- Agentic: an LLM agent (Claude Code, Pi, or any harness that can read markdown and run bash)
  drives the steps by following `SKILL.md` files.
- Human in the loop: a person reviews clips and copy, and publishes manually.
- Cross-platform where possible (macOS is the primary machine today).

## Decisions

| # | Decision | Why (evidence) |
|---|----------|----------------|
| 1 | Source video is a local file path passed per run. Usually the OBS recording; a YouTube Studio download also works. | OBS records locally while streaming, at full quality. Studio "Download" only gives 720p or 360p (YouTube Help, answer 56100). |
| 2 | No `yt-dlp` step, and no mention of it in repo docs. | YouTube ToS bans downloading except through routes the Service permits, with no carve-out for owners (youtube.com/t/terms). yt-dlp has also been unreliable since 2025 (yt-dlp issue tracker). |
| 3 | Transcription: `whisper.cpp` with `large-v3-turbo`, Italian, Silero VAD, word-level JSON. Fallbacks: `mlx-whisper` (macOS) or Parakeet v3. | whisper.cpp is MIT, actively maintained, Metal-accelerated, cross-platform. faster-whisper is CPU-only on Mac (CTranslate2 has no Metal). |
| 4 | No DaVinci Resolve stage. | Python scripting is Studio-only ($295) from Resolve 21.1; Smart Reframe and auto-subtitles are Studio-only too. Resolve remains an optional manual polish tool. |
| 5 | Cut and reframe with ffmpeg only. Fixed-coordinate layout, no face tracking. | The OBS scene is fixed (speaker and slides both on screen). Re-measure the crop regions when the setup changes (a capture card is planned). |
| 6 | Captions: `.ass` file with one `Dialogue` event per word, active word highlighted, burned in with ffmpeg. | Pattern proven in OpenShorts (MIT). Avoids Remotion/Chromium and its licence question. |
| 7 | Highlight selection returns the best 5 candidates by segment index plus exact quotes (used as a checksum), never timestamps. Code (`snap_clip.py`) resolves segment boundaries to a cut point snapped to detected silence. | LLMs are unreliable at timestamp arithmetic (TimeStampEval, arXiv 2511.11594; OpenShorts `snap_clip_to_words`). Segment indices are stable and cheap for an agent to reference from `render_transcript.sh`'s numbered output; the quote checksum catches an agent citing the wrong segment. |
| 8 | Human picks from N candidates before any cutting. | A bad clip goes out under the community's name. Closes v1 open question 3. |
| 9 | Publishing stays manual. | Platform APIs need app review, not worth it at one event per month. |
| 10 | Tooling lives in `clips/`. No per-event output is committed: everything (text and media) lives in the gitignored `clips/work/<event>/`. `events/` is human-only and never written by the pipeline. | Keeps `events/` clean for humans and avoids bloating git with per-run artifacts. |

## Pipeline

```
local source file (OBS recording, or Studio download)
      |
      v
[1] ingest              remux to MP4 if needed, extract 16 kHz mono WAV
      |
      v
[2] transcript-extract   whisper.cpp -> punctuated segments + word-level JSON (+ VAD)
      |
      v
[3] highlight-selection  LLM reads full transcript -> best 5 candidates (segment idx + quotes + score)
      |                  code snaps cuts to silence, enforces 15-60 s
      v
      HUMAN PICKS CLIPS
      |
      v
[4] clip-cutter          ffmpeg: frame-accurate cut + fixed crop/vstack to 1080x1920
      |
      v
[5] caption-burn         gen_ass.py: word events -> .ass, burn with ffmpeg
      |
      v
[6] social-copy          LLM: title, hook, caption, hashtags per clip per platform (Italian)
      |
      v
HUMAN REVIEW -> manual publish
```

## Repo layout

```
AGENTS.md                       entry point for any agent (CLAUDE.md imports it)
clips/
  PLANNING.md                   this file
  README.md                     human-facing usage (written in package 1)
  skills/
    ingest/{SKILL.md, ingest.sh}
    transcript-extract/{SKILL.md, transcribe.sh}
    highlight-selection/{SKILL.md, render_transcript.sh, snap_clip.py}
    clip-cutter/{SKILL.md, cut.sh}
    caption-burn/{SKILL.md, gen_ass.py}
    social-copy/{SKILL.md}
  workflows/full-pipeline.md    order of skills, inputs and outputs between them
  schemas/highlights.schema.json
  config/                       layout.json (crop regions), style presets
  work/<date>/                  gitignored: all per-event outputs
    source.mp4, audio.wav       working media
    transcript.json             compact segment + word-level transcript
    highlights.json             candidates + chosen
    candidates.md                human review sheet, from snap_clip.py
    social-copy.md               per-clip, per-platform copy
    final/                       finished clips
```

Skill contract (unchanged from v1): each `SKILL.md` has Trigger, What it does, Inputs, Outputs, How.

## Step details

### [1] ingest
- Input: `--source <path>` (mp4, mkv, mov). Output: `work/<event>/source.mp4` (remuxed if needed) and `audio.wav` (16 kHz mono).
- If the OBS recording has a mic-only audio track, prefer it for the WAV (cleaner for ASR). Unverified whether Hybrid MP4 handles multi-track well; MKV is the safe choice for multi-track.
- Setup check: `ffmpeg -filters | grep -E '\bass\b'` must succeed (Homebrew `ffmpeg` lacks libass; use `ffmpeg-full` on macOS).

### [2] transcript-extract
- `whisper-cli -m ggml-large-v3-turbo -l it --vad ... -ojf` (no `--prompt`, no `-ml 1
  -sow`). `compact.jq` merges each segment's `tokens[]` back into punctuated words,
  keeping both the punctuated segment text and word-level timestamps from one run.
- `-ml 1 -sow` (one whisper.cpp "transcription" entry per word) was dropped: measured
  on the 2026-09-17 recording it kept punctuation on only 30 of 7212 words, which
  step 3 needs to quote sentences. Word end times under that mode also absorbed
  trailing silence (257 words over 2s, worst case 53s); the token-merge approach has
  the same end-time issue, so `compact.jq` clamps every word's end to the next word's
  start capped at 1.5s.
- VAD stays on by default: on the same recording, normal segments with VAD gave 942
  segments (91% ending in sentence punctuation) and 7176 words, vs. 2331 fragment
  segments, only 226 sentence-ending words and a hallucinated "applausi" without VAD
  (`--no-vad`, not recommended for full talks). With `--vad`, token offsets are on
  the VAD-compressed (silence-stripped) timeline and drift over 1100s by the end of
  this 68-minute talk if uncorrected; `compact.jq` shifts each segment's tokens back
  to the segment start to fix this (silence removed inside a segment is not
  recovered).
- Word timing (start included, not just the clamped end) is only rough: see the
  timing contract in `skills/transcript-extract/SKILL.md`. Segment/sentence times
  are the reliable level; steps 3 and 5 treat word times as approximate only.
- The vocabulary/prompt seed was dropped: it did not fix "Mantova Dev" (still came out
  as "Mantua Dev" / "mantuadev" with the file's term first in the prompt) and the
  maintainer judged a static term list not worth maintaining.
- Measured full-transcript run (68 minutes / 4084s of audio, Metal backend, no
  `--threads` override): about 2 min 15 s wall time.
- Check the first and last minutes for hallucinations (silence, applause, music).

### [3] highlight-selection
- `render_transcript.sh` prints the whole transcript as numbered `[<i>] <mm:ss> <text>`
  lines so the agent reads the full talk in one pass instead of in windows (dropped
  the windowed-with-overlap approach from v1: full-talk reading is cheap enough at
  this length and avoids picks clustering per-window).
- Criteria: self-contained, strong or counterintuitive claim, works without visual aid, no filler start, hook then value then payoff. Best 5 candidates, ranked, spread across the talk.
- Output: `highlights.json` (see schema) with segment indices, quoted start/end
  segment text as a checksum, score, hook (Italian), reason (English),
  `needs_visuals`.
- Segment times are reliable (see [2]'s timing contract); `snap_clip.py` computes the
  cut from segment/word boundaries and snaps the actual cut point to nearby silence
  detected with `ffmpeg silencedetect` (cached per event in
  `clips/work/<event>/silences.json`) rather than trusting a word time directly, adds
  a small lead (at most 0.35 s) and tail (at most 0.45 s) limited to half the
  neighbouring silence, and flags (does not auto-fix) candidates under 15 s or over
  60 s, quote mismatches, and overlaps. `snap_clip.py choose` marks the human's picks
  as `chosen` and supports manual start/end shifts. Both subcommands (re)generate
  `candidates.md`, a plain-text review sheet with time range, flags, hook, reason,
  transcript text and an `ffplay` preview command per candidate. A human can still
  adjust the cut point interactively before cutting.

### [4] clip-cutter
- `-ss` before `-i` with transcode gives frame-accurate cuts. Never stream-copy.
- Layout from `config/layout.json`: slides region on top, speaker region below, `vstack`, pad to 1080x1920.
- Encode `libx264 -crf 18 -preset medium -pix_fmt yuv420p -movflags +faststart`, AAC. `h264_videotoolbox` is an optional Mac-only speed path (no CRF; quality unbenchmarked).

### [5] caption-burn
- One `Dialogue` event per word, running until the next word starts, active word styled via override tags, reset with `\r`. Write `utf-8-sig`. Pin a font with `fontsdir`. Use neutral file names (apostrophes and colons break the filter string).
- Must not use the full-talk transcript's word times (only rough, see [2]). Re-aligns
  words per chosen clip instead (15 to 60 s of audio is cheap to re-run). Candidates:
  a short per-clip whisper pass without VAD, optionally with `-dtw large.v3.turbo`
  (requires `--no-flash-attn`, which also changes the decoded text, so no fair
  accuracy comparison has been made yet); or a forced aligner. Approach to be decided
  in this step's own work package. A human can correct caption text and timing
  before burn-in.

### [6] social-copy
- Per clip and per platform: hook line, caption, hashtags, in Italian. Separate prompts per platform.

## Output spec (safe default)

1080x1920, 30 fps, H.264 + AAC MP4, 15 to 60 s. Shorts allow up to 3 minutes (since 2024-10-15) but the 15 to 60 s target stays.

## Work packages

1. **Foundation + transcription.** Scaffold `clips/`, add gitignore rules for media, write `ingest` and `transcript-extract`. Run on the 2026-09-17 recording; check Italian accuracy and word timing. Optionally compare against the YouTube Studio `.vtt` for that event. Check in.
2. **Highlight selection.** Prompt, schema, `snap_clip.py`. Tune on two past events. Check in.
3. **Cut and layout.** Measure crop regions, write `cut.sh` and `layout.json`. Get one ugly-but-working vertical clip. Check in.
4. **Captions.** `gen_ass.py`, burn-in, style preset. Check in.
5. **Social copy + workflow doc.** `social-copy` skill, `workflows/full-pipeline.md`, `clips/README.md`.
6. **Polish (optional).** OBS recording checklist for volunteers, capture-card layout re-measure, optional Resolve-based manual polish notes.

## Prerequisites (need approval before installing)

- `brew install ffmpeg-full whisper-cpp`
- Whisper model `ggml-large-v3-turbo` (about 1.5 GB), stored in a gitignored `models/` folder.

## OBS recording checklist (for the streamer)

- Record locally while streaming (Settings > General > "Automatically record when streaming", or press Start Recording).
- Use a separate recording encoder if the machine can afford it; "Same as stream" caps quality at the stream bitrate.
- Format: Hybrid MP4 (default in OBS 32) or MKV. Both survive crashes.
- Put the speaker mic on its own audio track in addition to the full mix, if practical.
- Vertical recording in OBS is not required. OBS 32.x has no native vertical recording UI; reframe in post.

## Open questions and unverified items

- Whisper Italian accuracy on real room audio with English tech terms: no benchmark found, only clean-speech figures (FLEURS). Test in package 1.
- Answered: word-level timing from the full-transcript run (any mode) is not precise
  enough for karaoke captions on its own. Measured: VAD vs. no-VAD word starts
  disagree on matching long words by a median of 0.66 s (p90 2.3 s), and 8.9% of word
  starts fall inside silence detected by `ffmpeg silencedetect` (a proxy, not
  human-verified). Caption-burn re-aligns words per clip instead; see [5].
- Whether `-dtw large.v3.turbo` (requires `--no-flash-attn`) improves per-clip
  caption timing over plain decoding: tried briefly on a slice, but `--no-flash-attn`
  also changes the decoded text, so no fair accuracy comparison was made. To settle
  in caption-burn's work package.
- Whether YouTube Studio captions have word-level timing, and their Italian quality. Unverified; test with a downloaded `.vtt`.
- Answered: measured full-talk transcription time on this Mac is about 2 min 15 s for
  68 minutes / 4084 s of audio (VAD run, Metal backend, no `--threads` override).
- Resolve 21.1 scripting claims rely on Puget Systems and CineD quoting Blackmagic's notes, not on Blackmagic's own text.
- Studio download of live-stream VODs is confirmed only by third-party pages, and its resolution cap for VODs is unknown.
- Official Instagram Reels and TikTok organic upload specs were not reached (only ad specs and third-party guides).
- Speaker consent: even with local processing, clips publish speakers' faces and voices. Decide a consent routine before publishing.

## Reuse notes

Do not adopt an existing clipper wholesale. Borrow ideas or fragments from OpenShorts (https://github.com/mutonby/openshorts, MIT core, `cloud/` under a separate licence): `snap_clip_to_words`, the stacked screencast filtergraph, the per-word ASS generator. Check each file's licence before copying. ClipsAI is stale (last push 2024-01-17).
