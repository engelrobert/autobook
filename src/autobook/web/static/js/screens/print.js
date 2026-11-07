// Step 5: the print file. Made by itself when it is missing or out of date; the printer's checks
// are shown as a short checklist in plain words, the technical report folded away.

import { api, bookApi, bookUrl, poll, profileOf, runJob } from "../api.js";
import { el, icon, show } from "../dom.js";
import { num, t } from "../i18n.js";
import * as P from "../plan.js";
import { layout } from "./book.js";
import { bookCover } from "./books.js";

export async function printStep(B) {
  if (!B.plan) {
    location.hash = bookUrl(B.name, "wishes");
    return;
  }
  P.attach(B);
  await P.flush();
  if (!B.exported || B.layout_outdated || (B.job && B.job.kind === "export")) {
    if (!(await layout(B))) return;
  }
  let rep = B.release;
  if (!rep || B.print_outdated || (B.job && B.job.kind === "release")) {
    rep = await makePrintFile(B);
    if (!rep) return;
  }
  render(B, rep);
}

function cover(B) {
  const b = B.plan.book;
  return el("div", { class: "print-cover" }, bookCover({ name: B.name, profile: b.profile, cover: b.cover_photo, title: b.title }, { size: 1024 }));
}

async function makePrintFile(B) {
  show(el("div", { class: "page narrow center" }, cover(B),
    el("h1", {}, t("print.making")), el("p", { class: "lead" }, t("print.makingLead")),
    el("div", { class: "loading" }, el("span", { class: "spinner" }), t("print.makingWait"))));
  try {
    const rep = B.job && B.job.kind === "release" ? await poll(B.job.id) : await runJob(bookApi(B.name, "release"));
    B.job = null;
    B.release = rep;
    B.print_outdated = false;
    return rep;
  } catch (e) {
    show(el("div", { class: "page narrow" }, el("h1", {}, t("print.failed")),
      el("details", { class: "card" }, el("summary", {}, t("print.details")), el("pre", {}, e.message)),
      el("a", { class: "btn", href: bookUrl(B.name, "book") }, t("book.title"))));
    return null;
  }
}

function checkItems(B, rep) {
  const by = Object.fromEntries(rep.checks.filter(c => c.id).map(c => [c.id, c]));
  const prof = profileOf(B.plan.book.profile);
  const items = [];
  const pages = by.pages;
  if (pages) {
    items.push(pages.ok ? { ok: true, title: t("print.pages", { n: pages.values.n, photos: P.photoCount(B.plan) }),
      sub: t("print.pagesSub", { min: pages.values.min, max: pages.values.max }) }
      : { ok: false, title: t(pages.values.n < pages.values.min ? "print.pagesFew" : "print.pagesMany", pages.values),
        sub: t(pages.values.n < pages.values.min ? "print.pagesFewDo" : "print.pagesManyDo"), link: bookUrl(B.name, "selection") });
  }
  const res = by.resolution, low = by.low_dpi;
  if (res && res.ok && !low) items.push({ ok: true, title: t("print.sharp"), sub: t("print.sharpSub", { ppi: res.values.ppi }) });
  else if (res || low) items.push({ ok: false, warn: (res ? res.severity : low.severity) === "warning",
    title: t("print.soft", { n: low ? low.values.n : 0 }), sub: t("print.softDo"), link: bookUrl(B.name, "book") });
  if (by.page_size) items.push({ ok: by.page_size.ok, title: t("print.format", { format: t(`format.${prof.name}`),
    w: num(prof.width / 10, 1), h: num(prof.height / 10, 1) }), sub: t("print.formatSub") });
  if (by.cover) items.push({ ok: true, title: t("print.cover"), sub: t("print.coverSub") });
  for (const c of rep.checks) {
    if (c.ok || !c.id || ["pages", "resolution", "low_dpi", "page_size"].includes(c.id)) continue;
    items.push({ ok: false, warn: c.severity !== "error", title: t("print.technical", { what: t(`check.${c.id}`) }), sub: t("print.technicalSub") });
  }
  return items;
}

function render(B, rep) {
  const items = checkItems(B, rep);
  const names = rep.files.map(f => f.split(/[\\/]/).pop());
  const prof = profileOf(B.plan.book.profile);
  const pagesN = rep.checks.find(c => c.id === "pages")?.values.n ?? "";
  show(el("div", { class: "page" },
    el("div", { class: "print-hero" },
      cover(B),
      el("div", { class: "col grow print-main" },
        el("div", { class: "row" },
          el("span", { class: "big-check" + (rep.ok ? "" : " warn") }, icon(rep.ok ? "check" : "warn", 30, { color: "#fff", width: 2.6 })),
          el("h1", {}, rep.ok ? t("print.ready") : t("print.almost"))),
        el("ul", { class: "checklist" }, items.map(it => el("li", { class: it.ok ? "ok" : it.warn ? "warn" : "bad" },
          icon(it.ok ? "check" : "warn", 24, { width: 2.4 }),
          el("span", { class: "col" }, el("span", { class: "check-title" }, it.title),
            el("span", { class: "muted" }, it.sub, it.link ? [" ", el("a", { href: it.link }, t("print.fix"))] : null))))),
        el("div", { class: "row wrap" },
          el("a", { class: "btn primary big", href: bookApi(B.name, "pdf?final=1"), target: "_blank" }, icon("file", 22), t("print.open")),
          el("button", { class: "btn big", onclick: () => api("POST", bookApi(B.name, "open-folder")).catch(() => {}) },
            icon("folder", 22), t("print.folder"))))),
    el("section", { class: "card" },
      el("h2", {}, t("print.howTitle")),
      el("ol", { class: "how" },
        el("li", {}, el("strong", {}, t("print.how1")), el("span", { class: "muted" },
          t("print.how1Sub", { format: t(`format.${prof.name}`), w: num(prof.width / 10, 1), h: num(prof.height / 10, 1), n: pagesN }))),
        el("li", {}, el("strong", {}, t("print.how2")), el("span", { class: "muted" },
          t("print.how2Sub", { files: names.map(n => `„${n}“`).join(", ") }))),
        el("li", {}, el("strong", {}, t("print.how3")), el("span", { class: "muted" }, t("print.how3Sub"))))),
    el("details", { class: "card" }, el("summary", {}, t("print.details")), el("pre", {}, rep.report))));
}
