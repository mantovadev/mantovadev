// obs-scenes.py reads everything after the assignment as strict JSON: quoted keys, no trailing commas, no comments below.
// All whole pixels: with fractions, the browser snaps the boxes and OBS places the video off its frame.
window.LAYOUTS = {
  "talk": { "screen": [81, 57, 1391, 782], "camera": [1548, 57, 291, 409], "card": [1547, 467, 293, 373] },
  "talk-4x3": { "screen": [81, 74, 1245, 932], "camera": [1401, 74, 438, 525], "side": [1400, 599, 440, 408] },
  "live": { "screen": [228, 57, 1464, 823] },
  "big-camera": { "camera": [81, 74, 1245, 932], "screen": [1401, 74, 438, 246], "side": [1400, 320, 440, 687] },
  "panel": { "camera": [81, 57, 1391, 782], "cards": [1547, 56, 293, 784] }
}
