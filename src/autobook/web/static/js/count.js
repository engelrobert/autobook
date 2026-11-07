// The photo-count slider for the whole book. While you drag, the server works out which photos
// would come in or go (nothing is saved) and the page shows it; letting go applies it as a normal
// change with Undo. Ticks mark counts that fill whole pages the printer takes; letting go close
// to one snaps to it.

import { api, bookApi } from "./api.js";
import { el } from "./dom.js";
import { num, t } from "./i18n.js";
import * as P from "./plan.js";

const SNAP = 2;  // photos

export const photosIn = plan => plan.chapters.reduce((n, c) => n + c.photos.length, 0);

export function countSlider(B, { onPreview = () => {}, onApply = () => {} } = {}) {
  const now = photosIn(B.plan);
  // from the photos that never go (must-have favourites, those you put in) to everything the days hold
  const min = Math.min(now, B.least || B.plan.chapters.length), max = Math.max(now, B.capacity || now);
  const fits = (B.fit_points || []).filter(f => f.photos >= min && f.photos <= max);
  const input = el("input", { type: "range", min: String(min), max: String(max), step: "1", value: String(now),
    "aria-label": t("count.label"), disabled: max <= min });
  const value = el("strong", { class: "serif big-num" }, num(now));
  const sub = el("span", { class: "muted" });
  const pct = n => (max > min ? (100 * (n - min)) / (max - min) : 0);
  const fitAt = n => fits.find(f => f.photos === n);
  const describe = (n, pages, added = 0, removed = 0) => {
    const parts = [fitAt(n) ? t("count.fits", { n: fitAt(n).pages }) : t("count.pages", { n: pages })];
    if (added) parts.push(t("count.added", { n: added }));
    if (removed) parts.push(t("count.removed", { n: removed }));
    sub.textContent = parts.join(" · ");
  };
  describe(now, B.pages_estimate || 0);

  let timer = null, seq = 0;
  const ask = async target => {
    await P.flush();  // the server works from the saved plan
    return api("POST", bookApi(B.name, "resize"), { target });
  };
  input.addEventListener("input", () => {
    value.textContent = num(Number(input.value));
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const mine = ++seq;
      try {
        const r = await ask(Number(input.value));
        if (mine !== seq) return;
        describe(r.count, r.pages, r.added.length, r.removed.length);
        onPreview(r);
      } catch { /* the preview is a bonus */ }
    }, 150);
  });
  input.addEventListener("change", async () => {
    clearTimeout(timer);
    seq++;
    let target = Number(input.value);
    const current = photosIn(B.plan);
    if (target !== current) {  // only a real move snaps: letting go where it was changes nothing
      const near = fits.reduce((best, f) => (Math.abs(f.photos - target) <= SNAP
        && (!best || Math.abs(f.photos - target) < Math.abs(best.photos - target)) ? f : best), null);
      if (near) target = near.photos;
    }
    input.value = String(target);
    value.textContent = num(target);
    if (target === current) { onPreview(null); describe(current, B.pages_estimate || 0); return; }
    const r = await ask(target);
    describe(r.count, r.pages);
    B.pages_estimate = r.pages;
    P.change(t("count.done", { n: r.count }), pl => { pl.chapters = r.plan.chapters; });
    onApply(r);
  });
  return el("section", { class: "count-card" },
    el("div", { class: "row between" },
      el("span", { class: "count-label" }, t("count.label"), " ", value),
      sub),
    el("div", { class: "count-track" }, input,
      el("div", { class: "count-ticks", "aria-hidden": "true" },
        fits.map(f => el("span", { class: "tick", style: `left:${pct(f.photos)}%`, title: t("count.fits", { n: f.pages }) })))),
    el("div", { class: "row between muted small" }, el("span", {}, t("count.fewer")), el("span", {}, t("count.more"))));
}

// − / + for one chapter
export async function chapterStep(B, cid, delta) {
  await P.flush();
  const r = await api("POST", bookApi(B.name, "resize"), { chapter: cid, delta });
  if (!r.added.length && !r.removed.length) return;
  B.pages_estimate = r.pages;
  P.change(t(delta > 0 ? "count.chapterMore" : "count.chapterFewer"), pl => { pl.chapters = r.plan.chapters; });
}
