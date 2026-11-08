// Animated block background on a 1920x1080 canvas (scale it with CSS if needed): horizontal stripes on a grid of cells, cut
// by a density map, that slide and bend at random but move the way the timeline in the design does. It never repeats.
// No dependencies, so the website can use it too.
//   map: rows of digits, the share of rows lit in tenths, spread smoothly over the grid
//   colors: [frame, background, blocks] keyframes over a 300-frame loop at 30 fps; one entry for a fixed palette
//   grain: strength of the specks on the blocks, 0 for none
//   motion: { gaps, x, y }: frames from one move to the next (one picked at random each time), and how far one move can go
//           along x (slide, % of the distance between stripes) and y (bend, % of a wave)
//   avoid: CSS selector of the page's text and logos: the blocks never come within MARGIN px of them
window.playBackground = (canvas, { map, colors, grain, motion, avoid }) => {
  const COLS = 56;
  const ROWS = 24;
  const PERIOD = 4; // rows from one stripe to the next
  const LOOP = 300;
  const XS = [0, ...Array.from({ length: 55 }, (_, k) => 15 + 35 * k), 1920];
  const YS = [0, ...Array.from({ length: 23 }, (_, k) => Math.floor(40 + 45.5 * k)), 1080];
  const rand = Math.random;
  // from the design: the timeline's reach, its easing (a quick start, a long settle), the bend and where the stripes rest
  const REACH = [80, 50];
  const EASE = [0.19847, 0, 0.29771, 1];
  const WAVE = 0.97; // amplitude of the bend in rows: 2.3% of 1920 px, over a 45.5 px row
  const PHASE = 0.8; // where the stripes sit at offset 0, in rows
  const MARGIN = 40;

  canvas.width = 1920;
  canvas.height = 1080;
  const ctx = canvas.getContext("2d", { alpha: false });

  // black and white specks at low alpha, about the given spread in brightness on a mid-tone block
  const tile = document.createElement("canvas");
  tile.width = tile.height = 256;
  const tileCtx = tile.getContext("2d");
  const image = tileCtx.createImageData(256, 256);
  for (let i = 0; i < image.data.length; i += 4) {
    image.data[i] = image.data[i + 1] = image.data[i + 2] = rand() < 0.5 ? 0 : 255;
    image.data[i + 3] = rand() * grain * 3.5;
  }
  tileCtx.putImageData(image, 0, 0);
  const noise = ctx.createPattern(tile, "repeat");

  // the map spread over the cells, keeping only those that can light up
  const digit = (x, y) => +map[Math.min(map.length - 1, Math.max(0, y))][Math.min(map[0].length - 1, Math.max(0, x))] / 10;
  const lit = [];
  for (let row = 0; row < ROWS; row++) {
    for (let col = 0; col < COLS; col++) {
      const fx = ((col + 0.5) / COLS) * map[0].length - 0.5;
      const fy = ((row + 0.5) / ROWS) * map.length - 0.5;
      const x = Math.floor(fx);
      const y = Math.floor(fy);
      const a = fx - x;
      const b = fy - y;
      const share = (digit(x, y) * (1 - a) + digit(x + 1, y) * a) * (1 - b) + (digit(x, y + 1) * (1 - a) + digit(x + 1, y + 1) * a) * b;
      if (share > 0) lit.push({ row, col, share });
    }
  }

  // with avoid, the cells near the text and logos stay dark. Measured at every frame, so a logo that loads late, a ticker or
  // a new talk is never covered. Text counts as far as it shows: its glyphs, clipped to its own box; a box with its own
  // background (a card) counts whole
  const clear = () => {
    if (!avoid) return lit;
    const box = canvas.getBoundingClientRect();
    if (!box.width) return [];
    const k = 1920 / box.width;
    const rects = [];
    for (const el of document.querySelectorAll(avoid)) {
      let r = el.getBoundingClientRect();
      if (!el.matches("img, svg, canvas, video") && getComputedStyle(el).backgroundColor === "rgba(0, 0, 0, 0)") {
        const range = document.createRange();
        range.selectNodeContents(el);
        const t = range.getBoundingClientRect();
        r = { left: Math.max(r.left, t.left), top: Math.max(r.top, t.top), right: Math.min(r.right, t.right), bottom: Math.min(r.bottom, t.bottom) };
      }
      if (r.right <= r.left || r.bottom <= r.top) continue;
      rects.push([(r.left - box.left) * k - MARGIN, (r.top - box.top) * k - MARGIN, (r.right - box.left) * k + MARGIN, (r.bottom - box.top) * k + MARGIN]);
    }
    return lit.filter(({ row, col }) => !rects.some(([l, t, r, b]) => XS[col] < r && XS[col + 1] > l && YS[row] < b && YS[row + 1] > t));
  };

  // CSS-style cubic-bezier easing: the x of the curve is time, solved by Newton's method
  const ease = (x) => {
    const [x1, y1, x2, y2] = EASE;
    const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
    const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    let t = x;
    for (let i = 0; i < 8; i++) {
      const err = ((ax * t + bx) * t + cx) * t - x;
      const slope = (3 * ax * t + 2 * bx) * t + cx;
      if (Math.abs(err) < 1e-6 || Math.abs(slope) < 1e-6) break;
      t -= err / slope;
    }
    t = Math.min(1, Math.max(0, t));
    return ((ay * t + by) * t + cy) * t;
  };
  // the next keyframe [frame, x, y] is picked when the last one is reached: a gap from motion.gaps, then a spot within
  // motion.x and motion.y of the current one, inside the timeline's reach
  const within = (now, step, reach) => {
    const lo = Math.max(-reach, now - step);
    const hi = Math.min(reach, now + step);
    return lo + rand() * (hi - lo);
  };
  let from = [0, 0, 0];
  let to = [0, 0, 0];
  const offsetAt = (f) => {
    while (f >= to[0]) {
      from = to;
      to = [from[0] + motion.gaps[Math.floor(rand() * motion.gaps.length)], within(from[1], motion.x, REACH[0]), within(from[2], motion.y, REACH[1])];
    }
    const k = ease((f - from[0]) / (to[0] - from[0]));
    return [from[1] + (to[1] - from[1]) * k, from[2] + (to[2] - from[2]) * k];
  };

  const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const mix = (a, b, k) => `rgb(${rgb(a).map((v, i) => Math.round(v + (rgb(b)[i] - v) * k))})`;
  const colorsAt = (f) => {
    const t = f % LOOP;
    const n = colors.findLastIndex(([at]) => at <= t);
    const [at, bg, fg] = colors[n];
    const [end, nextBg, nextFg] = colors[n + 1] ?? [LOOP, colors[0][1], colors[0][2]];
    const k = (t - at) / (end - at);
    return [mix(bg, nextBg, k), mix(fg, nextFg, k)];
  };

  const draw = (f) => {
    const [bg, fg] = colorsAt(f);
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, 1920, 1080);
    const [ox, oy] = offsetAt(f);
    const base = PHASE - (ox / 100) * PERIOD;
    ctx.beginPath();
    for (const { row, col, share } of clear()) {
      // straight stripes, bent by one wave across the whole width
      const wave = -WAVE * Math.sin(((960 - (XS[col] + XS[col + 1]) / 2) / 1920 + oy / 100) * 2 * Math.PI);
      const d = row - base - wave;
      const distance = Math.abs(((d % PERIOD) + PERIOD + PERIOD / 2) % PERIOD - PERIOD / 2);
      if (distance < (share * PERIOD) / 2) ctx.rect(XS[col], YS[row], XS[col + 1] - XS[col], YS[row + 1] - YS[row]);
    }
    ctx.fillStyle = fg;
    ctx.fill();
    if (grain) {
      ctx.fillStyle = noise;
      ctx.fill();
    }
  };

  const begin = performance.now();
  let frame = -1;
  const tick = (now) => {
    const f = Math.max(0, Math.floor(((now - begin) / 1000) * 30)); // the first frame can come a few ms before begin
    if (f !== frame) {
      frame = f;
      draw(f);
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
};
