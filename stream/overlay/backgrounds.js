// The animated backgrounds per theme and page, for background.js. Each map is the screen in 14 x 6 blocks: a digit is how
// thick the stripes get there, in tenths. Colors and maps come from the videos in the design by Giovanni Serretiello.

// How the stripes move, from the timelines of the design: calm behind the video scenes, so it doesn't disturb what's on top,
// faster on Inizio, Pausa and Fine. gaps: frames between moves (30 fps); x, y: how far one move can go (see background.js)
const SOFT = { gaps: [60], x: 60, y: 40 };
const FAST = { gaps: [90, 60, 60, 60, 30], x: 160, y: 100 };
// what the blocks never cover: the fillers' text and logos, and the video scenes' footer and side column
const FILLER_AVOID = ".stage .date, .stage h1, .stage .claim, .stage .follow, .stage img";
const LIVE_AVOID = ".footer img, .footer .center, .side .center, .side img";

const TEAL = { colors: [[0, "#031915", "#2ec5b8"]], grain: 3.9 };
const MAGENTA = { colors: [[0, "#170510", "#b5016a"]], grain: 1.2 };
const MONO = { colors: [[0, "#ffffff", "#000000"]], grain: 0 };
// teal, then magenta, then back; the blocks fade out and in around each change
const TEAL_MAGENTA = {
  colors: [
    [0, "#031915", "#2ec5b8"],
    [65, "#031915", "#2ec5b8"],
    [78, "#181110", "#181110"],
    [90, "#170510", "#170510"],
    [95, "#170510", "#a80460"],
    [112, "#170510", "#b5016a"],
    [246, "#170510", "#b5016a"],
    [260, "#121712", "#121712"],
    [275, "#031915", "#031915"],
    [280, "#031915", "#2abeaf"],
    [297, "#031915", "#2ec5b8"],
  ],
  grain: 1.3,
};
window.BACKGROUNDS = {
  talk: {
    inizio: {
      ...TEAL_MAGENTA,
      map: [
        "43110000000123",
        "41000000000012",
        "30000000000001",
        "20000000000000",
        "00000000000000",
        "00000000000000",
      ],
    },
    live: {
      ...MAGENTA,
      map: [
        "85200000023532",
        "63100000000001",
        "42000000000000",
        "10000000000000",
        "00000000000000",
        "00000000000000",
      ],
    },
    pausa: {
      ...MAGENTA,
      map: [
        "21000000000000",
        "00000000000000",
        "00000000000000",
        "00000000000000",
        "00000000000000",
        "00000155410000",
      ],
    },
    fine: {
      ...TEAL_MAGENTA,
      map: [
        "00000000032334",
        "00000000000012",
        "00000000000001",
        "00000000000001",
        "00000000000000",
        "00000000002101",
      ],
    },
  },
  events: {
    inizio: {
      ...TEAL,
      map: [
        "32100000011223",
        "31000000000001",
        "31000000000001",
        "20000000000000",
        "10000000000000",
        "10000000000000",
      ],
    },
    live: {
      ...TEAL,
      map: [
        "75310000000123",
        "54210000000012",
        "33100000000001",
        "11000000000000",
        "00000000000000",
        "00000000000000",
      ],
    },
    pausa: {
      ...TEAL,
      map: [
        "00011000021100",
        "00000000000000",
        "00000000000000",
        "00000000000000",
        "00000000000000",
        "00000244410000",
      ],
    },
    fine: {
      ...TEAL,
      map: [
        "00011000021100",
        "00000000000000",
        "00000000000000",
        "00000000000000",
        "00000000000001",
        "00000000002113",
      ],
    },
  },
  mono: {
    inizio: {
      ...MONO,
      map: [
        "00000000045115",
        "00000000000002",
        "00000000000002",
        "00000000000000",
        "20000000000000",
        "65200000000000",
      ],
    },
    live: {
      ...MONO,
      map: [
        "53100000012357",
        "62000000000002",
        "40000000000000",
        "20000000000000",
        "00000000000000",
        "00000000000000",
      ],
    },
    pausa: {
      ...MONO,
      map: [
        "20122000000000",
        "00000000000000",
        "00000000000000",
        "00000000000000",
        "00000000000011",
        "00000000001344",
      ],
    },
    fine: {
      ...MONO,
      map: [
        "30011000000001",
        "10000000000001",
        "00000000000004",
        "00000000000015",
        "00000000000037",
        "00000000000258",
      ],
    },
  },
};

for (const pages of Object.values(window.BACKGROUNDS)) {
  for (const [page, background] of Object.entries(pages)) {
    background.motion = page === "live" ? SOFT : FAST;
    background.avoid = page === "live" ? LIVE_AVOID : FILLER_AVOID;
  }
}
