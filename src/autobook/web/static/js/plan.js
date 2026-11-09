// Changes to the book plan. Every change can be undone and is saved automatically shortly after.

import { api, bookApi } from "./api.js";
import { toast } from "./dom.js";
import { t } from "./i18n.js";

let B = null;
let undo = [];
let timer = null;
let saving = null;
let rerender = () => {};
const listeners = new Set();
export let status = "saved";  // saved | pending | saving | error

export function attach(book, onChange) {
  if (!B || B.name !== book.name) undo = [];
  B = book;
  rerender = onChange || (() => {});
}

export function onStatus(fn) {
  listeners.add(fn);
  fn(status);
  return () => listeners.delete(fn);
}

function setStatus(s) {
  status = s;
  document.body.dataset.unsaved = s === "saved" ? "" : "1";
  listeners.forEach(fn => fn(s));
}

// change("Photo removed", plan => removePhoto(plan, id)) — applies, saves, offers Undo.
export function change(label, fn, { quiet = false } = {}) {
  undo.push(JSON.stringify(B.plan));
  if (undo.length > 60) undo.shift();
  fn(B.plan);
  scheduleSave();
  if (!quiet) toast(label, { action: t("undo"), onAction: undoLast });
  rerender();
}

export function undoLast() {
  const s = undo.pop();
  if (!s) return;
  B.plan = JSON.parse(s);
  scheduleSave();
  toast(t("undone"));
  rerender();
}

function scheduleSave() {
  setStatus("pending");
  clearTimeout(timer);
  timer = setTimeout(save, 600);
}

export async function flush() {
  if (timer) {
    clearTimeout(timer);
    timer = null;
    await save();
  } else if (saving) {
    await saving.catch(() => {});
  }
}

export async function save() {
  timer = null;
  setStatus("saving");
  saving = api("POST", bookApi(B.name, "plan"), B.plan);
  try {
    await saving;
    B.layout_outdated = true;
    B.print_outdated = true;
    B.release = null;
    setStatus("saved");
  } catch (e) {
    console.error(e);
    setStatus("error");
  } finally {
    saving = null;
  }
}

// ── edits (pure functions on the plan) ───────────────────────────────────
export const chapterOf = (plan, id) => plan.chapters.find(c => c.photos.some(p => p.id === id));
export const chapterById = (plan, cid) => plan.chapters.find(c => c.id === cid);
export const photoOf = (plan, id) => chapterOf(plan, id)?.photos.find(p => p.id === id);

export function minimal(book, id, like = null) {
  const m = book.photos[id] || {};
  return { id, path: "", ts: m.taken || "", width: m.w || 0, height: m.h || 0,
    weight: like ? like.weight : 1.0, strong: like ? like.strong : false };
}

function sortByTime(c) {
  c.photos.sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
}

export function removePhoto(plan, id) {
  const c = chapterOf(plan, id);
  if (!c) return;
  c.photos = c.photos.filter(p => p.id !== id);
  c.alternatives = [id, ...c.alternatives.filter(x => x !== id)];
  c.excluded = [...new Set([...(c.excluded || []), id])];  // the photo-count slider leaves it out from now on
  setPageColor(plan, id, null);
  setCrop(plan, id, null);
}

export function toggleBig(plan, id) {
  const p = photoOf(plan, id);
  if (!p) return;
  const big = p.weight > 1;
  p.weight = big ? 1.0 : 1.8;
  p.strong = !big;
}

export function swap(plan, book, id, other) {
  const c = chapterOf(plan, id);
  if (c) {
    const i = c.photos.findIndex(p => p.id === id);
    c.photos[i] = { ...minimal(book, other, c.photos[i]), by_user: true };
    c.alternatives = [id, ...c.alternatives.filter(x => x !== other)];
    const colour = (plan.book.page_colors || {})[id];  // the page keeps its colour
    if (colour) { setPageColor(plan, id, null); setPageColor(plan, other, colour); }
    setCrop(plan, id, null);  // another shot needs its own cut
    return;
  }
  setCrop(plan, id, null);
  for (const ch of plan.chapters) {  // the chapter opener has other shots too
    if (ch.divider.photo === id) ch.divider.photo = other;
  }
  if (plan.book.cover_photo === id) plan.book.cover_photo = other;
}

export function setOpener(plan, book, id) {
  const c = chapterOf(plan, id);
  if (!c) return;
  const old = c.divider.photo;
  c.photos = c.photos.filter(p => p.id !== id);
  if (old) c.photos.push(minimal(book, old));
  sortByTime(c);
  c.divider = { style: "hero", photo: id };
}

export function openerBack(plan, book, cid) {
  const c = chapterById(plan, cid);
  if (!c || !c.divider.photo) return;
  c.photos.push(minimal(book, c.divider.photo));
  sortByTime(c);
  c.divider = { style: "plain", photo: null };
}

export function moveTo(plan, id, cid) {
  const from = chapterOf(plan, id), to = chapterById(plan, cid);
  if (!from || !to || from === to) return;
  const p = from.photos.find(x => x.id === id);
  from.photos = from.photos.filter(x => x.id !== id);
  to.photos.push(p);
  sortByTime(to);
}

export function addPhoto(plan, book, cid, id) {
  const c = chapterById(plan, cid);
  if (!c) return;
  c.alternatives = c.alternatives.filter(x => x !== id);
  c.excluded = (c.excluded || []).filter(x => x !== id);
  c.photos.push({ ...minimal(book, id), by_user: true });
  sortByTime(c);
}

// Page design: the same frame on every page, a colour for the book and for single pages.
// A page's own colour is stored under its first photo (or chapter:<id> for a plain opener).
export function setFrame(plan, frame) {
  plan.book.frame = frame;
}

export function setBackground(plan, colour) {
  plan.book.background = colour;
}

export function setPageColor(plan, key, colour) {
  const colours = { ...(plan.book.page_colors || {}) };
  if (colour) colours[key] = colour;
  else delete colours[key];
  plan.book.page_colors = colours;
}

// The printer and format (a profile name). Same page size: the pages stay; another one: laid out anew.
export function setProfile(plan, name) {
  plan.book.profile = name;
}

// The cover as the printer's cover calculator gives it ({binding, width_mm, height_mm, spine_mm, margin_mm})
export function setCoverSize(plan, size) {
  plan.book.cover_size = size;
}

// Where a trimmed photo is cut: [x, y], 0 keeps the left/top edge, 1 the right/bottom one; null = centred
export function setCrop(plan, id, xy) {
  const crops = { ...(plan.book.crops || {}) };
  if (xy) crops[id] = xy;
  else delete crops[id];
  plan.book.crops = crops;
}

// Which photos share a page: once you move a photo, the chapter keeps your split (photo ids per page)
export function setPages(plan, cid, pages) {
  const c = chapterById(plan, cid);
  if (c) c.pages = pages ? pages.filter(pg => pg.length) : null;
}

// Move a photo onto another page of its chapter. split: the chapter's pages as shown, to: their index.
export function moveToPage(plan, id, split, to) {
  const c = chapterOf(plan, id);
  if (!c || to < 0 || to >= split.length) return;
  const ts = pid => photoOf(plan, pid)?.ts || "";
  const pages = split.map(pg => pg.filter(x => x !== id));
  pages[to] = [...pages[to], id].sort((a, b) => (ts(a) < ts(b) ? -1 : ts(a) > ts(b) ? 1 : 0));
  setPages(plan, c.id, pages);
}

// normal | page (a page to itself) | spread (across both pages of a spread)
export function setLayout(plan, id, layout) {
  const p = photoOf(plan, id);
  if (p) p.layout = layout;
}

// Chapters in your own order: move one to index `to` (clamped), or put them back in date order
export function moveChapter(plan, cid, to) {
  const from = plan.chapters.findIndex(c => c.id === cid);
  if (from < 0) return;
  const [c] = plan.chapters.splice(from, 1);
  plan.chapters.splice(Math.max(0, Math.min(plan.chapters.length, to)), 0, c);
}

const byDates = (a, b) => (a.date_from < b.date_from ? -1 : a.date_from > b.date_from ? 1
  : a.date_to < b.date_to ? -1 : a.date_to > b.date_to ? 1 : 0);

export function byDate(plan) {
  plan.chapters = plan.chapters.map((c, i) => [c, i]).sort(([a, i], [b, j]) => byDates(a, b) || i - j).map(([c]) => c);
}

export const inDateOrder = plan => plan.chapters.every((c, i) => i === 0 || byDates(plan.chapters[i - 1], c) <= 0);

export function photoCount(plan) {
  return plan.chapters.reduce((n, c) => n + c.photos.length + (c.divider.photo ? 1 : 0), 0)
    + (plan.book.cover_photo ? 1 : 0);
}
