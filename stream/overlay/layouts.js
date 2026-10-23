// obs-scenes.py reads everything after the assignment as strict JSON: quoted keys, no trailing commas, no comments below.
window.LAYOUTS = {
  "talk": { "screen": [24, 24, 1536, 864], "camera": [1584, 24, 312, 468], "card": [1584, 516, 312, 372], "band": [24, 912, 1872, 144] },
  "talk-4x3": { "screen": [24, 24, 1376, 1032], "camera": [1424, 24, 472, 708], "card": [1424, 756, 472, 144], "aside": [1424, 924, 472, 132] },
  "camera": { "camera": [192, 24, 1536, 864], "band": [24, 912, 1872, 144] },
  "big-camera": { "camera": [24, 24, 1376, 864], "screen": [1424, 24, 472, 266], "card": [1424, 314, 472, 574], "band": [24, 912, 1872, 144] },
  "panel": { "camera": [24, 24, 1536, 864], "cards": [1584, 24, 312, 864], "band": [24, 912, 1872, 144] },
  "demo": { "screen": [192, 24, 1536, 864], "band": [24, 912, 1872, 144] }
}
