// Animated block background on a 1920x1080 canvas (scale it with CSS if needed): horizontal stripes that drift up and down
// on a grid of cells, cut by a density map. It never repeats. No dependencies, so the website can use it too.
//   map: rows of digits, the share of rows lit in tenths, spread smoothly over the grid
//   colors: [frame, background, blocks] keyframes over a 300-frame loop at 30 fps; one entry for a fixed palette
//   grain: strength of the specks on the blocks, 0 for none
window.playBackground = (canvas, { map, colors, grain }) => {
  const COLS = 56;
  const ROWS = 24;
  const PERIOD = 4; // rows from one stripe to the next
  const LOOP = 300;
  const XS = [0, ...Array.from({ length: 55 }, (_, k) => 15 + 35 * k), 1920];
  const YS = [0, ...Array.from({ length: 23 }, (_, k) => Math.floor(40 + 45.5 * k)), 1080];
  const rand = Math.random;

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
  const cells = [];
  for (let row = 0; row < ROWS; row++) {
    for (let col = 0; col < COLS; col++) {
      const fx = ((col + 0.5) / COLS) * map[0].length - 0.5;
      const fy = ((row + 0.5) / ROWS) * map.length - 0.5;
      const x = Math.floor(fx);
      const y = Math.floor(fy);
      const a = fx - x;
      const b = fy - y;
      const share = (digit(x, y) * (1 - a) + digit(x + 1, y) * a) * (1 - b) + (digit(x, y + 1) * (1 - a) + digit(x + 1, y + 1) * a) * b;
      if (share > 0) cells.push({ row, col, share });
    }
  }

  // the stripes' position: long holds with a slow creep, then a quick move
  let from = 0;
  let to = 0;
  let start = 0;
  let moved = 0;
  let held = 0;
  const center = (f) => {
    while (f >= held) {
      from = to;
      to = from + (rand() < 0.5 ? -1 : 1) * (0.6 + rand() * 2.6);
      start = held;
      moved = start + 8 + rand() * 22;
      held = moved + 20 + rand() * 60;
    }
    const k = Math.min(1, (f - start) / (moved - start));
    return from + (to - from) * k * k * (3 - 2 * k) + 0.15 * Math.sin(f / 37);
  };
  // each side bends on its own, and each row is slightly off, so the bars don't line up
  const phases = [rand() * 6.28, rand() * 6.28];
  const offsets = Array.from({ length: ROWS }, () => (rand() - 0.5) * 0.125);

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
    const base = center(f);
    ctx.beginPath();
    for (const { row, col, share } of cells) {
      const left = col < COLS / 2;
      const bend = 0.35 * Math.sin(f / (left ? 61 : 53) + phases[left ? 0 : 1] + (left ? col : -col) * 0.09);
      const d = row + offsets[row] - base - 0.09 * row - bend;
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
    const f = Math.floor(((now - begin) / 1000) * 30);
    if (f !== frame) {
      frame = f;
      draw(f);
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
};
