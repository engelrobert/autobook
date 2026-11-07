// One photo large: browse with ← →, close with Esc, and big buttons for what you can do with it.
// Below: other shots of the same moment; one click swaps them.

import { api, bookApi, thumbUrl } from "../api.js";
import { el, icon } from "../dom.js";
import { clock, getLang, t, weekday } from "../i18n.js";
import * as P from "../plan.js";

let S = null;  // { B, ids, index, mode: book | alt | opener, chapter, back, sibs }

export function openLightbox(B, { ids, index = 0, mode = "book", chapter }) {
  closeLightbox();
  S = { B, ids: [...ids], index: Math.max(0, index), mode, chapter, back: document.activeElement, sibs: {} };
  S.onKey = e => {
    if (e.target && e.target.tagName === "SELECT") return;
    if (e.key === "Escape") closeLightbox();
    else if (e.key === "ArrowLeft") go(-1);
    else if (e.key === "ArrowRight") go(1);
  };
  document.addEventListener("keydown", S.onKey);
  document.body.classList.add("no-scroll");
  draw();
  document.querySelector(".lb-close")?.focus();
}

export function closeLightbox() {
  if (!S) return;
  document.removeEventListener("keydown", S.onKey);
  const ov = document.getElementById("overlay");
  ov.hidden = true;
  ov.replaceChildren();
  document.body.classList.remove("no-scroll");
  const back = S.back;
  S = null;
  if (back && back.isConnected) back.focus();
}

function go(d) {
  const n = S.ids.length;
  if (n < 2) return;
  S.index = (S.index + d + n) % n;
  draw();
}

// After a change that takes the photo out of this list (removed, moved, became the opener).
function dropCurrent() {
  S.ids.splice(S.index, 1);
  if (!S.ids.length) {
    closeLightbox();
    return;
  }
  S.index = Math.min(S.index, S.ids.length - 1);
  draw();
}

function action(iconName, title, sub, onclick, { on = false, danger = false } = {}) {
  return el("button", { class: "lb-action" + (on ? " on" : "") + (danger ? " danger" : ""), "aria-pressed": on ? "true" : null, onclick },
    icon(iconName, 24), el("span", { class: "col" }, el("span", { class: "lb-act-title" }, title), sub ? el("span", { class: "lb-act-sub" }, sub) : null));
}

function actions() {
  const { B, mode } = S;
  const id = S.ids[S.index];
  const plan = B.plan;
  const p = P.photoOf(plan, id);
  const out = [];
  const fav = B.photos[id]?.fav || 0;
  if (fav) out.push(el("span", { class: "pill gold" }, icon("star", 15, { fill: "var(--gold)", color: "var(--gold)", width: 1.5 }),
    fav >= 2 ? t("lb.favBest") : t("lb.fav")));
  if (p && p.reason && getLang() === "en") out.push(el("p", { class: "lb-reason" }, `“${p.reason}”`));
  if (mode === "book" && p) {
    const big = p.weight > 1;
    out.push(action(big ? "smaller" : "bigger", big ? t("lb.isBig") : t("act.bigger"), big ? t("lb.isBigSub") : t("lb.biggerSub"),
      () => { P.change(big ? t("done.smaller") : t("done.bigger"), pl => P.toggleBig(pl, id)); draw(); }, { on: big }));
    out.push(action("chapter", t("act.opener"), t("lb.openerSub"),
      () => { P.change(t("done.opener"), pl => P.setOpener(pl, B, id)); dropCurrent(); }));
    out.push(action("cover", plan.book.cover_photo === id ? t("lb.isCover") : t("act.cover"), t("lb.coverSub"),
      () => { P.change(t("done.cover"), pl => { pl.book.cover_photo = id; }); draw(); }, { on: plan.book.cover_photo === id }));
    const others = plan.chapters.filter(c => c.id !== S.chapter);
    if (others.length) {
      const sel = el("select", { "aria-label": t("act.move") },
        el("option", { value: "" }, t("act.move")), others.map(c => el("option", { value: c.id }, c.title)));
      sel.addEventListener("change", () => {
        if (!sel.value) return;
        const target = P.chapterById(plan, sel.value);
        P.change(t("done.moved", { title: target.title }), pl => P.moveTo(pl, id, sel.value));
        dropCurrent();
      });
      out.push(el("label", { class: "lb-action select" }, icon("move", 24), sel));
    }
    out.push(action("close", t("act.remove"), t("lb.removeSub"),
      () => { P.change(t("done.removed"), pl => P.removePhoto(pl, id)); dropCurrent(); }, { danger: true }));
  } else if (mode === "alt") {
    out.push(action("plus", t("act.add"), t("lb.addSub"),
      () => { P.change(t("done.added"), pl => P.addPhoto(pl, B, S.chapter, id)); dropCurrent(); }));
  } else if (mode === "opener") {
    out.push(action("chapter", t("lb.openerBack"), t("lb.openerBackSub"),
      () => { P.change(t("done.openerBack"), pl => P.openerBack(pl, B, S.chapter)); closeLightbox(); }));
  }
  return out;
}

async function siblings(strip, id) {
  const { B } = S;
  if (!(id in S.sibs)) {
    try {
      S.sibs[id] = (await api("GET", bookApi(B.name, `siblings/${id}`))).siblings;
    } catch {
      S.sibs[id] = [];
    }
  }
  if (!S || S.ids[S.index] !== id) return;
  const list = S.sibs[id];
  if (!list.length) {
    strip.replaceChildren(el("span", { class: "lb-sib-title" }, t("lb.noSiblings")));
    return;
  }
  const canSwap = S.mode !== "alt";
  strip.replaceChildren(
    el("span", { class: "lb-sib-title" }, t("lb.siblings"), canSwap ? el("span", { class: "soft" }, ` – ${t("lb.siblingsHint")}`) : null),
    el("div", { class: "lb-sib-row" },
      el("span", { class: "lb-sib on" }, el("img", { src: thumbUrl(B.name, id), alt: "" }), el("span", { class: "lb-sib-tag" }, t("lb.inBook"))),
      list.map(s => el("button", { class: "lb-sib", "aria-label": t("lb.takeThis"), disabled: !canSwap, onclick: () => {
        P.change(t("done.swapped"), pl => P.swap(pl, B, id, s.id));
        S.sibs[s.id] = [{ ...s, id }, ...list.filter(x => x.id !== s.id)];  // the old shot is now a sibling
        S.ids[S.index] = s.id;
        draw();
      } }, el("img", { src: thumbUrl(B.name, s.id), alt: "" }),
      s.fav ? el("span", { class: "lb-sib-star" }, icon("star", 13, { fill: "var(--gold)", color: "var(--gold)", width: 1.5 })) : null))));
}

function draw() {
  const { B, mode } = S;
  const id = S.ids[S.index];
  const ch = P.chapterById(B.plan, S.chapter);
  const m = B.photos[id] || {};
  const where = mode === "alt" ? t("lb.altCount", { i: S.index + 1, n: S.ids.length })
    : mode === "opener" ? t("sel.opener") : t("lb.count", { i: S.index + 1, n: S.ids.length });
  const strip = el("div", { class: "lb-sibs" });
  const nav = S.ids.length > 1;
  const box = el("div", { class: "lb", role: "dialog", "aria-modal": "true", "aria-label": t("lb.label") },
    el("div", { class: "lb-top" },
      el("div", { class: "col grow" },
        el("span", { class: "serif lb-title" }, ch ? ch.title : ""),
        el("span", { class: "soft" }, [where, m.taken ? `${weekday(m.taken)}, ${clock(m.taken)}` : ""].filter(Boolean).join(" · "))),
      nav ? el("span", { class: "soft small keys" }, t("lb.keys")) : null,
      el("button", { class: "lb-close", "aria-label": t("lb.close"), onclick: closeLightbox }, icon("close", 26, { color: "#fff", width: 2.2 }))),
    el("div", { class: "lb-main" },
      el("div", { class: "lb-stage" },
        nav ? el("button", { class: "lb-nav", "aria-label": t("lb.prev"), onclick: () => go(-1) }, icon("left", 28, { color: "#fff", width: 2.2 })) : null,
        el("div", { class: "lb-img" }, el("img", { src: thumbUrl(B.name, id, 1024), alt: "" })),
        nav ? el("button", { class: "lb-nav", "aria-label": t("lb.next"), onclick: () => go(1) }, icon("right", 28, { color: "#fff", width: 2.2 })) : null),
      el("aside", { class: "lb-side" }, actions())),
    strip);
  const ov = document.getElementById("overlay");
  ov.replaceChildren(box);
  ov.hidden = false;
  siblings(strip, id);
}
