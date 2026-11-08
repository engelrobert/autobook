// My books: covers on a shelf, the state of each book in words, and "make a new book".

import { keyDialog } from "../appinfo.js";
import { G, api, bookUrl, profileOf, thumbUrl } from "../api.js";
import { el, icon, show } from "../dom.js";
import { dateRange, num, t } from "../i18n.js";

const JOB_TEXT = { scan: "books.job.scan", select: "books.job.select", export: "books.job.export",
  release: "books.job.release", questions: "books.job.questions" };

function status(b) {
  if (b.job) return { working: true, text: t(JOB_TEXT[b.job.kind] || "books.job.select"),
    href: bookUrl(b.name, b.job.kind === "export" ? "book" : b.job.kind === "release" ? "print" : "making") };
  switch (b.state) {
    case "printed": return { ready: true, text: t("books.state.printed"), href: bookUrl(b.name, "print") };
    case "exported": return { text: t("books.state.exported"), href: bookUrl(b.name, "book") };
    case "planned": return { text: t("books.state.planned"), href: bookUrl(b.name, "selection") };
    default: return { text: t("books.state.scanned"), href: bookUrl(b.name, "wishes") };
  }
}

export function coverShape(profileName) {
  const p = profileOf(profileName);
  return p.width / p.height;
}

export function bookCover(b, { title = true, size = 640 } = {}) {
  const ar = coverShape(b.profile);
  // all covers about as tall as a landscape one: squares and portraits do not fill the whole width
  const cover = el("div", { class: "cover-mock", style: `aspect-ratio: ${ar}; width: min(100%, calc(var(--cover-h, 200px) * ${ar.toFixed(3)}))` },
    b.cover ? el("img", { src: thumbUrl(b.name, b.cover, size), alt: "", loading: "lazy" }) : el("div", { class: "cover-empty" }),
    el("span", { class: "spine" }),
    title && b.title ? el("span", { class: "cover-title" }, b.title) : null);
  return cover;
}

function card(b) {
  const s = status(b);
  const photos = b.in_book ? t("books.inBook", { n: b.in_book }) : t("books.photos", { n: b.photos || 0, nf: num(b.photos || 0) });
  return el("a", { class: "book-card", href: s.href },
    el("div", { class: "cover-stage" }, bookCover(b)),
    el("div", { class: "book-meta" },
      el("span", { class: "book-title" }, b.title || b.name),
      el("span", { class: "muted small" }, [dateRange(b.date_from, b.date_to), photos].filter(Boolean).join(" · ")),
      el("span", { class: "pill" + (s.ready ? " ok" : " accent") },
        s.ready ? icon("check", 16, { width: 2.6 }) : el("span", { class: "dot" + (s.working ? " pulse" : "") }), s.text)));
}

// Filters for the shelf, kept in this browser: search, state, year, order
const FKEY = "autobook.shelf";
const SORTS = {
  edited: (a, b) => (b.updated || 0) - (a.updated || 0),
  trip: (a, b) => String(b.date_from || "").localeCompare(String(a.date_from || "")),
  name: (a, b) => String(a.title || a.name).localeCompare(String(b.title || b.name), undefined, { sensitivity: "base" }),
};

function loadFilters() {
  try {
    return { q: "", state: "all", year: null, sort: "edited", ...JSON.parse(localStorage.getItem(FKEY) || "{}"), q: "" };
  } catch {
    return { q: "", state: "all", year: null, sort: "edited" };
  }
}

function saveFilters(f) {
  try { localStorage.setItem(FKEY, JSON.stringify({ state: f.state, year: f.year, sort: f.sort })); } catch { /* fine */ }
}

function matches(b, f) {
  const q = f.q.trim().toLowerCase();
  if (q && !`${b.title || ""} ${b.name}`.toLowerCase().includes(q)) return false;
  if (f.state === "done" && b.state !== "printed") return false;
  if (f.state === "working" && b.state === "printed") return false;
  return !f.year || String(b.date_from || "").slice(0, 4) === f.year;
}

function toolbar(all, f, redraw) {
  const seg = (label, opts, value, set) => el("div", { class: "seg", role: "radiogroup", "aria-label": label },
    opts.map(([v, text]) => el("button", { class: "seg-btn" + (v === value ? " on" : ""), role: "radio",
      "aria-checked": String(v === value), onclick: () => { set(v); saveFilters(f); redraw(); } }, text)));
  const search = el("input", { type: "search", class: "shelf-search", placeholder: t("shelf.search"),
    "aria-label": t("shelf.search"), value: f.q });
  search.addEventListener("input", () => { f.q = search.value; redraw(false); });
  const years = [...new Set(all.map(b => String(b.date_from || "").slice(0, 4)).filter(Boolean))].sort().reverse();
  if (f.year && !years.includes(f.year)) f.year = null;
  return el("div", { class: "shelf-tools" }, search,
    seg(t("shelf.state"), [["all", t("shelf.all")], ["working", t("shelf.working")], ["done", t("shelf.done")]],
      f.state, v => { f.state = v; }),
    years.length > 1 ? seg(t("shelf.year"), [[null, t("shelf.allYears")], ...years.map(y => [y, y])], f.year,
      v => { f.year = v; }) : null,
    el("label", { class: "row tight muted small" }, t("shelf.sort"),
      el("select", { onchange: e => { f.sort = e.target.value; saveFilters(f); redraw(false); } },
        ["edited", "trip", "name"].map(s => el("option", { value: s, selected: s === f.sort }, t(`shelf.sort.${s}`))))));
}

export async function books() {
  const data = await api("GET", "/api/books");
  Object.assign(G, data);
  const all = data.books, f = loadFilters();
  const shelf = el("div", { class: "shelf" });
  const tools = el("div", {});
  const drawShelf = () => {
    const list = all.filter(b => matches(b, f)).sort(SORTS[f.sort] || SORTS.edited);
    shelf.replaceChildren(...[  // (replaceChildren takes nodes, not lists like el() does)
      el("a", { class: "book-card new", href: "#/new" },
        el("div", { class: "new-cover" },
          el("span", { class: "new-plus" }, icon("plus", 34, { color: "#fff", width: 2.2 })),
          el("span", { class: "new-title" }, t("books.new"))),
        el("span", { class: "muted small center" }, t("books.newSub"))),
      list.map(card),
      all.length && !list.length ? el("p", { class: "muted shelf-none" }, t("shelf.none"), " ",
        el("button", { class: "link", onclick: () => {
          Object.assign(f, { q: "", state: "all", year: null });
          saveFilters(f);
          redraw();
        } }, t("shelf.reset"))) : null].flat().filter(Boolean));
  };
  // the toolbar is drawn again only when a button changed (typing in the search keeps its field)
  const redraw = (tb = true) => {
    if (tb && all.length > 1) tools.replaceChildren(toolbar(all, f, redraw));
    drawShelf();
  };
  redraw();
  show(el("div", { class: "page" },
    el("div", { class: "page-head" },
      el("h1", {}, t("books.title")),
      el("p", { class: "lead" }, all.length ? t("books.lead") : t("books.leadEmpty"))),
    tools,
    shelf,
    el("div", { class: "tip" }, icon("star", 26, { color: "var(--gold)", width: 1.8 }), el("p", {}, t("books.tip"))),
    G.ai_available ? null : el("p", { class: "muted small" }, t("books.noAi"), " ",
      el("button", { class: "link", onclick: keyDialog }, t("key.enter")))));
}
