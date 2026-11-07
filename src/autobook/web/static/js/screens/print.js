// Step 5: the print file. Made by itself when it is missing or out of date; the printer's checks
// are shown as a short checklist in plain words, the technical report folded away. For a printer
// like epubli: the inner pages and the cover as files of their own, the cover at the size its
// cover calculator gives (typed in here), and the steps of ordering there.

import { G, api, bookApi, bookUrl, poll, profileOf, runJob } from "../api.js";
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
  const cs = by.cover_size;
  if (cs && cs.values.typed) items.push({ ok: cs.ok, title: t("print.coverTyped", { w: num(cs.values.w / 10, 1),
    h: num(cs.values.h / 10, 1), spine: num(cs.values.spine, 1), binding: t(`binding.${cs.values.binding}`) }),
    sub: t(cs.ok ? "print.coverTypedSub" : "print.coverMismatch") });
  else if (cs) items.push({ ok: false, title: t("print.coverMissing"), sub: t("print.coverMissingSub"),
    action: () => document.getElementById("cover-size")?.scrollIntoView({ behavior: "smooth" }) });
  const fs = by.file_size;
  if (fs && prof.max_file_mb && prof.max_file_mb < 1000) items.push(fs.ok
    ? { ok: true, title: t("print.fileSize", { mb: num(fs.values.mb, 0), max: num(fs.values.max, 0) }), sub: t("print.fileSizeSub") }
    : { ok: false, title: t("print.fileTooLarge", { mb: num(fs.values.mb, 0), max: num(fs.values.max, 0) }),
      sub: t("print.fileTooLargeDo"), link: bookUrl(B.name, "selection") });
  for (const c of rep.checks) {
    if (c.ok || !c.id || ["pages", "resolution", "low_dpi", "page_size", "cover_size", "file_size"].includes(c.id)) continue;
    items.push({ ok: false, warn: c.severity !== "error", title: t("print.technical", { what: t(`check.${c.id}`) }), sub: t("print.technicalSub") });
  }
  return items;
}

// The cover's size from the printer's cover calculator: it depends on binding, paper and page count
function coverCard(B, prof, pagesN) {
  const cs = B.plan.book.cover_size;
  let binding = cs ? cs.binding : prof.bindings[0];
  const field = (label, value, hint) => {
    const input = el("input", { type: "number", min: "0", step: "0.1", inputmode: "decimal", class: "num-input",
      value: value == null ? "" : String(value) });
    return [input, el("label", { class: "field" }, el("span", { class: "check-title" }, label), input,
      hint ? el("span", { class: "muted small" }, hint) : null)];
  };
  const [w, wField] = field(t("cover.width"), cs?.width_mm, t("cover.widthHint"));
  const [h, hField] = field(t("cover.height"), cs?.height_mm);
  const [spine, spineField] = field(t("cover.spine"), cs?.spine_mm);
  const [margin, marginField] = field(t("cover.margin"), cs ? cs.margin_mm : prof.margins[binding], t("cover.marginHint"));
  const msg = el("p", { class: "key-msg", role: "status" });
  const seg = el("div", { class: "seg", role: "radiogroup", "aria-label": t("cover.binding") });
  const drawSeg = () => seg.replaceChildren(...prof.bindings.map(b => el("button", { class: "seg-btn" + (b === binding ? " on" : ""),
    role: "radio", "aria-checked": String(b === binding), onclick: () => {
      if (Number(margin.value) === prof.margins[binding]) margin.value = String(prof.margins[b]);  // untouched: follows
      binding = b;
      drawSeg();
    } }, t(`binding.${b}`))));
  drawSeg();
  const save = el("button", { class: "btn primary", onclick: () => {
    const size = { binding, width_mm: Number(w.value), height_mm: Number(h.value), spine_mm: Number(spine.value),
      margin_mm: Number(margin.value) };
    const room = size.width_mm - 2 * size.margin_mm - size.spine_mm > 2 * prof.width * 0.9 && size.height_mm - 2 * size.margin_mm > prof.height * 0.9;
    if (![size.width_mm, size.height_mm, size.margin_mm].every(v => v > 0) || !(size.spine_mm >= 0) || !room) {
      msg.textContent = t("cover.bad", { w: num(2 * prof.width / 10, 1), h: num(prof.height / 10, 1) });
      msg.className = "key-msg error";
      return;
    }
    P.change(t("cover.saved"), plan => P.setCoverSize(plan, size));
    printStep(B);
  } }, icon("check", 20), t(cs ? "cover.update" : "cover.save"));
  return el("section", { class: "card q", id: "cover-size" },
    el("h2", {}, t("cover.title", { printer: t(`printer.${prof.printer}`) })),
    el("p", { class: "q-lead" }, t("cover.lead")),
    el("ol", { class: "how" },
      el("li", {}, el("strong", {}, el("a", { href: prof.calculator, target: "_blank", rel: "noopener" }, t("cover.calc"))),
        el("span", { class: "muted" }, t("cover.calcSub", { format: t(`format.${prof.name}`), n: pagesN }))),
      el("li", {}, el("strong", {}, t("cover.type")), el("span", { class: "muted" }, t("cover.typeSub")))),
    el("div", { class: "row" }, el("span", { class: "check-title" }, t("cover.binding")), seg),
    el("div", { class: "cover-fields" }, wField, hField, spineField, marginField),
    msg, el("div", { class: "row" }, save));
}

// Another printer or format: the same page size keeps the pages, another size lays the book out anew
function printerCard(B, prof) {
  const choices = [...G.profiles].sort((a, b) => (a.printer === prof.printer ? 0 : 1) - (b.printer === prof.printer ? 0 : 1));
  const pick = el("select", { "aria-label": t("print.printerLabel") }, choices.map(p =>
    el("option", { value: p.name, selected: p.name === prof.name },
      `${t(`printer.${p.printer || "other"}`)}: ${t(`format.${p.name}`)}, ${num(p.width / 10, 1)} × ${num(p.height / 10, 1)} cm`)));
  const note = el("p", { class: "muted small" });
  const go = el("button", { class: "btn", disabled: true }, t("print.printerGo"));
  pick.addEventListener("change", () => {
    const to = profileOf(pick.value);
    go.disabled = to.name === prof.name;
    note.textContent = to.name === prof.name ? "" : t(to.width === prof.width && to.height === prof.height ? "print.printerSame" : "print.printerNew");
  });
  go.addEventListener("click", () => {
    P.change(t("print.printerChanged"), plan => P.setProfile(plan, pick.value));
    printStep(B);
  });
  return el("details", { class: "card" }, el("summary", {}, t("print.printerTitle")),
    el("div", { class: "q" }, el("p", { class: "q-lead" }, t("print.printerLead")), el("div", { class: "row" }, pick, go), note));
}

function fileButtons(B, rep, prof) {
  const folder = el("button", { class: "btn big", onclick: () => api("POST", bookApi(B.name, "open-folder")).catch(() => {}) },
    icon("folder", 22), t("print.folder"));
  if (!prof.separate_files) {
    return [el("a", { class: "btn primary big", href: bookApi(B.name, "pdf?final=1"), target: "_blank" }, icon("file", 22), t("print.open")), folder];
  }
  const names = rep.files.map(f => f.split(/[\\/]/).pop());
  const cover = names.findIndex(n => n.endsWith(".jpg")) >= 0 ? names.findIndex(n => n.endsWith(".jpg")) : 1;
  return [
    el("a", { class: "btn primary big", href: bookApi(B.name, "print-file/0"), target: "_blank" }, icon("file", 22), t("print.openInner")),
    names.length > 1 ? el("a", { class: "btn big", href: bookApi(B.name, `print-file/${cover}`), target: "_blank" }, icon("cover", 22), t("print.openCover")) : null,
    folder];
}

function howSteps(B, rep, prof, pagesN) {
  const names = rep.files.map(f => f.split(/[\\/]/).pop());
  const format = t(`format.${prof.name}`), w = num(prof.width / 10, 1), h = num(prof.height / 10, 1);
  if (prof.printer === "epubli") {
    const binding = B.plan.book.cover_size ? t(`binding.${B.plan.book.cover_size.binding}`) : t("binding.hardcover");
    const cover = names.find(n => n.endsWith(".jpg")) || names[1] || "";
    return [
      [t("print.epubli1"), t("print.epubli1Sub", { format, w, h, binding, n: pagesN })],
      [t("print.epubli2"), t("print.epubli2Sub", { file: names[0] || "" })],
      [t("print.epubli3"), t("print.epubli3Sub", { file: cover })],
      [t("print.epubli4"), t("print.epubli4Sub")]];
  }
  return [
    [t("print.how1"), t("print.how1Sub", { format, w, h, n: pagesN })],
    [t("print.how2"), t("print.how2Sub", { files: names.map(n => `„${n}“`).join(", ") })],
    [t("print.how3"), t("print.how3Sub")]];
}

function render(B, rep) {
  const items = checkItems(B, rep);
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
            el("span", { class: "muted" }, it.sub, it.link ? [" ", el("a", { href: it.link }, t("print.fix"))] : null,
              it.action ? [" ", el("button", { class: "link", onclick: it.action }, t("print.fix"))] : null))))),
        el("div", { class: "row wrap" }, fileButtons(B, rep, prof)))),
    prof.cover_typed ? coverCard(B, prof, pagesN) : null,
    el("section", { class: "card" },
      el("h2", {}, prof.printer ? t("print.howTitleAt", { printer: t(`printer.${prof.printer}`) }) : t("print.howTitle")),
      el("ol", { class: "how" }, howSteps(B, rep, prof, pagesN).map(([title, sub]) =>
        el("li", {}, el("strong", {}, title), el("span", { class: "muted" }, sub))))),
    printerCard(B, prof),
    el("details", { class: "card" }, el("summary", {}, t("print.details")), el("pre", {}, rep.report))));
}
