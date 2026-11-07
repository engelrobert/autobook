// Step 4: the laid-out book as spreads. Pages are laid out by themselves whenever the selection
// changed; click a photo on a page to swap, enlarge or remove it, and the pages follow.
// The page design bar sets the frame and colour of the whole book, the palette on a page its own colour.
// "Choose the cut" moves a trimmed photo inside its space, so you decide which part is cut away.

import { G, api, bookApi, bookUrl, loadBook, poll, profileOf, runJob, thumbUrl } from "../api.js";
import { countSlider } from "../count.js";
import { el, icon, progressBar, show, toast } from "../dom.js";
import { t } from "../i18n.js";
import { onLeave } from "../main.js";
import * as P from "../plan.js";

// Lay the pages out (again) with a progress screen. Returns false when the user has to decide
// something first (a conflict with changes made in the Fotobuch program) or it failed.
export async function layout(B, { fresh = false, quiet = null } = {}) {
  const bar = el("div", { class: "grow" }, progressBar(0.05));
  const note = el("p", { class: "muted" }, t("book.layingLead"));
  if (quiet) {
    quiet.replaceChildren(el("div", { class: "relayout" }, el("span", { class: "spinner" }), t("book.relaying")));
  } else {
    show(el("div", { class: "page narrow center" },
      el("div", { class: "book-anim" }, icon("book", 64, { color: "var(--accent)", width: 1.2 })),
      el("h1", {}, t("book.laying")), note, el("div", { class: "bar-wrap" }, bar)));
  }
  const onProgress = j => {
    if (j.stage === "fotobuch" && j.total) bar.replaceChildren(progressBar(0.2 + 0.8 * j.done / j.total));
    else if (j.stage) bar.replaceChildren(progressBar(0.1));
  };
  let r;
  try {
    r = B.job && B.job.kind === "export" ? await poll(B.job.id, onProgress)
      : await runJob(bookApi(B.name, "export"), { fresh }, onProgress);
  } catch (e) {
    show(el("div", { class: "page narrow" }, el("h1", {}, t("book.failed")),
      el("details", { class: "card" }, el("summary", {}, t("print.details")), el("pre", {}, e.message)),
      el("a", { class: "btn", href: bookUrl(B.name, "selection") }, t("book.backToSelection"))));
    return false;
  }
  B.job = null;
  if (!r.ok && r.conflict) {
    show(el("div", { class: "page narrow" }, el("h1", {}, t("book.conflict")),
      el("p", { class: "lead" }, t("book.conflictLead")),
      el("div", { class: "row" },
        el("button", { class: "btn primary", onclick: async () => { if (await layout(B, { fresh: true })) bookStep(B); } }, t("book.conflictFresh")),
        el("a", { class: "btn ghost", href: bookUrl(B.name, "selection") }, t("book.backToSelection"))),
      el("details", { class: "card" }, el("summary", {}, t("print.details")), el("pre", {}, r.error))));
    return false;
  }
  B.exported = true;
  B.layout_outdated = false;
  B.print_outdated = true;
  B.release = null;
  if (r.filled) {  // the book was too short: photos were added instead of blank pages
    const st = await loadBook(B.name);
    Object.assign(B, { plan: st.plan, capacity: st.capacity, least: st.least, fit_points: st.fit_points,
      pages_estimate: st.pages_estimate });
    toast(t("count.filled", { n: r.filled }));
  }
  return true;
}

let keyHandler = null;
let shownPages = [];  // the pages of the current layout (from page_index), for moving photos between them
const pageOfBox = new WeakMap();
const WIDE = 1.6;  // photos at least this wide can run across both pages

export async function bookStep(B) {
  if (!B.plan) {
    location.hash = bookUrl(B.name, "wishes");
    return;
  }
  P.attach(B, () => { refreshDesign(B); scheduleRelayout(B); });
  await P.flush();
  const fresh = sessionStorage.getItem(`fresh:${B.name}`);
  sessionStorage.removeItem(`fresh:${B.name}`);
  if (fresh || !B.exported || B.layout_outdated || (B.job && B.job.kind === "export")) {
    if (!(await layout(B, { fresh: !!fresh }))) return;
  }
  show(el("div", { class: "page narrow center" }, el("div", { class: "loading" }, el("span", { class: "spinner" }), t("book.preparing"))));
  const { pages } = await api("GET", bookApi(B.name, "pages"));
  render(B, pages);
}

let relayoutTimer = null;
function scheduleRelayout(B) {
  clearTimeout(relayoutTimer);
  const holder = document.getElementById("relayout-holder");
  if (holder) holder.replaceChildren(el("div", { class: "relayout" }, el("span", { class: "spinner" }), t("book.relaying")));
  relayoutTimer = setTimeout(async () => {
    await P.flush();
    if (!location.hash.endsWith("/book")) return;
    if (await layout(B, { quiet: document.getElementById("relayout-holder") })) {
      const { pages } = await api("GET", bookApi(B.name, "pages"));
      render(B, pages);
    }
  }, 900);
}

function spreadsOf(pages) {
  const cover = pages.find(p => p.kind === "cover");
  const inner = pages.filter(p => p.kind !== "cover");
  const out = [];
  if (cover) out.push({ cover });
  if (inner.length) out.push({ left: null, right: inner[0] });  // page 1 is a right-hand page
  for (let i = 1; i < inner.length; i += 2) out.push({ left: inner[i], right: inner[i + 1] || null });
  return { spreads: out, inner };
}

function render(B, pages) {
  shownPages = pages;
  const { spreads, inner } = spreadsOf(pages);
  const key = `spread:${B.name}`;
  let idx = Math.min(Number(sessionStorage.getItem(key) || 0), Math.max(0, spreads.length - 1));
  const prof = profileOf(B.plan.book.profile);
  const pageAr = (prof.width + 6) / (prof.height + 6);
  const blanks = pages.filter(p => p.kind === "blank").length;
  const stage = el("div", { class: "spread-stage", style: `--spread-ar:${(2 * pageAr).toFixed(3)}` });
  const caption = el("p", { class: "muted center" });
  const strip = el("div", { class: "spread-strip" });

  function label(s) {
    if (s.cover) return t("book.coverLabel");
    const ns = [s.left, s.right].filter(Boolean).map(p => p.n);
    return ns.length === 2 ? t("book.pagesLabel", { a: ns[0], b: ns[1], n: inner.length }) : t("book.pageLabel", { a: ns[0], n: inner.length });
  }

  function pageEl(pg, cover = false) {
    const box = el("div", { class: "pg" + (cover ? " cover" : ""), style: cover ? "" : `aspect-ratio:${pageAr}` },
      el("img", { src: pg.image, alt: label({ left: pg }),
        onload: e => { if (cover) stage.style.setProperty("--cover-ar", (e.target.naturalWidth / e.target.naturalHeight).toFixed(3)); } }));
    pageOfBox.set(box, pg);
    const rects = [...(pg.rects || [])];
    const opener = pg.kind === "opener" ? P.chapterById(B.plan, pg.chapter)?.divider : null;
    if (opener?.style === "hero" && opener.photo) {  // a full-page chapter photo: its cut can be chosen too
      rects.push({ id: opener.photo, x: 0, y: 0, w: 1, h: 1, ar: pageAr, opener: true });
    }
    if (pg.kind === "spread" && pg.photos.length) {  // one half of a panorama: the cut covers both pages
      rects.push({ id: pg.photos[0], x: 0, y: 0, w: 1, h: 1, ar: 2 * pageAr, opener: true, spread: true });
    }
    for (const r of rects) {
      const hot = el("button", { class: "hot", "aria-label": t("book.photoMenu"),
        style: `left:${r.x * 100}%;top:${r.y * 100}%;width:${r.w * 100}%;height:${r.h * 100}%`,
        onclick: e => { e.stopPropagation(); if (!hot.dataset.dragged) photoMenu(B, pg, r, box, hot); delete hot.dataset.dragged; } });
      if (pg.kind === "photos") draggable(B, pg, r, hot);
      box.append(hot);
    }
    const key = colourKey(B, pg);
    if (key) {
      const btn = el("button", { class: "pg-color", "aria-label": t("design.pageColour"), title: t("design.pageColour"),
        onclick: e => { e.stopPropagation(); colourMenu(B, key, box, btn); } }, icon("palette", 22));
      box.append(btn);
    }
    return box;
  }

  function go(i) {
    idx = Math.max(0, Math.min(spreads.length - 1, i));
    sessionStorage.setItem(key, String(idx));
    const s = spreads[idx];
    stage.replaceChildren(s.cover ? el("div", { class: "spread cover" }, pageEl(s.cover, true))
      : el("div", { class: "spread" },
        s.left ? pageEl(s.left) : el("div", { class: "pg inside", style: `aspect-ratio:${pageAr}` }),
        s.right ? pageEl(s.right) : el("div", { class: "pg inside", style: `aspect-ratio:${pageAr}` })));
    caption.textContent = label(s);
    strip.querySelectorAll(".mini").forEach((m, j) => m.classList.toggle("on", j === idx));
    strip.children[idx]?.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
  }

  strip.replaceChildren(...spreads.map((s, i) => el("button", { class: "mini", "aria-label": label(s), onclick: () => go(i) },
    el("span", { class: "mini-pages" + (s.cover ? " cover" : "") },
      ...(s.cover ? [el("img", { src: s.cover.image, alt: "", loading: "lazy" })]
        : [s.left ? el("img", { src: s.left.image, alt: "", loading: "lazy" }) : el("span", { class: "mini-blank" }),
          s.right ? el("img", { src: s.right.image, alt: "", loading: "lazy" }) : el("span", { class: "mini-blank" })])),
    el("span", { class: "mini-label" }, s.cover ? t("book.coverShort") : [s.left, s.right].filter(Boolean).map(p => p.n).join("–")))));

  show(el("div", { class: "page wide" },
    el("div", { class: "sel-head" },
      el("div", { class: "col" }, el("h1", {}, t("book.title")), el("span", { class: "muted" }, t("book.lead", { n: inner.length }))),
      el("div", { class: "row" }, el("span", { id: "relayout-holder" },
        el("span", { class: "save-status saved" }, icon("check", 18, { width: 2.4 }), t("book.upToDate"))),
      el("a", { class: "btn primary big", href: bookUrl(B.name, "print") }, t("book.next"), icon("next", 22)))),
    blanks ? el("div", { class: "card note row" }, icon("warn", 22),
      el("span", { class: "grow" }, t("book.blanks", { n: blanks })),
      el("a", { class: "btn ghost", href: bookUrl(B.name, "selection") }, t("book.addPhotos"))) : null,
    el("div", { class: "viewer" },
      el("button", { class: "nav-round", "aria-label": t("book.prevSpread"), onclick: () => go(idx - 1) }, icon("left", 28)),
      stage,
      el("button", { class: "nav-round", "aria-label": t("book.nextSpread"), onclick: () => go(idx + 1) }, icon("right", 28))),
    caption, el("div", { id: "design-holder" }, designBar(B)),
    el("div", { class: "count-row" }, countSlider(B), autoFit(B)), strip));
  go(idx);

  if (keyHandler) document.removeEventListener("keydown", keyHandler);
  keyHandler = e => {
    if (!location.hash.endsWith("/book") || !document.getElementById("overlay").hidden) return;
    if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName) || document.querySelector(".crop-edit")) return;
    if (e.key === "ArrowLeft") go(idx - 1);
    if (e.key === "ArrowRight") go(idx + 1);
  };
  document.addEventListener("keydown", keyHandler);
  onLeave(() => { document.removeEventListener("keydown", keyHandler); keyHandler = null; });
}

function photoMenu(B, pg, r, box, hot) {
  document.querySelectorAll(".pg-pop").forEach(p => p.remove());
  const id = r.id;
  const p = P.photoOf(B.plan, id);
  const isCover = pg.kind === "cover";
  const layout = p?.layout || "normal";
  const pop = el("div", { class: "pg-pop", role: "dialog", "aria-label": t("book.photoMenu") });
  const sibs = el("div", { class: "pop-sibs" });
  const close = () => { pop.remove(); hot.classList.remove("on"); document.removeEventListener("click", outside); };
  const outside = e => { if (!pop.contains(e.target)) close(); };
  hot.classList.add("on");
  pop.append(...[
    el("button", { class: "pop-x", "aria-label": t("lb.close"), onclick: close }, icon("close", 18)),
    el("img", { class: "pop-thumb", src: thumbUrl(B.name, id), alt: "" }),
    sibs,
    trimmed(B, r) ? el("button", { class: "pop-item", onclick: () => { close(); cropEditor(B, r, r.spread ? box.closest(".spread") : box); } },
      icon("crop", 20), t("crop.choose")) : null,
    isCover ? el("a", { class: "pop-item", href: bookUrl(B.name, "selection") }, icon("cover", 20), t("book.otherCover")) : null,
    ...pageItems(B, pg, p, close),
    p && layout === "normal" ? el("button", { class: "pop-item", onclick: () => { close(); P.change(p.weight > 1 ? t("done.smaller") : t("done.bigger"), pl => P.toggleBig(pl, id)); } },
      icon(p.weight > 1 ? "smaller" : "bigger", 20), p.weight > 1 ? t("act.smaller") : t("act.bigger")) : null,
    p ? el("button", { class: "pop-item danger", onclick: () => { close(); P.change(t("done.removed"), pl => P.removePhoto(pl, id)); } },
      icon("close", 20), t("act.remove")) : null,
    el("button", { class: "pop-item", onclick: () => { sessionStorage.setItem("focus", id); location.hash = bookUrl(B.name, "selection"); } },
      icon("right", 20), t("book.showInSelection")),
  ].filter(Boolean));  // DOM append would print "null"
  box.append(pop);
  // centred over the photo, kept inside the page
  const W = box.clientWidth, H = box.clientHeight, pw = pop.offsetWidth;
  pop.style.left = `${Math.max(8, Math.min(W - pw - 8, (r.x + r.w / 2) * W - pw / 2))}px`;
  pop.style.top = `${Math.max(8, Math.min(Math.max(8, H - pop.offsetHeight - 8), r.y * H + 16))}px`;
  setTimeout(() => document.addEventListener("click", outside), 0);
  api("GET", bookApi(B.name, `siblings/${id}`)).then(({ siblings }) => {
    if (!siblings.length || !pop.isConnected) return;
    sibs.replaceChildren(el("span", { class: "muted small" }, t("lb.siblings")),
      el("div", { class: "row tight" }, siblings.slice(0, 4).map(s => el("button", { class: "pop-sib", "aria-label": t("lb.takeThis"),
        onclick: () => { close(); P.change(t("done.swapped"), pl => P.swap(pl, B, id, s.id)); } },
      el("img", { src: thumbUrl(B.name, s.id), alt: "" })))));
  }).catch(() => {});
}

// A book too short for the printer gets more photos instead of blank pages
function autoFit(B) {
  const box = el("input", { type: "checkbox", checked: B.plan.book.auto_fit !== false });
  box.addEventListener("change", () => P.change(t(box.checked ? "count.autoOn" : "count.autoOff"),
    pl => { pl.book.auto_fit = box.checked; }));
  return el("label", { class: "check-row auto-fit" }, box,
    el("span", { class: "col" }, el("strong", {}, t("count.auto")), el("span", { class: "muted small" }, t("count.autoWhy"))));
}

// ── page design ──────────────────────────────────────────────────────────
function swatchRow(current, onPick) {
  return el("div", { class: "swatches", role: "radiogroup" }, G.swatches.map(s => {
    const on = s.key === current;
    return el("button", { class: "swatch" + (on ? " on" : ""), role: "radio", "aria-checked": String(on),
      "aria-label": t(`colour.${s.key}`), title: t(`colour.${s.key}`), onclick: () => { if (!on) onPick(s.key); } },
    el("span", { class: "dot", style: `background:${s.bg}` }, on ? icon("check", 16, { color: s.ink, width: 3 }) : null));
  }));
}

function designBar(B) {
  const frame = B.plan.book.frame || "narrow", bg = B.plan.book.background || "white";
  return el("div", { class: "design-bar" },
    el("div", { class: "design-group" }, el("span", { class: "design-label" }, t("design.frame")),
      el("div", { class: "seg", role: "radiogroup", "aria-label": t("design.frame") }, G.frames.map(f => {
        const on = f === frame;
        return el("button", { class: "seg-btn" + (on ? " on" : ""), role: "radio", "aria-checked": String(on),
          onclick: () => { if (!on) P.change(t("design.frameDone"), pl => P.setFrame(pl, f)); } },
        el("span", { class: `frame-ico ${f}` }, el("span")), t(`frame.${f}`));
      }))),
    el("div", { class: "design-group" }, el("span", { class: "design-label" }, t("design.background")),
      swatchRow(bg, key => P.change(t("design.backgroundDone"), pl => P.setBackground(pl, key)))));
}

function refreshDesign(B) {
  document.getElementById("design-holder")?.replaceChildren(designBar(B));
}

// A page's own colour is stored under its first photo, a plain chapter page's under its chapter
function colourKey(B, pg) {
  if (pg.kind === "photos" && pg.photos.length) return pg.photos[0];
  if (pg.kind === "opener" && P.chapterById(B.plan, pg.chapter)?.divider.style === "plain") return `chapter:${pg.chapter}`;
  return null;
}

function colourMenu(B, key, box, btn) {
  document.querySelectorAll(".pg-pop").forEach(p => p.remove());
  const own = (B.plan.book.page_colors || {})[key] || null;
  const pop = el("div", { class: "pg-pop", role: "dialog", "aria-label": t("design.pageColour") });
  const close = () => { pop.remove(); btn.classList.remove("on"); document.removeEventListener("click", outside); };
  const outside = e => { if (!pop.contains(e.target)) close(); };
  btn.classList.add("on");
  pop.append(
    el("button", { class: "pop-x", "aria-label": t("lb.close"), onclick: close }, icon("close", 18)),
    el("strong", { class: "pop-title" }, t("design.pageColour")),
    swatchRow(own, c => { close(); P.change(t("design.pageColourDone"), pl => P.setPageColor(pl, key, c)); }),
    el("button", { class: "pop-item" + (own ? "" : " on"), onclick: () => { close(); if (own) P.change(t("design.likeBookDone"), pl => P.setPageColor(pl, key, null)); } },
      own ? el("span", { class: "dot-sm", style: `background:${G.swatches.find(s => s.key === (B.plan.book.background || "white")).bg}` }) : icon("check", 20),
      t("design.likeBook")));
  box.append(pop);
  const W = box.clientWidth, pw = pop.offsetWidth;  // under the palette button, kept inside the page
  pop.style.left = `${Math.max(8, Math.min(W - pw - 8, btn.offsetLeft + btn.offsetWidth - pw))}px`;
  pop.style.top = `${btn.offsetTop + btn.offsetHeight + 8}px`;
  setTimeout(() => document.addEventListener("click", outside), 0);
}

// ── choosing the cut ─────────────────────────────────────────────────────
const photoShape = (B, id) => { const m = B.photos[id] || {}; return (m.w || 3) / (m.h || 2); };
const trimmed = (B, r) => !!r.ar && Math.abs(Math.log(photoShape(B, r.id) / r.ar)) > 0.01;

// The whole photo dimmed, its space on the page as a bright window; drag the photo (or use the
// arrow keys) until the window shows the part you want. The position is stored as 0..1 per axis.
function cropEditor(B, r, box) {
  document.querySelectorAll(".pg-pop, .crop-edit, .crop-bar").forEach(e => e.remove());
  const W = box.clientWidth, H = box.clientHeight;
  const sw = r.w * W, sh = sw / r.ar;  // the slot's own shape (a rect may be clipped at the page edge)
  const pa = photoShape(B, r.id);
  const [iw, ih] = pa > r.ar ? [sh * pa, sh] : [sw, sw / pa];
  // where autobook cuts by itself: centred on the faces when the AI found them, otherwise the middle
  const centreOn = (at, full, part) => (full > part ? Math.max(0, Math.min(1, (at * full - part / 2) / (full - part))) : 0.5);
  const face = (B.focus || {})[r.id];
  const auto = face ? [centreOn(face[0], iw, sw), centreOn(face[1], ih, sh)] : [0.5, r.opener ? 0.4 : 0.5];
  const start = (B.plan.book.crops || {})[r.id] || auto;
  let [fx, fy] = start;
  const src = thumbUrl(B.name, r.id, 1024);
  const ghost = el("img", { class: "crop-ghost", src, alt: "", draggable: "false" });
  const lit = el("img", { src, alt: "", draggable: "false" });
  const win = el("div", { class: "crop-win", tabindex: "0", "aria-label": t("crop.hint") }, lit);
  const wrap = el("div", { class: "crop-edit", style: `left:${r.x * W}px;top:${r.y * H}px;width:${sw}px;height:${sh}px` },
    ghost, win);
  const clamp = v => Math.max(0, Math.min(1, v));
  const draw = () => {
    for (const im of [ghost, lit]) {
      Object.assign(im.style, { width: `${iw}px`, height: `${ih}px`, left: `${-(iw - sw) * fx}px`, top: `${-(ih - sh) * fy}px` });
    }
  };
  // dragging the photo to the right shows more of its left side
  const move = (dx, dy) => {
    if (iw - sw > 0.5) fx = clamp(fx - dx / (iw - sw));
    if (ih - sh > 0.5) fy = clamp(fy - dy / (ih - sh));
    draw();
  };
  let drag = null;
  win.addEventListener("pointerdown", e => {
    e.preventDefault();
    win.setPointerCapture(e.pointerId);
    drag = { x: e.clientX, y: e.clientY };
    wrap.classList.add("dragging");
  });
  win.addEventListener("pointermove", e => {
    if (!drag) return;
    move(e.clientX - drag.x, e.clientY - drag.y);
    drag = { x: e.clientX, y: e.clientY };
  });
  const stop = () => { drag = null; wrap.classList.remove("dragging"); };
  win.addEventListener("pointerup", stop);
  win.addEventListener("pointercancel", stop);

  const close = () => { wrap.remove(); bar.remove(); document.removeEventListener("keydown", keys, true); };
  const done = () => {
    const centred = Math.abs(fx - auto[0]) < 0.005 && Math.abs(fy - auto[1]) < 0.005;  // autobook's own cut: no entry
    bar.remove();
    document.removeEventListener("keydown", keys, true);
    ghost.remove();
    wrap.classList.add("result");  // shows the new cut until the pages are laid out again
    if (Math.abs(fx - start[0]) < 0.001 && Math.abs(fy - start[1]) < 0.001) { wrap.remove(); return; }
    P.change(t("crop.done"), pl => P.setCrop(pl, r.id, centred ? null : [+fx.toFixed(3), +fy.toFixed(3)]));
  };
  const keys = e => {
    const step = e.shiftKey ? 40 : 8;
    const k = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key];
    if (k) move(...k);
    else if (e.key === "Enter") done();
    else if (e.key === "Escape") close();
    else return;
    e.preventDefault();
    e.stopPropagation();
  };
  document.addEventListener("keydown", keys, true);
  const bar = el("div", { class: "crop-bar", role: "dialog", "aria-label": t("crop.choose") },
    el("span", { class: "crop-hint" }, icon("drag", 20), t("crop.hint")),
    el("button", { class: "btn ghost", onclick: () => { [fx, fy] = auto; draw(); } }, t("crop.centre")),
    el("button", { class: "btn ghost", onclick: close }, t("crop.cancel")),
    el("button", { class: "btn primary", onclick: done }, icon("check", 20), t("crop.ok")));
  draw();
  box.append(wrap);
  document.body.append(bar);
  win.focus({ preventScroll: true });
}

// ── which photos share a page ────────────────────────────────────────────
// The chapter's photo pages as shown (photo ids per page), in book order.
function chapterSplit(B, cid) {
  const mine = new Set((P.chapterById(B.plan, cid)?.photos || []).map(p => p.id));
  return shownPages.filter(pg => pg.kind === "photos" && pg.photos.some(id => mine.has(id)))
    .map(pg => pg.photos.filter(id => mine.has(id)));
}

const splitIndex = (split, pg) => split.findIndex(ids => ids.length && pg.photos.includes(ids[0]));

function moveTo(B, id, pg) {
  const c = P.chapterOf(B.plan, id);
  if (!c) return;
  const split = chapterSplit(B, c.id), to = splitIndex(split, pg);
  if (to < 0) { toast(t("move.otherChapter")); return; }
  if (split[to].includes(id)) return;
  P.change(t("move.done"), pl => P.moveToPage(pl, id, split, to));
}

// Menu items about the page: previous / next page, a page to itself, across both pages, back to normal
function pageItems(B, pg, p, close) {
  if (!p) return [];
  const id = p.id, layout = p.layout || "normal", c = P.chapterOf(B.plan, id);
  const item = (ic, label, fn, note = null) => el("button", { class: "pop-item", onclick: () => { close(); fn(); } },
    icon(ic, 20), note ? el("span", { class: "col" }, label, el("span", { class: "muted small" }, note)) : label);
  const out = [];
  if (layout === "normal" && pg.kind === "photos") {
    const split = chapterSplit(B, c.id), k = splitIndex(split, pg);
    if (k > 0) out.push(item("left", t("move.prev"), () => P.change(t("move.done"), pl => P.moveToPage(pl, id, split, k - 1))));
    if (k >= 0 && k < split.length - 1) out.push(item("right", t("move.next"), () => P.change(t("move.done"), pl => P.moveToPage(pl, id, split, k + 1))));
    out.push(item("cover", t("layout.page"), () => P.change(t("layout.pageDone"), pl => P.setLayout(pl, id, "page"))));
    if (photoShape(B, id) >= WIDE) {
      out.push(item("book", t("layout.spread"), () => P.change(t("layout.spreadDone"), pl => P.setLayout(pl, id, "spread")), t("layout.spreadHint")));
    }
  } else if (layout !== "normal") {
    out.push(item("smaller", t("layout.normal"), () => P.change(t("layout.normalDone"), pl => P.setLayout(pl, id, "normal"))));
  }
  if (c?.pages) out.push(item("swap", t("move.auto"), () => P.change(t("move.autoDone"), pl => P.setPages(pl, c.id, null))));
  return out;
}

// Drag a photo onto the other page of the spread (mouse, finger or pen); a short press still opens the menu.
function draggable(B, pg, r, hot) {
  let start = null, ghost = null;
  hot.addEventListener("pointerdown", e => { start = { x: e.clientX, y: e.clientY, id: e.pointerId }; });
  hot.addEventListener("pointermove", e => {
    if (!start) return;
    if (!ghost && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 10) {
      try { hot.setPointerCapture(start.id); } catch { /* the drag still works without it */ }
      ghost = el("img", { class: "drag-ghost", src: thumbUrl(B.name, r.id), alt: "" });
      document.body.append(ghost);
      hot.classList.add("dragging");
    }
    if (ghost) {
      ghost.style.left = `${e.clientX}px`;
      ghost.style.top = `${e.clientY}px`;
      document.querySelectorAll(".pg.drop").forEach(x => x.classList.remove("drop"));
      ghost.hidden = true;
      const over = document.elementFromPoint(e.clientX, e.clientY)?.closest(".pg");
      ghost.hidden = false;
      if (over && pageOfBox.get(over) !== pg) over.classList.add("drop");
    }
  });
  const end = e => {
    if (ghost) {
      ghost.remove();
      ghost = null;
      hot.classList.remove("dragging");
      hot.dataset.dragged = "1";  // no menu after a drag
      document.querySelectorAll(".pg.drop").forEach(x => x.classList.remove("drop"));
      const over = document.elementFromPoint(e.clientX, e.clientY)?.closest(".pg");
      const target = over && pageOfBox.get(over);
      if (target && target !== pg) {
        if (target.kind === "photos") moveTo(B, r.id, target);
        else toast(t("move.otherChapter"));
      }
    }
    start = null;
  };
  hot.addEventListener("pointerup", end);
  hot.addEventListener("pointercancel", () => { ghost?.remove(); ghost = null; start = null; hot.classList.remove("dragging"); });
}
