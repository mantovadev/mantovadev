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
  const SOCIALS = ["tiktok", "instagram", "facebook", "youtube", "linkedin", "telegram", "github"];

  const body = document.body;
  const layout = body.dataset.layout;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);

  const event = await latestEvent();
  const theme = Object.hasOwn(THEMES, event?.theme ?? "") ? event.theme : "talk";
  const brand = THEMES[theme][layout] ?? THEMES[theme].default;
  // a partner's logos, paths relative to the event folder: logo (top right of the fillers), lockup (in place of ours),
  // talk (in place of the event logo in the footer), side (in place of the event logo in the side column)
  const partner = event?.partner ?? {};
  const img = (cls, slot, own) => partner[slot]
    ? `<img class="${cls} partner" src="../../events/${event.date}/${esc(partner[slot])}" alt="">`
    : `<img class="${cls}" src="assets/logos/${own}" alt="">`;
  body.dataset.theme = theme;
  body.dataset.palette = brand.palette;

  const canvas = document.createElement("canvas");
  canvas.className = "background";
  const content = document.createElement("div");
  body.prepend(canvas, content);
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
    // Fine also points to the next events and the socials
    const fine = layout === "fine";
    const heading = fine ? `<div class="hero">${title}<div class="cta"><img class="qr" src="qr.svg" alt=""><div><div class="label">Prossimi eventi</div><b>mantova.dev</b></div></div></div>` : title;
    const icons = fine ? `<ul class="socials">${SOCIALS.map((name) => `<li><svg aria-label="${name}"><use href="../../site/icons.svg#${name}"/></svg></li>`).join("")}</ul>` : "";
    const logo = img("logo", "lockup", brand.logo);
    if (partner.logo) {
      add(`<div class="stage"><div class="topbar">${img("event-logo", "", brand.event)}${date}${img("partner-logo", "logo")}</div>${heading}<div class="logos">${logo}</div>${icons}</div>`);
    } else {
      add(`<div class="stage">${date}${heading}<div class="logos">${logo}${img("event-logo", "", brand.event)}</div>${icons}</div>`);
    }
  }

  function render(talk) {
    const L = window.LAYOUTS[layout];
    if (L.screen) box("slot", L.screen);
    if (L.camera) box("slot", L.camera);
    const speakers = talk?.speakers ?? [];
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
        // Panel: with several speakers, the list of names over the current one's card
        const list = speakers.length > 1 ? speakers.map((p, n) => `<div class="speaker" data-i="${n}">${esc(p.name)}</div>`).join("") : "";
        box("cards", L.cards, `${list}<div class="${card}">${people}</div>`);
      }
      add(`<div class="footer">${img("logo", "lockup", brand.logo)}<div class="center">${lines}</div>${img("event-logo", "talk", brand.event)}</div>`);
    }
    document.fonts.ready.then(fitText);
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
    return `<div class="who"><div class="name">${esc(p.name)}</div>${opt("meta", p.role)}${opt("meta", p.company)}</div>${opt("bio fit", p.bio)}`;
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

  function fitText() {
    // shrink text that overflows its box
    for (const el of document.querySelectorAll(".fit")) {
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
})();
