// Step 3: the chosen photos per chapter, large and in their own shape. Click a photo to see it
// big; change titles, the cover and the chapter photos; everything is saved by itself.

import { api, bookApi, bookUrl } from "../api.js";
import { chapterStep, countSlider } from "../count.js";
import { el, icon, popup, show } from "../dom.js";
import { dateRange, shortDay, t, weekday } from "../i18n.js";
import { onLeave } from "../main.js";
import * as P from "../plan.js";
import { aspect, favBadge, img } from "../photos.js";
import { bookCover } from "./books.js";
import { photoBrowser } from "./browser.js";
import { openLightbox } from "./lightbox.js";

let pageOf = {};
const openAlts = new Set();
let statusEl = null;
let unsubscribe = null;

export async function selection(B) {
  if (!B.plan) {
    location.hash = bookUrl(B.name, B.job ? "making" : "wishes");
    return;
  }
  P.attach(B, () => draw(B));
  onLeave(P.flush);
  statusEl = el("span", { class: "save-status" });
  if (unsubscribe) unsubscribe();
  unsubscribe = P.onStatus(s => {
    statusEl.replaceChildren(...(s === "saved" ? [icon("check", 18, { width: 2.4 }), t("save.saved")]
      : s === "error" ? [icon("warn", 18), t("save.error"), el("button", { class: "link", onclick: () => P.save() }, t("save.retry"))]
        : [el("span", { class: "spinner small" }), t("save.saving")]));
    statusEl.className = "save-status " + s;
  });
  pageOf = {};
  draw(B);
  const focus = sessionStorage.getItem("focus");
  if (focus) {
    sessionStorage.removeItem("focus");
    focusPhoto(focus);
  }
  if (B.exported && !B.layout_outdated) {
    try {
      const r = await api("GET", bookApi(B.name, "pages"));
      for (const pg of r.pages) for (const id of pg.photos) pageOf[id] = pg.n;
      if (location.hash.endsWith("/selection")) draw(B);
    } catch { /* page numbers are a bonus */ }
  }
}

function focusPhoto(id) {
  const e = document.getElementById(`ph-${id}`);
  if (!e) return;
  e.scrollIntoView({ block: "center" });
  e.classList.add("flash");
  setTimeout(() => e.classList.remove("flash"), 2400);
}

// A title that turns into a text field when you click it (or its pencil). A subtitle may be empty:
// then it shows the placeholder, and saving an empty field clears it.
export function editable(text, onSave, tag, label, { allowEmpty = false, placeholder = "", cls = "" } = {}) {
  const box = el("div", { class: "editable" + (cls ? ` ${cls}` : "") });
  const view = () => box.replaceChildren(
    el(tag, { class: "edit-text" + (text ? "" : " placeholder"), tabindex: "0", onclick: edit,
      onkeydown: e => { if (e.key === "Enter") edit(); } }, text || placeholder),
    el("button", { class: "icon-btn quiet", "aria-label": label, onclick: edit }, icon("pencil", 20, { width: 1.8 })));
  function edit() {
    const input = el("input", { type: "text", value: text || "", placeholder, class: `edit-input ${tag}`, "aria-label": label });
    let done = false;
    const commit = () => {
      if (done) return;
      done = true;
      const v = input.value.trim();
      if ((v || allowEmpty) && v !== (text || "")) onSave(v); else view();
    };
    input.addEventListener("keydown", e => {
      if (e.key === "Enter") commit();
      if (e.key === "Escape") { done = true; view(); }
    });
    input.addEventListener("blur", commit);
    box.replaceChildren(input);
    input.focus();
    input.select();
  }
  view();
  return box;
}

function draw(B) {
  const plan = B.plan, scrollY = window.scrollY;
  const total = P.photoCount(plan);
  const pages = B.pages_estimate || 0;
  show(el("div", { class: "page wide" },
    el("div", { class: "sel-head" },
      el("div", { class: "col" },
        editable(plan.book.title, v => P.change(t("done.title"), p => { p.book.title = v; }), "h1", t("sel.editTitle")),
        editable(plan.book.subtitle, v => P.change(t("done.subtitle"), p => { p.book.subtitle = v; }), "p",
          t("sel.editBookSubtitle"), { allowEmpty: true, placeholder: t("sel.addSubtitle"), cls: "subtitle" }),
        el("span", { class: "muted" }, t("sel.stats", { n: total, c: t("sel.chapterCount", { n: plan.chapters.length }), p: pages }))),
      el("div", { class: "row" }, statusEl,
        el("a", { class: "btn primary big", href: bookUrl(B.name, "book") }, t("sel.next"), icon("next", 22)))),
    countSlider(B, { onPreview: r => preview(B, r) }),
    plan.warnings && plan.warnings.length ? el("details", { class: "card note" },
      el("summary", {}, t("sel.warnings", { n: plan.warnings.length })), el("pre", {}, plan.warnings.join("\n"))) : null,
    coverCard(B),
    el("div", { class: "sel-body" },
      chapterNav(B),
      el("div", { class: "chapters" }, plan.chapters.map(c => chapterSection(B, c))))));
  window.scrollTo(0, scrollY);
}

function coverCard(B) {
  const b = B.plan.book;
  const cands = [...new Set([b.cover_photo, ...(b.cover_candidates || [])].filter(Boolean))].slice(0, 7);
  return el("section", { class: "card cover-card" },
    el("div", { class: "cover-big" }, bookCover({ name: B.name, profile: b.profile, cover: b.cover_photo, title: b.title })),
    el("div", { class: "col grow" },
      el("h2", {}, t("sel.coverTitle")),
      el("p", { class: "muted" }, t("sel.coverLead")),
      el("div", { class: "cover-cands" }, cands.map(id => el("button", {
        class: "cand" + (id === b.cover_photo ? " on" : ""), "aria-pressed": String(id === b.cover_photo),
        "aria-label": t("act.cover"),
        onclick: () => { if (id !== b.cover_photo) P.change(t("done.cover"), p => { p.book.cover_photo = id; }); },
      }, img(B, id, { sizes: "140px" }))))));
}

function chapterDates(c) {
  return c.date_from === c.date_to ? weekday(c.date_from) : dateRange(c.date_from, c.date_to, { day: "numeric", month: "long", year: "numeric" });
}

function chapterNav(B) {
  return el("nav", { class: "ch-nav card", "aria-label": t("sel.chapters") },
    el("span", { class: "label" }, t("sel.chapters")),
    B.plan.chapters.map(c => {
      const thumbId = c.divider.photo || c.photos[0]?.id;
      return el("a", { href: `#ch-${c.id}`, onclick: e => {
        e.preventDefault();
        document.getElementById(`ch-${c.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
      } }, thumbId ? img(B, thumbId, { sizes: "48px" }) : el("span", { class: "nav-empty" }),
      el("span", { class: "col" }, el("span", { class: "nav-title" }, c.title),
        el("span", { class: "muted small" }, `${shortDay(c.date_from)} · ${t("sel.photos", { n: c.photos.length })}`)));
    }));
}

function chapterSection(B, c) {
  const opener = c.divider.photo;
  const ids = c.photos.map(p => p.id);
  const open = id => openLightbox(B, { ids, index: ids.indexOf(id), mode: "book", chapter: c.id });
  return el("section", { class: "card chapter", id: `ch-${c.id}` },
    el("div", { class: "ch-head" },
      el("div", { class: "col" },
        editable(c.title, v => P.change(t("done.title"), p => { P.chapterById(p, c.id).title = v; }), "h2", t("sel.editChapter")),
        editable(c.subtitle, v => P.change(t("done.subtitle"), p => { P.chapterById(p, c.id).subtitle = v; }), "p",
          t("sel.editSubtitle"), { allowEmpty: true, placeholder: t("sel.addSubtitle"), cls: "subtitle" }),
        el("div", { class: "row tight" }, el("span", { class: "muted" }, chapterDates(c)),
          el("span", { class: "stepper", role: "group", "aria-label": t("count.chapter") },
            el("button", { class: "step-btn", "aria-label": t("count.chapterFewer"), disabled: c.photos.length <= 1,
              onclick: () => chapterStep(B, c.id, -1) }, "−"),
            el("span", { class: "step-n" }, t("sel.photos", { n: c.photos.length })),
            el("button", { class: "step-btn", "aria-label": t("count.chapterMore"), onclick: () => chapterStep(B, c.id, +1) }, "+")))),
      opener ? el("button", { class: "opener", onclick: () => openLightbox(B, { ids: [opener], index: 0, mode: "opener", chapter: c.id }) },
        img(B, opener, { sizes: "160px" }),
        el("span", { class: "col" }, el("strong", {}, t("sel.opener")), el("span", { class: "muted small" }, t("sel.openerSub"))))
        : el("span", { class: "muted small opener-none" }, t("sel.noOpener"))),
    c.photos.length ? el("div", { class: "jgrid" }, c.photos.map(p => tile(B, c, p, open)))
      : el("p", { class: "muted" }, t("sel.emptyChapter")),
    el("div", { class: "incoming", id: `in-${c.id}`, hidden: true }),
    addPhotos(B, c));
}

// What the photo-count slider would change: leaving photos dimmed, newcomers per chapter marked "neu"
function preview(B, r) {
  document.querySelectorAll(".tile.leaving").forEach(e => e.classList.remove("leaving"));
  document.querySelectorAll(".incoming").forEach(e => { e.hidden = true; e.replaceChildren(); });
  if (!r) return;
  for (const id of r.removed) document.getElementById(`ph-${id}`)?.classList.add("leaving");
  const added = new Set(r.added);
  for (const c of r.plan.chapters) {
    const ids = c.photos.map(p => p.id).filter(id => added.has(id));
    const box = document.getElementById(`in-${c.id}`);
    if (!box || !ids.length) continue;
    box.hidden = false;
    box.append(el("p", { class: "muted small" }, t("count.incoming", { n: ids.length })),
      el("div", { class: "jgrid small" }, ids.slice(0, 24).map(id => el("figure", { class: "tile alt", style: `--ar:${aspect(B, id)}` },
        img(B, id, { sizes: "200px" }), el("span", { class: "badge new tl" }, t("count.new"))))));
  }
}

function actBtn(name, label, onclick, cls = "") {
  return el("button", { class: `act ${cls}`, title: label, "aria-label": label, onclick: e => { e.stopPropagation(); onclick(); } },
    icon(name, 20, { width: 2.2 }));
}

function tile(B, c, p, open) {
  const fav = B.photos[p.id]?.fav || 0;
  const big = p.weight > 1;
  const bigger = () => P.change(big ? t("done.smaller") : t("done.bigger"), pl => P.toggleBig(pl, p.id));
  const remove = () => P.change(t("done.removed"), pl => P.removePhoto(pl, p.id));
  const others = () => openLightbox(B, { ids: c.photos.map(x => x.id), index: c.photos.indexOf(p), mode: "book", chapter: c.id });
  const more = el("button", { class: "tile-more", "aria-label": t("act.menu"), onclick: e => {
    e.stopPropagation();
    popup(more, [
      { icon: big ? "smaller" : "bigger", label: big ? t("act.smaller") : t("act.bigger"), onclick: bigger },
      { icon: "swap", label: t("act.swap"), onclick: others },
      { icon: "close", label: t("act.remove"), onclick: remove, danger: true },
    ]);
  } }, icon("more", 20, { width: 2.4 }));
  return el("figure", { class: "tile", id: `ph-${p.id}`, style: `--ar:${aspect(B, p.id, p)}` },
    el("button", { class: "tile-img", "aria-label": t("act.open"), onclick: () => open(p.id) }, img(B, p.id)),
    favBadge(fav, "bl"),
    big ? el("span", { class: "badge dark tl" }, t("sel.bigBadge"))
      : p.layout === "page" ? el("span", { class: "badge dark tl" }, t("sel.pageBadge"))
        : p.layout === "spread" ? el("span", { class: "badge dark tl" }, t("sel.spreadBadge")) : null,
    pageOf[p.id] != null ? el("span", { class: "badge dark br" }, t("sel.page", { n: pageOf[p.id] })) : null,
    el("div", { class: "acts" },
      actBtn(big ? "smaller" : "bigger", big ? t("act.smaller") : t("act.bigger"), bigger),
      actBtn("swap", t("act.swap"), others),
      actBtn("close", t("act.remove"), remove, "danger")),
    more);
}

// "Fotos hinzufügen": all photos of the chapter's days in a browser (screens/browser.js)
function addPhotos(B, c) {
  if (!openAlts.has(c.id)) {
    return el("button", { class: "btn dashed", "aria-expanded": "false", onclick: () => { openAlts.add(c.id); draw(B); } },
      icon("plus", 20), t("browse.open"));
  }
  return photoBrowser(B, c, { onClose: () => { openAlts.delete(c.id); draw(B); } });
}
