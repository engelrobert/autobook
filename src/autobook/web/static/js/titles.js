// "Titel neu vorschlagen": other titles and subtitles from the AI, in a tone of your choice, three to pick
// from for the book and every chapter (or for one chapter). Nothing changes until "Übernehmen"; that is
// one normal plan change, so it can be undone.

import { G, bookApi, runJob } from "./api.js";
import { keyDialog } from "./appinfo.js";
import { el, icon } from "./dom.js";
import { t } from "./i18n.js";
import * as P from "./plan.js";

const TONES = ["plain", "warm", "playful", "poetic"];

export function titlesDialog(B, chapterId = null) {
  if (!G.ai_available) {
    keyDialog();
    return;
  }
  document.querySelector(".modal-back")?.remove();
  let tone = (B.brief && B.brief.title_tone) || "warm";
  let fresh = 0, result = null;
  const picks = {};  // "book" or chapter id -> "keep" | "folder" | option index
  const subs = el("input", { type: "checkbox", checked: true });
  const seg = el("div", { class: "seg", role: "radiogroup", "aria-label": t("titles.tone") });
  const drawSeg = () => seg.replaceChildren(...TONES.map(v => el("button", { class: "seg-btn" + (v === tone ? " on" : ""),
    role: "radio", "aria-checked": String(v === tone), onclick: () => { tone = v; drawSeg(); } }, t(`titles.tone.${v}`))));
  drawSeg();
  const out = el("div", { class: "col title-picks" });
  const msg = el("p", { class: "key-msg", role: "status" });
  const ask = el("button", { class: "btn primary", onclick: () => fetchSuggestions() }, icon("star", 20), t("titles.ask"));
  const apply = el("button", { class: "btn primary", hidden: true, onclick: () => applyPicks() }, icon("check", 20), t("titles.apply"));
  const again = el("button", { class: "btn ghost", hidden: true, onclick: () => { fresh += 1; fetchSuggestions(); } },
    t("titles.again"));
  const close = () => back.remove();
  const chapter = chapterId ? P.chapterById(B.plan, chapterId) : null;
  const back = el("div", { class: "modal-back", onclick: e => { if (e.target === back) close(); } },
    el("div", { class: "modal card wide-modal", role: "dialog", "aria-label": t("titles.title") },
      el("h2", {}, chapter ? t("titles.titleChapter", { name: chapter.title }) : t("titles.title")),
      el("p", { class: "muted" }, t("titles.lead")),
      el("div", { class: "row" }, el("span", { class: "check-title" }, t("titles.tone")), seg),
      el("label", { class: "setting" }, subs, el("span", {}, t("titles.withSubtitles"))),
      out, msg,
      el("div", { class: "row between" }, again,
        el("div", { class: "row" }, el("button", { class: "btn ghost", onclick: close }, t("key.close")), ask, apply))));
  document.body.append(back);

  async function fetchSuggestions() {
    ask.disabled = again.disabled = true;
    msg.className = "key-msg";
    msg.textContent = "";
    out.replaceChildren(el("div", { class: "loading" }, el("span", { class: "spinner" }), t("titles.working")));
    try {
      result = await runJob(bookApi(B.name, "titles"), { tone, subtitles: subs.checked, chapter: chapterId, fresh });
    } catch (e) {
      out.replaceChildren();
      msg.textContent = t("titles.failed", { msg: e.message });
      msg.classList.add("error");
      ask.disabled = again.disabled = false;
      return;
    }
    ask.disabled = again.disabled = false;
    Object.keys(picks).forEach(k => delete picks[k]);  // new suggestions: start again from "keep"
    ask.hidden = true;
    apply.hidden = again.hidden = false;
    drawPicks();
  }

  function item(key, label, current, options, folder) {
    picks[key] = picks[key] ?? "keep";
    const name = `pick-${key}`;
    const choice = (value, title, sub, note) => el("label", { class: "title-choice" + (picks[key] === value ? " on" : "") },
      el("input", { type: "radio", name, checked: picks[key] === value, onchange: () => { picks[key] = value; drawPicks(); } }),
      el("span", { class: "col" }, el("strong", {}, title), sub ? el("span", { class: "muted small" }, sub) : null,
        note ? el("span", { class: "muted small" }, note) : null));
    return el("fieldset", { class: "title-item" }, el("legend", { class: "muted small" }, label),
      choice("keep", current.title, current.subtitle, t("titles.keep")),
      options.map((o, i) => choice(i, o.title, o.subtitle)),
      folder && folder !== current.title ? choice("folder", folder, null, t("titles.folderName")) : null);
  }

  function drawPicks() {
    const parts = [];
    if (result.book && result.book.length) {
      parts.push(item("book", t("titles.book"), { title: B.plan.book.title, subtitle: B.plan.book.subtitle }, result.book));
    }
    for (const c of B.plan.chapters) {
      const opts = result.chapters[c.id];
      if (!opts && !result.folders[c.id]) continue;
      parts.push(item(c.id, t("titles.chapter"), { title: c.title, subtitle: c.subtitle }, opts || [], result.folders[c.id]));
    }
    out.replaceChildren(...(parts.length ? parts : [el("p", { class: "muted" }, t("titles.none"))]));
  }

  function chosen(key, options, folder) {
    const p = picks[key];
    if (p === "keep" || p === undefined) return null;
    if (p === "folder") return { title: folder, subtitle: null };
    const o = options[p];
    return { title: o.title, subtitle: result.subtitles ? o.subtitle : null };  // without subtitles: keep yours
  }

  function applyPicks() {
    const changes = [];
    if (result.book) changes.push(["book", chosen("book", result.book)]);
    for (const c of B.plan.chapters) changes.push([c.id, chosen(c.id, result.chapters[c.id] || [], result.folders[c.id])]);
    const todo = changes.filter(([, v]) => v);
    close();
    if (!todo.length) return;
    P.change(t("titles.applied", { n: todo.length }), plan => {
      for (const [key, v] of todo) {
        const target = key === "book" ? plan.book : P.chapterById(plan, key);
        if (!target) continue;
        target.title = v.title;
        if (v.subtitle !== null) target.subtitle = v.subtitle;
      }
    });
  }
}
