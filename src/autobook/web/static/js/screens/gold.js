// Favourites test (under "More"): mark the photos you would choose yourself, to compare them with
// the AI's choice (`autobook eval`). Books with Favoriten folders or "+" names have this already.

import { api, bookApi } from "../api.js";
import { el, show, toast } from "../dom.js";
import { num, t } from "../i18n.js";
import { aspect, favBadge, img } from "../photos.js";

export function gold(B) {
  const marked = new Set((B.gold && B.gold.favourites) || []);
  const ids = Object.entries(B.photos).sort((a, b) => (a[1].taken < b[1].taken ? -1 : 1)).map(([id]) => id);
  const count = el("span", { class: "muted" });
  const update = () => { count.textContent = t("gold.count", { n: marked.size, total: num(ids.length) }); };
  const grid = el("div", { class: "jgrid small" }, ids.slice(0, 1500).map(id => {
    const fig = el("figure", { class: "tile pickable" + (marked.has(id) ? " picked" : ""), style: `--ar:${aspect(B, id)}` },
      el("button", { class: "tile-img", "aria-pressed": String(marked.has(id)), onclick: () => {
        marked.has(id) ? marked.delete(id) : marked.add(id);
        fig.classList.toggle("picked", marked.has(id));
        fig.firstChild.setAttribute("aria-pressed", String(marked.has(id)));
        update();
      } }, img(B, id, { sizes: "220px" })),
      favBadge(B.photos[id].fav || 0, "bl"));
    return fig;
  }));
  update();
  show(el("div", { class: "page wide" },
    el("div", { class: "sel-head" },
      el("div", { class: "col" }, el("h1", {}, t("gold.title")), el("p", { class: "lead" }, t("gold.lead"))),
      el("div", { class: "row" }, count, el("button", { class: "btn primary", onclick: async () => {
        const r = await api("POST", bookApi(B.name, "gold"), { favourites: [...marked] });
        toast(t("gold.saved", { n: r.count }));
      } }, t("gold.save")))),
    grid));
}
