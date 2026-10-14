# Mantova Dev conventions

Read by the agent in `produce-clip`. Aeonik Pro (proprietary, not in the repo) must be
installed on the machine: without it, captions fall back to a default font.

## Post text

Italian, informal, no hype, only what the clip says and what
`events/<event>/README.md` holds. Title and caption are about the talk's topic, in
plain words: no teaser, no striking numbers. Earlier `post.txt` files in the delivery
folder show the tone. A title of at most 60 characters, a caption of 2 or 3 short
sentences ending with an invitation to the next meetup and `https://mantova.dev`, 5 to
8 hashtags (`#MantovaDev` `#Mantova` plus topic ones). Name the speaker only if the
event README does. Nothing dated.

## Delivery

One folder per post, default in `~/Movies/MD`:

```
YYYYMMDD-mantovadev-<slug>/   # today's date
  video.mp4                   # the montage, with its end card
  cover.png                   # clip_<id>.cover.png
  post.txt                    # the approved text, as below
```

```
TITOLO
<title>

DIDASCALIA
<caption>

HASHTAG
<hashtags>
```
