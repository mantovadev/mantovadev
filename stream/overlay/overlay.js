// Builds the overlay for <body data-layout> from LAYOUTS (layouts.js), the theme and the front matter of the event's README
// (events/<date>/README.md). Without one, it shows a generic Mantova Dev overlay.
// Boxes are [x, y, width, height] in canvas pixels; obs-scenes.py places the video sources on the same slots.
(async () => {
  // palette and logos per theme: a default and overrides per page
  const TEAL = { palette: "teal", logo: "mantova-dev-logo.svg", event: "events-logo.svg" };
  const MAGENTA = { palette: "magenta", logo: "mantova-dev-logo-purple.svg", event: "talk-logo.svg" };
  const MONO = { palette: "mono", logo: "mantova-dev-logo-black.svg", event: "talk-logo-black.svg" };
  const THEMES = {
    talk: { default: MAGENTA, inizio: TEAL, fine: TEAL },
    events: { default: TEAL },
    mono: { default: MONO },
  };
  const FILLERS = ["inizio", "pausa", "fine"];
  const SOCIALS = ["instagram", "tiktok", "linkedin", "telegram", "github", "youtube"];

  const body = document.body;
  const layout = body.dataset.layout;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);

  const event = await latestEvent();
  const theme = Object.hasOwn(THEMES, event?.theme ?? "") ? event.theme : "talk";
  const brand = THEMES[theme][layout] ?? THEMES[theme].default;
  // a partner's logos, paths relative to the event folder: logo (top right of the fillers), lockup (in place of ours),
  // talk (in place of the event logo in the footer), side (in place of the event logo in the side column)
  const partner = event?.partner ?? {};
  // Inizio and Fine of the talk theme: each logo is the teal file with the magenta one on top, animated in overlay.css (.swap)
  const SWAP = theme === "talk" && (layout === "inizio" || layout === "fine");
  const src = (file) => `assets/logos/${file}`;
  const img = (cls, slot, own) => {
    if (partner[slot]) return `<img class="${cls} partner" src="../../events/${event.date}/${esc(partner[slot])}" alt="">`;
    if (!SWAP) return `<img class="${cls}" src="${src(own)}" alt="">`;
    const to = own === TEAL.logo ? MAGENTA.logo : MAGENTA.event;
    return `<span class="${cls} swap"><img src="${src(own)}" alt=""><img class="to" src="${src(to)}" alt=""></span>`;
  };

  body.dataset.theme = theme;
  body.dataset.palette = brand.palette;

  const canvas = document.createElement("canvas");
  canvas.className = "background";
  // the grain over the background: assets/noise.png, blended in overlay.css. Right above the canvas, so everything else stays clean
  const noise = document.createElement("div");
  noise.className = "noise";
  const content = document.createElement("div");
  body.prepend(canvas, noise, content);
  window.playBackground(canvas, window.BACKGROUNDS[theme][FILLERS.includes(layout) ? layout : "live"]);

  const add = (html) => content.insertAdjacentHTML("beforeend", html);
  const box = (cls, [x, y, w, h], html = "") =>
    add(`<div class="box ${cls}" style="left:${x}px;top:${y}px;width:${w}px;height:${h}px">${html}</div>`);
  let rotation;

  if (FILLERS.includes(layout)) {
    filler();
  } else {
    // the talk on stage follows the clock: check every 30 s and redraw when it changes
    let shown = onStage();
    render(shown);
    setInterval(() => {
      if (onStage() === shown) return;
      shown = onStage();
      clearInterval(rotation);
      content.replaceChildren();
      render(shown);
    }, 30000);
  }

  function filler() {
    const date = event ? `<p class="date">${esc(event.date)}.md</p>` : "";
    const title = `<h1>${esc(body.dataset.heading)}</h1>`;
    const logo = img("logo", "lockup", brand.logo);
    // Fine: the lockup on top, the thanks in the middle; at the bottom the claim on the left and a card with the socials and the QR on the right
    if (layout === "fine") {
      const links = SOCIALS.map((name) => `<li><svg aria-label="${name}"><use href="../../site/icons.svg#${name}"/></svg></li>`).join("");
      add(`<div class="stage">${logo}${title}<div class="closing"><p class="claim">Build<br>knowledge<br>together.</p><div class="follow"><div class="follow-text"><div class="label">Follow us</div><ul class="socials">${links}</ul></div><svg class="qr" aria-label="mantova.dev"><use href="qr.svg#qr"/></svg></div></div></div>`);
      return;
    }
    if (partner.logo) {
      add(`<div class="stage"><div class="topbar">${img("event-logo", "", brand.event)}${date}${img("partner-logo", "logo")}</div>${title}<div class="logos">${logo}</div></div>`);
    } else {
      add(`<div class="stage">${date}${title}<div class="logos">${logo}${img("event-logo", "", brand.event)}</div></div>`);
    }
  }

  function render(talk) {
    const L = window.LAYOUTS[layout];
    const speakers = talk?.speakers ?? [];
    if (L.screen) box("slot", L.screen);
    if (L.camera) box("slot", L.camera);
    // Talk 4:3 has room for the name only
    const people = speakers.map((p, n) => `<div class="person" data-i="${n}">${layout === "talk-4x3" ? `<div class="name">${esc(p.name)}</div>` : person(p)}</div>`).join("");
    const card = `card${speakers.some((p) => p.bio) ? " has-bio" : ""}`;
    // without an event, the tagline goes on the subtitle line
    const lines = `${opt("title ticker", talk?.title)}${opt("subtitle ticker", talk ? talk.subtitle : "La tech community per sviluppatori e appassionati")}`;
    if (L.side) {
      // the side column: the speaker below the video, then the talk and the logos
      const top = speakers.length ? `<div class="card">${people}</div>` : "";
      box("side", L.side, `${top}<div class="text"><div class="center fit">${lines}</div><div class="logos">${img("logo", "", brand.logo)}${img("event-logo", "side", brand.event)}</div></div>`);
    } else {
      if (L.card && speakers.length) box(card, L.card, people);
      if (L.cards && speakers.length) {
        // Panel: an accordion, one item per speaker. The name is always visible, the one on stage is open and shows the rest.
        // "sizing" turns the transitions off until fitText and sizeAccordion have measured everything
        const items = speakers.map((p, n) => {
          const metas = p.role || p.company ? `<div class="metas">${opt("meta role", p.role)}${opt("meta company", p.company)}</div>` : "";
          return `<div class="speaker" data-i="${n}"><div class="name">${esc(p.name)}</div><div class="details">${metas}${opt("bio fit", p.bio)}</div></div>`;
        }).join("");
        box(`cards sizing${speakers.some((p) => p.bio) ? " has-bio" : ""}`, L.cards, items);
      }
      add(`<div class="footer">${img("logo", "lockup", brand.logo)}<div class="center">${lines}</div>${img("event-logo", "talk", brand.event)}</div>`);
    }
    document.fonts.ready.then(() => {
      sizeAccordion();
      fitText();
      const cards = document.querySelector(".cards");
      if (cards) {
        void cards.offsetHeight; // apply the measured state with no transition, then let them run
        cards.classList.remove("sizing");
      }
    });
    // with more speakers, the cards take turns every 15 s: .on marks everything for speaker i
    let i = 0;
    const turn = () => document.querySelectorAll("[data-i]").forEach((el) => el.classList.toggle("on", el.dataset.i === String(i)));
    turn();
    if (speakers.length > 1) rotation = setInterval(() => {
      i = (i + 1) % speakers.length;
      turn();
    }, 15000);
  }

  // one speaker's card content. The .person cards are laid out stacked, so fitText can measure the hidden ones too
  function person(p) {
    return `<div class="who"><div class="name">${esc(p.name)}</div>${opt("meta role", p.role)}${opt("meta company", p.company)}</div>${opt("bio fit", p.bio)}`;
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
      if (valid) return { ...data, date, today: date === today };
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

  // Panel accordion: each item gets its closed height (the name) and its open height in px, so the height can transition.
  // The open one takes the rest of the column; with no bios at all it only takes what its role and company need.
  function sizeAccordion() {
    const cards = document.querySelector(".cards");
    if (!cards) return;
    const items = [...cards.children];
    const closed = items.map((el) => el.querySelector(".name").offsetHeight + el.offsetHeight - el.clientHeight); // plus the borders
    const rest = closed.reduce((a, b) => a + b, 0);
    const fill = cards.classList.contains("has-bio");
    items.forEach((el, n) => {
      const metas = el.querySelector(".metas");
      const natural = closed[n] + (metas ? metas.offsetHeight - 8 + 20 : 0); // the metas sit 8px under the name's padding, 20px above the bottom
      el.style.setProperty("--closed", `${closed[n]}px`);
      el.style.setProperty("--open", `${fill ? cards.clientHeight - (rest - closed[n]) : natural}px`);
    });
  }

  function fitText() {
    // shrink text that overflows its box
    for (const el of document.querySelectorAll(".fit")) {
      // in the accordion only the open item has room for its bio: measure each one as if its speaker were on stage
      const item = el.closest(".speaker");
      const reopen = item && !item.classList.contains("on");
      if (reopen) item.classList.add("on");
      let size = parseFloat(getComputedStyle(el).fontSize);
      while (el.scrollHeight > el.clientHeight && size > 16) el.style.fontSize = `${--size}px`;
      // still too long at the smallest size: keep the lines that fit whole and end the last one with "...";
      // with room for less than 2 lines, hide it rather than show a fragment
      if (el.scrollHeight > el.clientHeight) {
        el.style.display = "-webkit-box";
        el.style.webkitBoxOrient = "vertical";
        const lineHeight = parseFloat(getComputedStyle(el).lineHeight);
        const lines = Math.floor(el.clientHeight / lineHeight);
        if (lines < 2) {
          el.style.display = "none";
        } else {
          el.style.webkitLineClamp = lines;
          el.style.flex = "none";
          el.style.height = `${lines * lineHeight}px`; // or the partial line below the cut still shows
        }
      }
      if (reopen) item.classList.remove("on");
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
})();
