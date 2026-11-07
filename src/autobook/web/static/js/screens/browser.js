// "Fotos hinzufügen": every photo of a chapter's days (or of the whole trip), grouped by day, with
// what autobook knows about each one. Tap photos to tick them, then add them all at once.

import { el, icon } from "../dom.js";
import { num, t, weekday } from "../i18n.js";
import * as P from "../plan.js";
import { aspect, favBadge, img } from "../photos.js";
import { openLightbox } from "./lightbox.js";

const PAGE = 120;  // photos shown before "Mehr zeigen"

export function photoBrowser(B, c, { onClose }) {
  const S = { filter: "out", wide: false, picked: new Set(), limit: PAGE };
  const panel = el("section", { class: "browser", "aria-label": t("browse.title") });

  const inBook = () => {
    const ids = new Set(B.plan.chapters.flatMap(ch => ch.photos.map(p => p.id)));
    B.plan.chapters.forEach(ch => ch.divider.photo && ids.add(ch.divider.photo));
    if (B.plan.book.cover_photo) ids.add(B.plan.book.cover_photo);
    return ids;
  };
  const reasons = Object.fromEntries((B.plan.rejected || []).map(r => [r.id, r.reason]));

  function pool() {
    const events = new Set(c.event_ids || []);
    const from = S.wide ? B.plan.book.date_from : c.date_from, to = S.wide ? B.plan.book.date_to : c.date_to;
    return Object.entries(B.photos).filter(([id, m]) => {
      if (reasons[id] === "screenshot") return false;
      if (!S.wide && events.size) return events.has(m.event);
      const day = (m.taken || "").slice(0, 10);
      return day >= from && day <= to;
    }).sort((a, b) => (a[1].taken < b[1].taken ? -1 : 1));
  }

  function draw() {
    const book = inBook();
    const shownMoments = new Set([...book].map(id => B.photos[id]?.dup).filter(Boolean));
    const all = pool();
    const list = all.filter(([id, m]) => S.filter === "all" || (S.filter === "favs" ? m.fav > 0 : !book.has(id)));
    const visible = list.slice(0, S.limit);
    const days = [];
    for (const [id, m] of visible) {
      const day = (m.taken || "").slice(0, 10);
      if (!days.length || days[days.length - 1].day !== day) days.push({ day, ids: [] });
      days[days.length - 1].ids.push(id);
    }
    const zoomIds = visible.map(([id]) => id);
    const tileFor = id => {
      const m = B.photos[id], here = book.has(id), on = S.picked.has(id);
      const note = here ? t("browse.inBook") : m.blur || reasons[id] === "blur" ? t("browse.blurry")
        : shownMoments.has(m.dup) ? t("browse.similar") : null;
      return el("figure", { class: "tile pickable" + (on ? " picked" : "") + (here ? " in-book" : ""), style: `--ar:${aspect(B, id)}` },
        el("button", { class: "tile-img", "aria-pressed": String(on), "aria-label": here ? t("browse.inBook") : t("browse.tick"),
          disabled: here, onclick: () => { if (on) S.picked.delete(id); else S.picked.add(id); draw(); } }, img(B, id, { sizes: "220px" })),
        favBadge(m.fav || 0, "bl"),
        note ? el("span", { class: "badge dark tl" }, note) : null,
        el("button", { class: "act zoom", "aria-label": t("browse.zoom"), title: t("browse.zoom"),
          onclick: e => { e.stopPropagation(); openLightbox(B, { ids: zoomIds, index: zoomIds.indexOf(id), mode: "alt", chapter: c.id }); } },
        icon("bigger", 18)));
    };
    const chip = (key, label) => el("button", { class: "pill-choice" + (S.filter === key ? " on" : ""), "aria-pressed": String(S.filter === key),
      onclick: () => { S.filter = key; S.limit = PAGE; draw(); } }, label);
    const wide = el("input", { type: "checkbox", checked: S.wide });
    wide.addEventListener("change", () => { S.wide = wide.checked; S.limit = PAGE; draw(); });
    const n = S.picked.size;
    panel.replaceChildren(...[  // DOM append would print "null"
      el("div", { class: "row between" },
        el("h3", {}, t("browse.title")),
        el("button", { class: "icon-btn", "aria-label": t("browse.close"), onclick: onClose }, icon("close", 22))),
      el("div", { class: "row" },
        el("div", { class: "pills" }, chip("out", t("browse.notIn")), chip("all", t("browse.all")), chip("favs", t("browse.favs"))),
        el("label", { class: "check-row" }, wide, el("span", {}, t("browse.otherDays")))),
      list.length ? null : el("p", { class: "muted" }, t("browse.none")),
      ...days.map(d => el("div", { class: "browse-day" },
        el("h4", { class: "muted" }, weekday(d.day)),
        el("div", { class: "jgrid small" }, d.ids.map(tileFor)))),
      list.length > S.limit ? el("button", { class: "btn ghost", onclick: () => { S.limit += PAGE; draw(); } },
        t("browse.more", { n: num(list.length - S.limit) })) : null,
      el("div", { class: "browse-bar" },
        el("span", { class: "muted" }, t("browse.count", { n: list.length })),
        el("button", { class: "btn ghost", onclick: onClose }, t("browse.close")),
        el("button", { class: "btn primary", disabled: !n, onclick: () => {
          const ids = [...S.picked];
          P.change(t("browse.done", { n: ids.length }), pl => ids.forEach(id => P.addPhoto(pl, B, c.id, id)));
          onClose();
        } }, icon("plus", 20), t("browse.add", { n }))),
    ].filter(Boolean));
  }
  draw();
  return panel;
}
