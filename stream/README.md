# Streaming

OBS setup for streaming meetups on YouTube: brand overlays and a generated scene collection.

- `overlay/`: one HTML page per scene, loaded in OBS as 1920x1080 browser sources. `layouts.js` holds the video slots. The event info comes from the front matter of today's `events/<date>/README.md`, else of the next or newest event folder on GitHub's `main` (pull first). With none, the overlays show a generic Mantova Dev version.
- `obs-scenes.py`: writes an OBS scene collection whose video sources sit on the same slots.

## Scenes

| Scene | Content |
|---|---|
| Inizio | Filler: logo, "Starting soon...", talks, mantova.dev and QR |
| Talk | Speaker screen 16:9 (1536x864), camera cropped 2:3, speaker card (name, role, company, bio), footer with talk title and subtitle |
| Talk 4:3 | Speaker screen 4:3 at full height (1376x1032), side column with camera cropped 2:3, compact card with talk and speaker, logo, mantova.dev and QR |
| Demo | Speaker screen (1536x864) and footer, with the speaker in its label |
| Solo camera | Camera (1536x864) and footer, with the speaker in its label: introductions, Q&A |
| Panel | Camera (1536x864), the speakers' names over the current one's card (in turn, 15 s each), and footer: discussions without slides |
| Pausa | Filler: logo, "Torniamo tra poco", talks, mantova.dev and QR |
| Fine | Filler: logo, "Grazie a tutti!", talks, mantova.dev and QR |

Mic and speaker laptop audio are global devices (`Microfono`, `Audio speaker`), so they are the same in every scene. `Audio speaker` starts muted. The mic has noise suppression, a compressor and a limiter.

## Setup (once per Mac)

1. Generate the collection, copying devices from an existing one (optional):

   ```sh
   python3 stream/obs-scenes.py -o ~/Desktop/mantovadev-obs.json \
     --devices ~/Library/Application\ Support/obs-studio/basic/scenes/Untitled.json
   ```

   The overlay paths in the file are absolute: regenerate it if the repo moves.
2. In OBS, set Settings > Video > Base and Output to 1920x1080 before importing: OBS places the scene items relative to the current canvas.
3. In OBS: Scene Collection > Import, pick the file, then switch to the "Mantova Dev" collection. To reimport, remove the old collection first (Scene Collection > Remove).
4. Check the devices: `Schermo speaker` and `Camera` in their properties, `Microfono` and `Audio speaker` in Settings > Audio. Without `--devices` both audio sources use the default input, so unmuting `Audio speaker` doubles the mic. Set the frame rate to 30; for `Camera`, untick "Use Preset" to see it.
5. Frame the speaker: slots keep the center of the camera image. To pan, select `Camera` in a scene, open Edit Transform (Cmd+E) and crop one side (Crop Left 400 moves the view about 200 camera pixels right); crop top and bottom to zoom in.

## Before each meetup

1. Fill in the front matter of the event's README (from `events/_template.md`): the meetup number and, for each talk, time, title, subtitle and its speakers, each with name, role, company and bio. Only title and the speakers' names are required; list the talks in time order. Keep each bio under 30 words, fewer with a long role: longer bios shrink, and past about 35 words they get cut; up to 3 talks fit the Inizio, Pausa and Fine screens.
2. After editing it, refresh the overlays: in OBS, open each `Overlay ...` source and press "Refresh cache of current page", or restart OBS.
3. Run a short unlisted test stream from the venue.

On the evening, the Talk, Talk 4:3, Demo and Solo camera overlays show the last talk whose `time` has come, and switch on their own. The overlays need internet to load the YAML parser and the font, and on other days to list the events on GitHub; without it, they show the generic version.

## Settings checklist

YouTube figures from its [encoder settings](https://support.google.com/youtube/answer/2853702) page: 1080p30 is 10 Mbps recommended for HEVC, 14 Mbps for H.264; keyframes every 2 s; CBR.

| Where | Setting |
|---|---|
| Output | Output Mode: Advanced |
| Output > Streaming | Encoder: Apple VT HEVC Hardware Encoder; Rate Control: CBR; Bitrate: 10000 Kbps; Keyframe Interval: 2 s |
| Output > Recording | Type: Standard; Format: Fragmented MP4; Encoder: Apple VT HEVC Hardware Encoder; Rate Control: CRF, Quality about 65 (default 60; no official recommendation) |
| Video | Base and Output 1920x1080; FPS 30. Use 25 only if the camera flickers under 50 Hz lights |
| Advanced > Network | Dynamically change bitrate to manage congestion: on |
| General > Output | Automatically record when streaming: on |
| Main window | Studio Mode on: pick the next scene in Preview, then Transition |
| Edit > Advanced Audio Properties | Check lip sync; set a Sync Offset on `Microfono` if the voice is ahead of the camera |

Bitrate 10000 needs about 15 Mbps of stable upload; lower it to 6000 to 8000 on a weak venue connection.
