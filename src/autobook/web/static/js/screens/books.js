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

export async function books() {
  const data = await api("GET", "/api/books");
  Object.assign(G, data);
  const list = [...data.books].sort((a, b) => (b.updated || 0) - (a.updated || 0));
  show(el("div", { class: "page" },
    el("div", { class: "page-head" },
      el("h1", {}, t("books.title")),
      el("p", { class: "lead" }, list.length ? t("books.lead") : t("books.leadEmpty"))),
    el("div", { class: "shelf" },
      el("a", { class: "book-card new", href: "#/new" },
        el("div", { class: "new-cover" },
          el("span", { class: "new-plus" }, icon("plus", 34, { color: "#fff", width: 2.2 })),
          el("span", { class: "new-title" }, t("books.new"))),
        el("span", { class: "muted small center" }, t("books.newSub"))),
      list.map(card)),
    el("div", { class: "tip" }, icon("star", 26, { color: "var(--gold)", width: 1.8 }), el("p", {}, t("books.tip"))),
    G.ai_available ? null : el("p", { class: "muted small" }, t("books.noAi"), " ",
      el("button", { class: "link", onclick: keyDialog }, t("key.enter")))));
}
