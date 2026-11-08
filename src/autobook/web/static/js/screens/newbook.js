// New book, step 1: choose the folder with the photos, name the book, let autobook look at them.

import { api, bookUrl, enc, poll } from "../api.js";
import { el, icon, progressBar, show } from "../dom.js";
import { dateRange, num, t } from "../i18n.js";

let selected = null;

const KEY = "autobook.newbook.path";

function remember(path) {
  try { sessionStorage.setItem(KEY, path || ""); } catch { /* fine */ }
}

export async function newBook() {
  let path = null;
  try { path = sessionStorage.getItem(KEY) || null; } catch { /* fine */ }
  selected = null;
  return render(path);
}

async function render(path) {
  let fs;
  try {
    fs = await api("GET", "/api/fs" + (path ? `?path=${enc(path)}` : ""));
  } catch {
    fs = await api("GET", "/api/fs");
  }
  remember(fs.path);
  const head = el("div", { class: "page-head" },
    el("h1", {}, t("new.title")), el("p", { class: "lead" }, t("new.lead")));

  if (!fs.path) {  // starting places: home folder, Pictures, drives
    const tiles = fs.roots.map((r, i) => el("button", { class: "root-tile", onclick: () => render(r.path) },
      icon(i === 0 ? "home" : "folder", 34, { width: 1.6 }),
      el("span", { class: "folder-name" }, i === 0 ? t("new.home") : r.path.split(/[\\/]/).filter(Boolean).pop() || r.path),
      el("span", { class: "muted small" }, r.path)));
    show(el("div", { class: "page" }, head, el("h2", { class: "label" }, t("new.start")), el("div", { class: "root-grid" }, tiles)));
    return;
  }

  const here = fs.photos_here + fs.dirs.reduce((n, d) => n + d.photos, 0);
  const loc = el("div", { class: "loc" },
    el("button", { class: "btn ghost", onclick: () => render(fs.parent && fs.parent !== fs.path ? fs.parent : null) },
      icon("left", 20), t("new.back")),
    el("span", { class: "muted" }, t("new.youAreIn")),
    el("span", { class: "loc-chip" }, icon("folder", 20, { width: 1.8 }), fs.label || fs.name),
    here ? el("button", { class: "btn ghost", onclick: () => pick({ path: fs.path, label: fs.label || fs.name, photos: here,
      date_from: null, book: fs.book }) }, t("new.takeThis")) : null);

  const tiles = fs.dirs.map(tile);
  const grid = tiles.length ? el("div", { class: "folder-grid" }, tiles)
    : el("p", { class: "empty-note" }, fs.photos_here ? t("new.onlyPhotosHere") : t("new.emptyFolder"));
  const bar = el("div", { id: "pick-bar" });
  show(el("div", { class: "page" }, head, loc, grid, bar));
  window.scrollTo(0, 0);  // a folder just opened: start at its top, not where the last one was scrolled to
  drawBar();

  function tile(d) {
    const picked = selected && selected.path === d.path;
    const mosaic = d.previews.length
      ? el("div", { class: `mosaic n${d.previews.length}` },
        d.previews.map(p => el("img", { src: `/api/fs/preview?path=${enc(p)}`, alt: "", loading: "lazy" })))
      : el("div", { class: "mosaic none" }, icon("folder", 44, { width: 1.4 }));
    const count = d.photos ? t(d.more ? "new.photosMore" : "new.photos", { n: d.photos, nf: num(d.photos) }) : t("new.noPhotos");
    const meta = [dateRange(d.date_from, d.date_to), count,
      d.subfolders ? t("new.subfolders", { n: d.subfolders }) : null].filter(Boolean).join(" · ");
    return el("div", { class: "folder-tile" + (picked ? " picked" : "") },
      el("button", { class: "folder-pick", "aria-pressed": String(!!picked), disabled: !d.photos,
        onclick: () => pick(d), ondblclick: () => d.subfolders && render(d.path) },
      mosaic,
      el("span", { class: "folder-name" }, d.label),
      el("span", { class: "muted small" }, meta),
      d.favourites ? el("span", { class: "pill gold" }, icon("star", 15, { fill: "var(--gold)", color: "var(--gold)", width: 1.5 }),
        t("new.withFavourites")) : null,
      d.book ? el("span", { class: "pill accent" }, icon("book", 15, { width: 1.8 }), t("new.isBook", { name: d.book })) : null,
      picked ? el("span", { class: "picked-mark" }, icon("check", 20, { color: "#fff", width: 3 })) : null),
      d.subfolders ? el("button", { class: "btn small ghost open-btn", onclick: () => render(d.path) },
        t("new.open"), icon("right", 18)) : null);
  }

  function pick(d) {
    selected = d;
    grid.querySelectorAll(".folder-tile").forEach((x, i) => {
      if (fs.dirs[i]) x.replaceWith(tile(fs.dirs[i]));
    });
    drawBar();
    document.getElementById("pick-bar").scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function drawBar() {
    if (!selected) {
      bar.replaceChildren();
      return;
    }
    if (selected.book && !selected.again) {  // this folder already has a book: open it (a second one on request)
      bar.replaceChildren(el("div", { class: "action-bar" },
        el("div", { class: "grow col" },
          el("span", { class: "muted small" }, t("new.selected")),
          el("span", { class: "serif h3" }, selected.label),
          el("span", { class: "muted small" }, t("new.alreadyBook", { name: selected.book }), " ",
            el("button", { class: "link", onclick: () => { selected.again = true; drawBar(); } }, t("new.secondBook")))),
        el("a", { class: "btn primary big", href: bookUrl(selected.book, "selection"), onclick: () => remember("") },
          t("new.openBook", { name: selected.book }), icon("next", 22))));
      return;
    }
    const name = el("input", { type: "text", value: selected.label, maxlength: "80", "aria-label": t("new.bookName") });
    const go = el("button", { class: "btn primary big", onclick: () => create(name.value.trim() || selected.label) },
      t("new.go"), icon("next", 22));
    name.addEventListener("keydown", e => { if (e.key === "Enter") go.click(); });
    bar.replaceChildren(el("div", { class: "action-bar" },
      el("div", { class: "grow col" },
        el("span", { class: "muted small" }, t("new.selected")),
        el("span", { class: "serif h3" }, selected.label),
        el("span", { class: "muted small" }, t("new.selectedMeta", { n: selected.photos, nf: num(selected.photos) }))),
      el("label", { class: "field grow" }, el("span", { class: "muted small" }, t("new.bookName")), name),
      go));
  }

  async function create(name) {
    let res;
    try {
      res = await api("POST", "/api/books", { folder: selected.path, name, again: !!selected.again });
    } catch (e) {
      if (e.status === 409 && e.data && e.data.book) {  // found out only now (e.g. picked in another tab)
        selected.book = e.data.book;
        drawBar();
        return;
      }
      bar.replaceChildren(el("p", { class: "error" }, t("new.failed", { msg: e.message })));
      return;
    }
    const line = el("span", { class: "muted" }, t("new.scanning"));
    const holder = el("div", { class: "grow" }, progressBar(0));
    bar.replaceChildren(el("div", { class: "action-bar" },
      el("div", { class: "grow col" }, el("span", { class: "serif h3" }, t("new.looking", { name: selected.label })), line, holder)));
    try {
      await poll(res.job, j => {
        if (j.total) {
          line.textContent = t("new.scanProgress", { done: num(j.done), total: num(j.total) });
          holder.replaceChildren(progressBar(j.done / j.total));
        }
      });
    } catch (e) {
      bar.replaceChildren(el("p", { class: "error" }, t("new.failed", { msg: e.message })));
      return;
    }
    remember("");
    selected = null;
    location.hash = bookUrl(res.book, "wishes");
  }
}
