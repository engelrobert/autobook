// autobook browser app — vanilla JS modules, no build step.
// Routes: #/ my books · #/new choose a folder · #/b/<book>/<step> with step = wishes | making |
// selection | book | print | gold (favourites test).

import { G, api, bookApi, bookUrl, loadBook } from "./api.js";
import { keyDialog, updateNotice } from "./appinfo.js";
import { closePopups, el, icon, popup, show, toast } from "./dom.js";
import { LANGS, getLang, initI18n, setLang, t } from "./i18n.js";
import { books } from "./screens/books.js";
import { newBook } from "./screens/newbook.js";
import { wishes } from "./screens/wishes.js";
import { making } from "./screens/making.js";
import { selection } from "./screens/selection.js";
import { bookStep } from "./screens/book.js";
import { printStep } from "./screens/print.js";
import { gold } from "./screens/gold.js";
import { closeLightbox } from "./screens/lightbox.js";

const STEPS = ["photos", "wishes", "selection", "book", "print"];
const STEP_OF = { new: 0, wishes: 1, making: 2, selection: 2, book: 3, print: 4 };
const LEGACY = { setup: "wishes", review: "selection", pages: "book" };  // links from older versions

// Screens register a hook to finish their work (e.g. a pending save) before the page changes.
const beforeLeave = new Set();
export const onLeave = fn => beforeLeave.add(fn);

let routing = Promise.resolve();
window.addEventListener("hashchange", () => { routing = routing.then(route, route); });

async function route() {
  for (const fn of [...beforeLeave]) {
    beforeLeave.delete(fn);
    try { await fn(); } catch (e) { console.error(e); }
  }
  closePopups();
  closeLightbox();
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  window.scrollTo(0, 0);
  if (parts[0] === "b" && parts[1]) {
    let B;
    try {
      B = await loadBook(parts[1]);
    } catch (e) {
      header();
      show(el("div", { class: "page" }, el("h1", {}, t("err.title")), el("p", { class: "lead" }, e.message),
        el("a", { class: "btn", href: "#/" }, t("nav.books"))));
      return;
    }
    const step = LEGACY[parts[2]] || parts[2] || defaultStep(B);
    header(B, step);
    const screens = { wishes, making, selection, book: bookStep, print: printStep, gold };
    return (screens[step] || wishes)(B);
  }
  if (parts[0] === "new") {
    header(null, "new");
    return newBook();
  }
  header();
  return books();
}

export function defaultStep(B) {
  if (B.job && (B.job.kind === "select" || B.job.kind === "scan")) return "making";
  if (!B.plan) return "wishes";
  const s = B.info.state;
  return s === "printed" ? "print" : s === "exported" ? "book" : "selection";
}

function stepAvailable(B, i) {
  if (!B) return false;
  if (i === 0) return true;
  if (i === 1) return !!B.summary;
  return !!B.plan;
}

function stepNav(B, current) {
  const cur = STEP_OF[current] ?? -1;
  return el("nav", { class: "steps", "aria-label": t("nav.steps") }, STEPS.map((key, i) => {
    const label = t(`step.${key}`);
    const done = B ? i < cur || (i === 0 && cur > 0) : false;
    const href = B && i > 0 && stepAvailable(B, i) && i !== cur ? bookUrl(B.name, key) : null;
    const badge = done ? el("span", { class: "step-dot done" }, icon("check", 16, { color: "#fff", width: 3 }))
      : el("span", { class: "step-dot" + (i === cur ? " current" : "") }, String(i + 1));
    const cls = "step" + (i === cur ? " current" : done ? " done" : "") + (href ? "" : " off");
    return href ? el("a", { class: cls, href }, badge, el("span", { class: "step-label" }, label))
      : el("span", { class: cls, "aria-current": i === cur ? "step" : null }, badge, el("span", { class: "step-label" }, label));
  }));
}

function moreMenu(B) {
  const items = B ? [
    { icon: "home", label: t("more.books"), href: "#/" },
    B.exported && { icon: "file", label: t("more.preview"), href: bookApi(B.name, "pdf"), target: "_blank" },
    B.exported && { icon: "folder", label: t("more.folder"),
      onclick: () => api("POST", bookApi(B.name, "open-folder")).catch(() => {}) },
    B.exported && { icon: "swap", label: t("more.relayout"), onclick: () => {
      if (!confirm(t("more.relayoutConfirm"))) return;
      sessionStorage.setItem(`fresh:${B.name}`, "1");
      if (location.hash === bookUrl(B.name, "book")) window.dispatchEvent(new HashChangeEvent("hashchange"));
      else location.hash = bookUrl(B.name, "book");
    } },
    { divider: true },
    { icon: "star", label: t("more.favtest"), href: bookUrl(B.name, "gold") },
  ] : [
    { icon: "plus", label: t("books.new"), href: "#/new" },
  ];
  items.push({ divider: true }, { icon: "key", label: t("key.menu"), onclick: keyDialog },
    G.updates && { icon: "swap", label: t("update.check"), onclick: checkUpdates },
    { note: t("update.version", { v: G.version || "?" }) });
  const btn = el("button", { class: "icon-btn", "aria-label": t("nav.more"), "aria-haspopup": "menu",
    onclick: e => { e.stopPropagation(); popup(btn, items); } }, icon("more", 22));
  return btn;
}

// "Nach Updates suchen": the answer as a note, a failed check with its reason (never "up to date")
async function checkUpdates() {
  toast(t("update.checking"));
  try {
    const st = await api("GET", "/api/update?check=1");
    if (st.error) toast(t("update.checkFailed", { e: st.error }), { ms: 12000 });
    else if (st.available) updateNotice();
    else toast(t("update.upToDate", { v: st.current }));
  } catch (e) {
    toast(t("update.checkFailed", { e: e.message }), { ms: 12000 });
  }
}

export function header(B = null, step = null) {
  const langs = el("div", { class: "lang", role: "group", "aria-label": t("nav.language") },
    Object.keys(LANGS).map(code => el("button", {
      "aria-pressed": String(code === getLang()), title: LANGS[code],
      onclick: async () => {
        if (code === getLang()) return;
        await setLang(code);
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      },
    }, code.toUpperCase())));
  document.getElementById("top").replaceChildren(
    el("a", { class: "logo", href: "#/", "aria-label": t("nav.books") },
      icon("book", 30, { color: "var(--accent)", width: 1.7 }), el("span", {}, "autobook")),
    B || step === "new" ? stepNav(B, step) : el("div", { class: "grow" }),
    langs, moreMenu(B));
  updateNotice();
}

(async () => {
  await initI18n();
  document.title = "autobook";
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  window.addEventListener("beforeunload", e => {
    if (document.body.dataset.unsaved) { e.preventDefault(); e.returnValue = ""; }
  });
})();
