// Small DOM helpers: elements, icons, the view, toasts and pop-up menus. All text goes through
// textContent (never innerHTML), so names of folders and photos cannot inject markup.

export function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else e.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return e;
}

export const view = () => document.getElementById("view");

export function show(...nodes) {
  view().replaceChildren(...nodes.flat().filter(n => n != null && n !== false));
}

// Stroke icons (24×24). Shapes: ["p", d] path, ["c", cx, cy, r] circle, ["r", x, y, w, h, rx] rect.
const ICONS = {
  book: [["p", "M3.5 5.5c0-.8.7-1.5 1.5-1.5h6v16H5c-.8 0-1.5-.7-1.5-1.5z"], ["p", "M20.5 5.5c0-.8-.7-1.5-1.5-1.5h-6v16h6c.8 0 1.5-.7 1.5-1.5z"]],
  check: [["p", "M5 12.5l4.5 4.5L19 7.5"]],
  next: [["p", "M5 12h14M13 6l6 6-6 6"]],
  left: [["p", "M15 6l-6 6 6 6"]],
  right: [["p", "M9 6l6 6-6 6"]],
  plus: [["p", "M12 5v14M5 12h14"]],
  close: [["p", "M6 6l12 12M18 6L6 18"]],
  more: [["c", 5, 12, 1], ["c", 12, 12, 1], ["c", 19, 12, 1]],
  star: [["p", "M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"]],
  bigger: [["p", "M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"]],
  smaller: [["p", "M10 14l-6 6M4 14v6h6M14 10l6-6M20 10V4h-6"]],
  swap: [["p", "M4 8h13l-3-3M20 16H7l3 3"]],
  pencil: [["p", "M4 20h4L19 9l-4-4L4 16z"], ["p", "M14 6l4 4"]],
  folder: [["p", "M3.5 7.5c0-1.1.9-2 2-2h4l2 2h7c1.1 0 2 .9 2 2v8c0 1.1-.9 2-2 2h-13c-1.1 0-2-.9-2-2z"]],
  file: [["p", "M7 3.5h7l4 4V20c0 .3-.2.5-.5.5h-10c-.3 0-.5-.2-.5-.5z"], ["p", "M14 3.5V8h4"]],
  chapter: [["r", 4, 3.5, 16, 17, 1.5], ["p", "M4 15l4.5-4 3.5 3 3-2.5 5 4"]],
  cover: [["p", "M5 4.5c0-.6.4-1 1-1h12c.6 0 1 .4 1 1v15c0 .6-.4 1-1 1H6c-.6 0-1-.4-1-1z"], ["p", "M8 8h8"]],
  move: [["p", "M4 12h14M13 6l6 6-6 6"]],
  home: [["p", "M4 11l8-6.5 8 6.5"], ["p", "M6 10v9.5h12V10"]],
  print: [["p", "M7 9V3.5h10V9"], ["r", 4, 9, 16, 8, 1.5], ["p", "M7 14h10v6.5H7z"]],
  warn: [["p", "M12 4l9 16H3z"], ["p", "M12 10v4M12 17.2v.1"]],
  key: [["c", 8, 15, 4], ["p", "M11 12l8-8M16 7l3 3M14 9l2 2"]],
  palette: [["p", "M12 3.5a8.5 8.5 0 1 0 0 17c1.1 0 1.7-.8 1.7-1.6 0-1-.8-1.4-.8-2.3 0-.8.7-1.4 1.5-1.4h2.1c2.2 0 4-1.8 4-4 0-4.3-3.8-7.7-8.5-7.7z"],
    ["c", 7.5, 11.5, 1.1], ["c", 9.8, 7.6, 1.1], ["c", 14.4, 7.6, 1.1]],
  crop: [["p", "M7 3v14h14"], ["p", "M3 7h14v14"]],
  drag: [["p", "M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"]],
};

const NS = "http://www.w3.org/2000/svg";

export function icon(name, size = 22, { color = "currentColor", width = 2, fill = "none" } = {}) {
  const svg = document.createElementNS(NS, "svg");
  for (const [k, v] of Object.entries({ width: size, height: size, viewBox: "0 0 24 24", fill,
    stroke: color, "stroke-width": width, "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true", class: "icon" })) svg.setAttribute(k, v);
  for (const [kind, ...a] of ICONS[name] || []) {
    const s = document.createElementNS(NS, { p: "path", c: "circle", r: "rect" }[kind]);
    const keys = { p: ["d"], c: ["cx", "cy", "r"], r: ["x", "y", "width", "height", "rx"] }[kind];
    keys.forEach((k, i) => s.setAttribute(k, a[i]));
    svg.append(s);
  }
  return svg;
}

let toastTimer = null;

// A short note at the bottom; with an action (e.g. "Undo") it stays a little longer.
export function toast(text, { action = null, onAction = null, ms = 0 } = {}) {
  const box = document.getElementById("toast");
  clearTimeout(toastTimer);
  box.replaceChildren(el("span", {}, text),
    action ? el("button", { class: "toast-action", onclick: () => { hideToast(); onAction(); } }, action) : null);
  box.hidden = false;
  toastTimer = setTimeout(hideToast, ms || (action ? 8000 : 3500));
}

export function hideToast() {
  document.getElementById("toast").hidden = true;
}

// A pop-up menu under a button; closes on outside click or Esc.
export function popup(anchor, items) {
  closePopups();
  const menu = el("div", { class: "popup", role: "menu" },
    items.filter(Boolean).map(it => it.divider ? el("hr", {}) : it.note ? el("span", { class: "popup-note" }, it.note) :
      el(it.href ? "a" : "button", {
        class: "popup-item" + (it.danger ? " danger" : ""), role: "menuitem", href: it.href,
        target: it.target, onclick: () => { closePopups(); if (it.onclick) it.onclick(); },
      }, it.icon ? icon(it.icon, 20) : null, el("span", {}, it.label))));
  document.body.append(menu);
  const r = anchor.getBoundingClientRect();
  const left = Math.min(r.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 12);
  menu.style.left = `${Math.max(12, left) + window.scrollX}px`;
  menu.style.top = `${r.bottom + 8 + window.scrollY}px`;
  setTimeout(() => document.addEventListener("click", closePopups, { once: true }), 0);
  const first = menu.querySelector(".popup-item");
  if (first) first.focus();
  return menu;
}

export function closePopups() {
  document.querySelectorAll(".popup").forEach(m => m.remove());
}

document.addEventListener("keydown", e => { if (e.key === "Escape") closePopups(); });

export function progressBar(fraction) {
  const bar = el("span", { class: "bar-fill" });
  bar.style.width = `${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%`;
  return el("span", { class: "bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100",
    "aria-valuenow": String(Math.round(fraction * 100)) }, bar);
}
