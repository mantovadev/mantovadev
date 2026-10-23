// Builds the overlay for <body data-layout> from LAYOUTS (layouts.js) and the front matter of the event's README
// (events/<date>/README.md). Without one, it shows a generic Mantova Dev overlay.
// Boxes are [x, y, width, height] in canvas pixels; obs-scenes.py places the video sources on the same slots.
(async () => {
  const body = document.body;
  const layout = body.dataset.layout;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);
  const logo = `<img class="logo" src="../../assets/svg/logo-orizzontale-chiaro.svg" alt="Mantova Dev">`;
  const qr = `<img class="qr" src="qr.svg" alt="">`;
  const link = `<div class="link"><div class="label">Prossimi eventi</div><b>mantova.dev</b></div>`;
  const add = (html) => body.insertAdjacentHTML("beforeend", html);
  const box = (cls, [x, y, w, h], html = "") =>
    add(`<div class="box ${cls}" style="left:${x}px;top:${y}px;width:${w}px;height:${h}px">${html}</div>`);

  const event = await latestEvent();
  const names = (talk) => (talk.speakers ?? []).map((p) => p.name).join(", ");
  let rotation;

  if (layout === "full") {
    pixels();
    const talks = (event?.talks ?? []).map((t) =>
      `<li><b class="ticker">${esc(t.title)}</b>${opt("sub ticker", t.subtitle)}<div class="label">${esc(names(t))}</div></li>`).join("");
    add(`<div class="top">${logo}</div><div class="box full"><h1>${esc(body.dataset.heading)}</h1>${talks && `<div class="bar"></div><ul class="talks">${talks}</ul>`}</div>`);
    add(`<div class="cta">${qr}${link}</div>`);
    document.fonts.ready.then(fitText);
  } else {
    // the talk on stage follows the clock: check every 30 s and redraw when it changes
    let shown = onStage();
    render(shown);
    setInterval(() => {
      if (onStage() === shown) return;
      shown = onStage();
      clearInterval(rotation);
      body.replaceChildren();
      render(shown);
    }, 30000);
  }

  function render(talk) {
    const L = window.LAYOUTS[layout];
    if (L.screen) box("slot", L.screen);
    if (L.camera) box("slot", L.camera);
    if (L.aside) {
      // Talk 4:3 has no footer: its short card carries the talk, with the speaker on the label line
      if (talk) box("card", L.card, `<div class="meta"><span class="label">Talk</span><span class="speaker ticker">${esc(names(talk))}</span></div><div class="title ticker">${esc(talk.title)}</div>${opt("sub ticker", talk.subtitle)}`);
      box("aside", L.aside, `<div>${logo}${link}</div>${qr}`);
    } else {
      // the speaker card under the camera; without a card, the speakers go on the footer label
      if (L.card && talk?.speakers?.length) box("card", L.card, talk.speakers.map(person).join(""));
      const label = talk ? [event.number && `#${esc(event.number)}`, !L.card && esc(names(talk))].filter(Boolean).join(" · ") : "Mantova Dev";
      const sub = talk?.subtitle ? `<span class="sub">${esc(talk.subtitle)}</span>` : "";
      const line = talk ? `<b>${esc(talk.title)}</b>${sub}` : "<b>La tech community per sviluppatori e appassionati</b>";
      box("band", L.band, `${logo}<div class="event"><div class="label">${label}</div><div class="line ticker">${line}</div></div>${link}${qr}`);
    }
    document.fonts.ready.then(fitText);
    // with more speakers, the card shows one at a time and fades to the next every 15 s
    const people = [...document.querySelectorAll(".card .person")];
    people[0]?.classList.add("on");
    let i = 0;
    if (people.length > 1) rotation = setInterval(() => {
      people[i].classList.remove("on");
      i = (i + 1) % people.length;
      people[i].classList.add("on");
    }, 15000);
  }

  // one speaker's card: all of them are laid out stacked, so fitText can measure the hidden ones too
  function person(p) {
    return `<div class="person"><div class="name">${esc(p.name)}</div>${opt("role", p.role)}${opt("company", p.company)}<div class="bio fit">${esc(p.bio ?? "")}</div></div>`;
  }

  // today's README from disk; on other days, a local page can't list folders, so it asks GitHub for the events/<date>/
  // folders on main and reads the next event's README (else the newest) from disk. It needs js-yaml, loaded by each page.
  // Offline, or with front matter of the wrong shape: no event.
  async function latestEvent() {
    const today = new Date().toLocaleDateString("sv"); // YYYY-MM-DD in local time
    try {
      let date = today;
      let readme = await fetch(`../../events/${today}/README.md`).catch(() => undefined);
      if (!readme?.ok) {
        const res = await fetch("https://api.github.com/repos/mantovadev/mantovadev/contents/events");
        if (!res.ok) return;
        const dates = (await res.json()).map((f) => f.name).filter((n) => /^\d{4}-\d{2}-\d{2}$/.test(n)).sort();
        date = dates.find((d) => d >= today) ?? dates.at(-1);
        readme = await fetch(`../../events/${date}/README.md`);
      }
      const front = (await readme.text()).match(/^---\r?\n([\s\S]*?)\r?\n---/);
      const data = front && window.jsyaml.load(front[1]);
      const valid = data?.talks?.length && data.talks.every((t) => t?.title && Array.isArray(t.speakers ?? []) && (t.speakers ?? []).every((p) => p?.name));
      if (valid) return { ...data, today: date === today };
    } catch {
      // no network, or no README on disk yet: the generic overlay
    }
  }

  // on the event day, the last talk whose time has come; before that, or with no times, the first
  function onStage() {
    if (!event) return undefined;
    const now = new Date().toTimeString().slice(0, 5);
    const started = event.today ? event.talks.filter((t) => t.time && String(t.time).padStart(5, "0") <= now) : [];
    return started.at(-1) ?? event.talks[0];
  }

  // an optional field: nothing when empty
  function opt(cls, value) {
    return value ? `<div class="${cls}">${esc(value)}</div>` : "";
  }

  function fitText() {
    // name and role prefer one line: shrink a little to get there, else keep the full size on two lines
    for (const [selector, min] of [[".card .name", 28], [".card .role", 20]]) {
      for (const el of document.querySelectorAll(selector)) {
        const lines = () => Math.round(el.scrollHeight / parseFloat(getComputedStyle(el).lineHeight));
        const full = parseFloat(getComputedStyle(el).fontSize);
        let size = full;
        while (lines() > 1 && size > min) el.style.fontSize = `${--size}px`;
        if (lines() > 1) el.style.fontSize = `${full}px`;
      }
    }
    // shrink text that overflows its box
    for (const el of document.querySelectorAll(".fit")) {
      let size = parseFloat(getComputedStyle(el).fontSize);
      while (el.scrollHeight > el.clientHeight && size > 18) el.style.fontSize = `${--size}px`;
      // still too long at the smallest size: keep the lines that fit whole and end the last one with "...";
      // with room for less than 2 lines, hide it rather than show a fragment
      if (el.scrollHeight > el.clientHeight) {
        el.style.display = "-webkit-box";
        el.style.webkitBoxOrient = "vertical";
        const lineHeight = parseFloat(getComputedStyle(el).lineHeight);
        const lines = Math.floor(el.clientHeight / lineHeight);
        if (lines < 2) {
          el.style.display = "none";
          continue;
        }
        el.style.webkitLineClamp = lines;
        el.style.flex = "none";
        el.style.height = `${lines * lineHeight}px`; // or the partial line below the cut still shows
      }
    }
    // a line too long for its box scrolls like a TV ticker: two copies side by side, shifted by one copy's width
    for (const el of document.querySelectorAll(".ticker")) {
      if (el.scrollWidth <= el.clientWidth) continue;
      el.innerHTML = `<span class="track"><span>${el.innerHTML}</span><span>${el.innerHTML}</span></span>`;
      const shift = el.querySelector(".track span").offsetWidth;
      el.style.setProperty("--shift", `-${shift}px`);
      el.style.setProperty("--dur", `${(shift / 70 / 0.8).toFixed(1)}s`); // 70 px/s, plus a 20% pause at the start
    }
  }

  // two 8x45 columns of squares, denser at the edge; a fixed seed keeps the pattern stable across reloads.
  // --o staggers the teal/magenta fade so it travels down the columns.
  function pixels() {
    let seed = 5;
    const rand = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
    for (const side of ["left", "right"]) {
      let squares = "";
      for (let y = 0; y < 45; y++) {
        for (let x = 0; x < 8; x++) {
          if (rand() > 0.5 * (1 - x / 8) ** 1.6) continue;
          const o = -(16 - (y / 45) * 16 + x * 0.4).toFixed(1);
          squares += `<i style="--x:${x};--y:${y};--o:${o}s;--t:${(6 + rand() * 8).toFixed(1)}s;--d:-${(rand() * 14).toFixed(1)}s"></i>`;
        }
      }
      add(`<div class="px ${side}">${squares}</div>`);
    }
  }
})();
