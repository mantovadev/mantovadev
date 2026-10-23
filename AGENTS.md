# AGENTS.md

Mantova Dev is a tech community in Mantova (Italy) that organizes free meetups.

## Layout

- `events/YYYY-MM-DD/`: one folder per meetup. `README.md` in Italian, copied from `events/_template.md` (keep its front matter and section headings), plus talk materials.
- `assets/`: brand assets. `assets/export-png.sh -o <dir>` exports PNGs from `assets/svg/`.
- `clips/`: local pipeline that turns a meetup recording into short vertical clips. Start from `clips/README.md`. Each step is a skill in `clips/skills/<name>/`: read its `SKILL.md` before running its scripts. All run outputs go in the gitignored `clips/work/<event>/`. Ingest with `--preset mantovadev`.
- `stream/`: OBS overlays and scene collection generator for streaming meetups on YouTube. Start from `stream/README.md`. Overlays are in Italian and read per-event info from the front matter of `events/<date>/README.md`.
- `site/`: the mantova.dev landing page, in Italian. Plain static files with no build step; the host serves this folder as is, so every file the page uses must live inside it. Keep it timeless: no dates, times, venues, event frequency or past events.

## Rules

- Inspect first, check in at decision points, and mark unverified claims as unverified.
- Ask before installing software, downloading models, deleting or overwriting files, or pushing.
- Never publish anything. Clips and social copy are drafts that a human reviews and posts.
- Never commit run outputs (media, transcripts, highlights, captions, clips); they live in `clips/work/<event>/`. The clips pipeline never writes into `events/`.
- The clips source video is always a local file path given by a human. A human picks which candidate clips get cut.

## Style

- Community-facing content (event READMEs, captions, social copy) is in Italian, informal and welcoming. Tooling, code comments and agent docs are in English.
- Concise markdown. No em dashes, en dashes, or `--` as sentence punctuation (CLI flags are fine).
- Shell scripts: POSIX where practical, quoted variables, print each action before running it.
