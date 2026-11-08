// The app itself: the OpenAI key (entered here, kept in the computer's password store) and
// updates of the installed app (a notice, one click, autobook restarts into the new version).

import { G, api, poll } from "./api.js";
import { el, icon, progressBar, toast } from "./dom.js";
import { t } from "./i18n.js";

const KEYS_URL = "https://platform.openai.com/api-keys";

export function keyDialog() {
  document.querySelector(".modal-back")?.remove();
  const input = el("input", { type: "password", class: "key-input", placeholder: "sk-…", autocomplete: "off",
    "aria-label": t("key.label") });
  const status = el("p", { class: "muted" });
  const msg = el("p", { class: "key-msg", role: "status" });
  const remove = el("button", { class: "btn ghost", hidden: true }, t("key.remove"));
  const save = el("button", { class: "btn primary" }, icon("check", 20), t("key.save"));
  const close = () => back.remove();
  const back = el("div", { class: "modal-back", onclick: e => { if (e.target === back) close(); } },
    el("div", { class: "modal card", role: "dialog", "aria-label": t("key.title") },
      el("h2", {}, t("key.title")),
      el("p", {}, t("key.lead"), " ", el("a", { href: KEYS_URL, target: "_blank", rel: "noopener" }, t("key.where"))),
      status,
      el("label", { class: "col" }, el("span", { class: "muted small" }, t("key.label")), input),
      msg,
      el("div", { class: "row between" }, remove,
        el("div", { class: "row" }, el("button", { class: "btn ghost", onclick: close }, t("key.close")), save))));
  const show = s => {
    status.textContent = s.set ? (s.from_env ? t("key.fromEnv") : t("key.isSet")) : t("key.notSet");
    remove.hidden = !s.set || s.from_env;
    if (G.ai_available !== s.ai_available) {  // screens that offer the AI follow at once
      G.ai_available = s.ai_available;
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    }
  };
  save.addEventListener("click", async () => {
    const key = input.value.trim();
    if (!key) { input.focus(); return; }
    save.disabled = true;
    msg.className = "key-msg";
    msg.textContent = t("key.checking");
    try {
      const r = await api("POST", "/api/key", { key });
      input.value = "";
      msg.textContent = r.checked ? t("key.saved") : t("key.savedUnchecked");
      msg.classList.add("ok");
      show({ set: true, from_env: false, ai_available: r.ai_available });
    } catch (e) {
      msg.textContent = String(e.message).includes("did not accept") ? t("key.invalid") : t("key.badFormat");
      msg.classList.add("error");
    } finally {
      save.disabled = false;
    }
  });
  remove.addEventListener("click", async () => {
    if (!confirm(t("key.removeConfirm"))) return;
    const r = await api("DELETE", "/api/key");
    msg.textContent = t("key.removed");
    show({ set: false, from_env: false, ai_available: r.ai_available });
  });
  input.addEventListener("keydown", e => { if (e.key === "Enter") save.click(); if (e.key === "Escape") close(); });
  document.body.append(back);
  input.focus();
  api("GET", "/api/key").then(show).catch(() => {});
}

// "Version X ist da – jetzt aktualisieren" under the header (installed app only)
let shownFor = null;
let waiting = false;
export async function updateNotice() {
  if (!G.updates || waiting) return;
  let st;
  waiting = true;
  try {  // the check runs in the background at start: ask again until it has finished (up to a minute)
    for (let i = 0; i < 20; i++) {
      st = await api("GET", "/api/update");
      if (st.checked) break;
      await new Promise(r => setTimeout(r, 3000));
    }
  } catch {
    return;
  } finally {
    waiting = false;
  }
  if (!st.available || shownFor === st.version || document.querySelector(".update-bar")) return;
  shownFor = st.version;
  const body = el("span", { class: "grow" }, t("update.available", { v: st.version }));
  const go = el("button", { class: "btn primary" }, icon("next", 20), t("update.now"));
  const bar = el("div", { class: "update-bar", role: "status" }, icon("star", 22), body, go);
  document.getElementById("top").after(bar);
  go.addEventListener("click", async () => {
    go.disabled = true;
    let started;
    try {
      started = await api("POST", "/api/update/apply");
    } catch (e) {
      go.disabled = false;
      toast(String(e.message).includes("busy") ? t("update.busy") : t("update.failed"));
      return;
    }
    const meter = el("div", { class: "grow" }, progressBar(0.02));
    body.replaceWith(el("span", { class: "grow" }, t("update.downloading")), meter);
    go.remove();
    try {
      await poll(started.job, j => { if (j.total) meter.replaceChildren(progressBar(j.done / j.total)); });
    } catch {
      toast(t("update.failed"));
      return;
    }
    bar.replaceChildren(el("span", { class: "spinner" }), el("span", { class: "grow" }, t("update.restarting")));
    await waitForRestart();
    location.reload();
  });
}

// The old autobook stops, the new one starts at the same address: wait until it answers again.
async function waitForRestart() {
  const ping = () => fetch("/api/ping", { cache: "no-store" }).then(r => r.ok).catch(() => false);
  const until = Date.now() + 120_000;
  let wentAway = false;
  while (Date.now() < until) {
    const up = await ping();
    if (!up) wentAway = true;
    else if (wentAway) return;
    await new Promise(r => setTimeout(r, 1000));
  }
}
