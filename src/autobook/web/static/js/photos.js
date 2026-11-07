// Photo building blocks shared by the screens.

import { thumbUrl } from "./api.js";
import { el, icon } from "./dom.js";
import { t } from "./i18n.js";

// A thumbnail that stays sharp on high-density screens (320 / 640 px versions).
export function img(B, id, { sizes = "(max-width: 700px) 50vw, 380px", alt = "" } = {}) {
  return el("img", {
    src: thumbUrl(B.name, id, 320),
    srcset: `${thumbUrl(B.name, id, 320)} 320w, ${thumbUrl(B.name, id, 640)} 640w`,
    sizes, loading: "lazy", decoding: "async", alt,
  });
}

// Width / height of a photo, from the catalog (upright) or the plan; kept within sensible bounds.
export function aspect(B, id, p = null) {
  const m = B.photos[id] || {};
  const w = m.w || p?.width || 3, h = m.h || p?.height || 2;
  return Math.max(0.45, Math.min(2.6, w / h)).toFixed(3);
}

export function favBadge(level, extraClass = "") {
  if (!level) return null;
  return el("span", { class: `badge fav ${extraClass}`, title: t("fav.badge") },
    Array.from({ length: Math.min(level, 3) }, () => icon("star", 14, { fill: "var(--gold)", color: "var(--gold)", width: 1.5 })));
}
