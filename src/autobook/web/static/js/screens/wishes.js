// Step 2: what the scan found, the questions as large choice cards, book shape and thickness,
// further settings folded away, and the start of the AI run with its cost and time.

import { keyDialog } from "../appinfo.js";
import { G, api, bookApi, bookUrl, profileOf, runJob, thumbUrl } from "../api.js";
import { el, icon, show } from "../dom.js";
import { dateRange, getLang, num, shortDay, t } from "../i18n.js";

const SKIP = new Set(["target_photos"]);  // answered by the thickness slider
const EFFORTS = ["low", "medium", "high", "max"];

export function costText(usd) {
  if (usd < 0.01) return t("cost.lessThanCent");
  if (usd < 1) return t("cost.cents", { n: Math.max(1, Math.round(usd * 100)) });
  return t("cost.dollars", { n: num(usd, 2) });
}

// "Family first: mostly photos of you" → ["Family first", "mostly photos of you"];
// "Highlights (about 75 photos)" → ["Highlights", "about 75 photos"].
function splitLabel(label) {
  const arrow = label.split(" → ");
  if (arrow.length === 2) return [arrow[0], `→ ${arrow[1]}`];
  const i = label.indexOf(": ");
  if (i > 0) return [label.slice(0, i), label.slice(i + 2)];
  const m = label.match(/^(.*) \(([^()]*)\)$/);
  return m ? [m[1], m[2]] : [label, ""];
}

function loading(text) {
  return el("div", { class: "page" }, el("div", { class: "loading" }, el("span", { class: "spinner" }), text));
}

export async function wishes(B) {
  if (!B.summary) {
    if (B.job) location.hash = bookUrl(B.name, "making");
    else show(el("div", { class: "page" }, el("h1", {}, t("wishes.notScanned")), el("a", { class: "btn", href: "#/new" }, t("books.new"))));
    return;
  }
  const meta = B.questions_meta || {};
  if (!B.questions || (!meta.ai && meta.lang !== getLang())) {
    show(loading(t("wishes.preparing")));
    B.questions = await runJob(bookApi(B.name, "questions"), { ai: false, ui_language: getLang() });
    B.questions_meta = { lang: getLang(), ai: false };
  }
  render(B);
}

function render(B) {
  const brief = B.brief || {};
  const saved = Object.fromEntries((brief.qa || []).map(a => [a.id, a.answer]));
  const S = {
    answers: Object.fromEntries(B.questions.map(q => [q.id, q.id in saved ? saved[q.id] : q.default])),
    profile: brief.profile || "square-21",
    perPage: brief.photos_per_page || 4.5,
    photos: brief.target_from_user ? brief.target_photos : null,  // null: follow the suggestion
    subtitles: !!brief.subtitles,
    ai: G.ai_available && brief.use_ai !== false,
    effort: brief.effort || "high",
  };
  const summary = B.summary;
  const chapters = Math.max(1, summary.days || 1);
  const unique = (summary.events || []).reduce((n, e) => n + (e.unique ?? e.n), 0) || summary.photos;
  const sampleCover = B.info.cover;
  let estimateTimer = null;
  const ready = el("section", { class: "ready" });

  const qOf = mapsTo => B.questions.find(q => q.maps_to === mapsTo);
  const favMode = () => (qOf("favourites_mode") ? S.answers[qOf("favourites_mode").id] : "ignore");

  function suggested() {
    const sg = B.suggest || {};
    const mode = favMode(), favs = sg.favourites || 0;
    if (mode === "only") return favs;
    if (mode === "fill") return Math.max(sg.highlights || 0, Math.round(favs * 1.15));
    if (mode === "best") return sg.highlights || 0;
    const scope = qOf("scope");
    return scope && S.answers[scope.id] === "complete" ? sg.complete || 0 : sg.highlights || 0;
  }
  const photos = () => (favMode() === "only" ? suggested() : S.photos ?? suggested());
  const pages = () => Math.round(photos() / S.perPage) + chapters;

  function changed({ redraw = true } = {}) {
    if (redraw) draw();
    clearTimeout(estimateTimer);
    estimateTimer = setTimeout(refreshEstimate, 350);
  }

  function choices(q, { pills = false } = {}) {
    const multi = q.kind === "multi";
    const isOn = v => (multi ? (S.answers[q.id] || []).includes(v) : S.answers[q.id] === v);
    return el("div", { class: pills ? "pills" : "choices" }, q.options.map(o => {
      const [title, sub] = splitLabel(o.label);
      return el("button", {
        class: (pills ? "pill-choice" : "choice") + (isOn(o.value) ? " on" : ""), "aria-pressed": String(isOn(o.value)),
        onclick: () => {
          if (multi) {
            const cur = new Set(S.answers[q.id] || []);
            cur.has(o.value) ? cur.delete(o.value) : cur.add(o.value);
            S.answers[q.id] = [...cur];
          } else {
            S.answers[q.id] = o.value;
          }
          changed();
        },
      }, multi && !pills ? el("span", { class: "check" + (isOn(o.value) ? " on" : "") }, icon("check", 16, { width: 3 })) : null,
      el("span", { class: "choice-title" }, pills ? o.label : title), !pills && sub ? el("span", { class: "choice-sub" }, sub) : null);
    }));
  }

  function question(q) {
    if (q.kind === "text") {
      const input = el("input", { type: "text", value: S.answers[q.id] ?? "", class: "wide" });
      input.addEventListener("change", () => { S.answers[q.id] = input.value; changed({ redraw: false }); });
      return el("section", { class: "q" }, el("h2", {}, q.text), input, q.why ? el("p", { class: "why" }, q.why) : null);
    }
    return el("section", { class: "q" }, el("h2", {}, q.text),
      choices(q, { pills: q.maps_to === "language" }), q.why ? el("p", { class: "why" }, q.why) : null);
  }

  function found() {
    const evs = summary.events || [];
    const favs = (B.suggest || {}).favourites || 0;
    const stat = (n, label, cls = "") => el("div", { class: "stat" }, el("span", { class: "stat-n " + cls }, n), el("span", { class: "muted" }, label));
    return el("section", { class: "card found" },
      el("h2", {}, t("wishes.found", { name: B.info.title || B.name })),
      el("div", { class: "stats" },
        stat(num(summary.photos), t("wishes.photosLabel", { n: summary.photos })),
        stat(num(summary.days), t("wishes.days", { n: summary.days, range: dateRange(summary.date_from, summary.date_to) })),
        favs ? stat(num(favs), t("wishes.favs", { n: favs }), "gold") : null,
        summary.duplicates_collapsed ? stat(num(summary.duplicates_collapsed), t("wishes.dups", { n: summary.duplicates_collapsed })) : null),
      el("div", { class: "strip" }, evs.map(e => el("div", { class: "day" },
        e.sample && e.sample[0] ? el("img", { src: thumbUrl(B.name, e.sample[0]), alt: "", loading: "lazy" }) : el("div", { class: "day-empty" }),
        el("span", { class: "day-date" }, shortDay(e.start)),
        el("span", { class: "muted small" }, [e.place, t("wishes.photos", { n: e.n, nf: num(e.n) })].filter(Boolean).join(" · "))))));
  }

  function favourites() {
    const marks = qOf("favourite_markers"), mode = qOf("favourites_mode");
    if (!mode) return null;
    const showMarks = el("details", { class: "marks" }, el("summary", {}, t("wishes.marksToggle")), marks ? question(marks) : null);
    return el("section", { class: "q" },
      el("h2", {}, t("wishes.favTitle")), el("p", { class: "q-lead" }, mode.text), choices(mode),
      mode.why ? el("p", { class: "why" }, mode.why) : null, marks ? showMarks : null);
  }

  function formats() {
    return el("section", { class: "q" }, el("h2", {}, t("wishes.format")),
      el("div", { class: "choices three" }, G.profiles.map(p => {
        const on = S.profile === p.name, ar = p.width / p.height;
        const h = 132, w = Math.round(h * ar);
        return el("button", { class: "choice format" + (on ? " on" : ""), "aria-pressed": String(on),
          onclick: () => { S.profile = p.name; changed(); } },
        el("span", { class: "format-stage" }, el("span", { class: "format-book", style: `width:${w}px;height:${h}px` },
          sampleCover ? el("img", { src: thumbUrl(B.name, sampleCover), alt: "" }) : null)),
        el("span", { class: "choice-title" }, t(`format.${p.name}`)),
        el("span", { class: "choice-sub" }, `${num(p.width / 10, 1)} × ${num(p.height / 10, 1)} cm`));
      })));
  }

  function thickness() {
    const prof = profileOf(S.profile);
    const min = prof.min_pages || 24, max = Math.max(min, Math.min(prof.max_pages || 150, Math.round(unique / S.perPage) + chapters));
    const only = favMode() === "only";
    const value = Math.max(min, Math.min(max, pages()));
    const label = el("strong", { class: "serif big-num" }, t("wishes.pages", { n: value }));
    const sub = el("span", { class: "muted" }, only ? t("wishes.onlyFavs", { n: photos() }) : t("wishes.aboutPhotos", { n: photos() }));
    const slider = el("input", { type: "range", min: String(min), max: String(max), step: "2", value: String(value),
      disabled: only || min === max, "aria-label": t("wishes.thickness") });
    slider.addEventListener("input", () => {
      S.photos = Math.max(10, Math.round((Number(slider.value) - chapters) * S.perPage));
      label.textContent = t("wishes.pages", { n: slider.value });
      sub.textContent = t("wishes.aboutPhotos", { n: photos() });
      changed({ redraw: false });
    });
    return el("section", { class: "card q" },
      el("div", { class: "row between" }, el("h2", {}, t("wishes.thickness")), el("span", {}, label, " ", sub)),
      slider,
      el("div", { class: "row between muted small" }, el("span", {}, t("wishes.thin")), el("span", {}, t("wishes.thick"))),
      min === max && !only ? el("p", { class: "why" }, t("wishes.fewPhotos", { n: min })) : null);
  }

  function more() {
    const per = el("input", { type: "range", min: "2", max: "6", step: "0.5", value: String(S.perPage) });
    const perOut = el("strong", {}, num(S.perPage, 1));
    per.addEventListener("input", () => { S.perPage = Number(per.value); perOut.textContent = num(S.perPage, 1); });
    per.addEventListener("change", () => changed());
    const subs = el("input", { type: "checkbox", checked: S.subtitles });
    subs.addEventListener("change", () => { S.subtitles = subs.checked; changed({ redraw: false }); });
    const ai = el("input", { type: "checkbox", checked: S.ai, disabled: !G.ai_available });
    ai.addEventListener("change", () => { S.ai = ai.checked; changed(); });
    const effort = el("select", {}, EFFORTS.map(v => el("option", { value: v, selected: S.effort === v }, t(`effort.${v}`))));
    effort.addEventListener("change", () => { S.effort = effort.value; changed({ redraw: false }); });
    const tailor = G.ai_available ? el("button", { class: "btn ghost", onclick: async () => {
      tailor.disabled = true;
      tailor.textContent = t("wishes.tailoring");
      try {
        B.questions = await runJob(bookApi(B.name, "questions"), { ai: true, effort: S.effort, ui_language: getLang() });
        B.questions_meta = { lang: getLang(), ai: true };
        B.brief = null;
        render(B);
      } catch (e) {
        tailor.textContent = t("err.short", { msg: e.message });
      }
    } }, t("wishes.tailor")) : null;
    return el("details", { class: "card more", open: !!sessionStorage.getItem(`more:${B.name}`) || null,
      ontoggle: e => sessionStorage[e.target.open ? "setItem" : "removeItem"](`more:${B.name}`, "1") },
    el("summary", {}, t("wishes.more")),
    el("div", { class: "settings" },
      el("label", { class: "setting" }, el("span", {}, t("wishes.perPage")), per, perOut),
      el("label", { class: "setting" }, subs, el("span", {}, t("wishes.subtitles"))),
      el("label", { class: "setting" }, ai, el("span", {}, t("wishes.useAi"))),
      G.ai_available ? null : el("p", { class: "why" }, t("wishes.noKey"), " ",
        el("button", { class: "link", onclick: keyDialog }, t("key.enter"))),
      el("label", { class: "setting" }, el("span", {}, t("wishes.effort")), effort),
      tailor));
  }

  async function saveSettings() {
    return api("POST", bookApi(B.name, "settings"), {
      answers: S.answers,
      settings: { profile: S.profile, photos: favMode() === "only" ? null : S.photos, per_page: S.perPage,
        subtitles: S.subtitles, ai: S.ai, effort: S.effort },
    });
  }

  async function refreshEstimate() {
    try {
      B.brief = await saveSettings();
      drawReady(await api("GET", bookApi(B.name, "estimate")));
    } catch (e) {
      ready.replaceChildren(el("p", { class: "error" }, t("err.short", { msg: e.message })));
    }
  }

  function drawReady(est) {
    const few = est.target_photos + est.chapters < est.min_pages;  // even one photo per page leaves blanks
    const go = el("button", { class: "btn light big", onclick: async () => {
      if (B.plan && !confirm(t("wishes.replaceConfirm"))) return;
      go.disabled = true;
      try {
        B.brief = await saveSettings();
        await api("POST", bookApi(B.name, "select"));
        location.hash = bookUrl(B.name, "making");
      } catch (e) {
        go.disabled = false;
        ready.append(el("p", { class: "error" }, t("err.short", { msg: e.message })));
      }
    } }, B.plan ? t("wishes.goAgain") : t("wishes.go"), icon("next", 24));
    const toggleAi = G.ai_available ? el("button", { class: "link light", onclick: () => { S.ai = !S.ai; changed(); } },
      S.ai ? t("wishes.withoutAi") : t("wishes.withAi")) : null;
    ready.replaceChildren(
      el("div", { class: "grow col" },
        el("span", { class: "serif h2" }, t("wishes.ready")),
        el("span", {}, est.ai ? t("wishes.aiNote", { n: est.minutes, cost: costText(est.usd) }) : t("wishes.offlineNote")),
        few ? el("span", { class: "warn-line" }, icon("warn", 18), t("wishes.fewPages", { min: est.min_pages })) : null,
        B.plan ? el("span", { class: "small soft" }, t("wishes.replaces")) : null,
        toggleAi),
      go);
  }

  function draw() {
    const rest = B.questions.filter(q => !SKIP.has(q.maps_to) && !["favourite_markers", "favourites_mode"].includes(q.maps_to));
    const scrollY = window.scrollY;
    show(el("div", { class: "page narrow" },
      el("div", { class: "page-head" }, el("h1", {}, t("wishes.title")), el("p", { class: "lead" }, t("wishes.lead"))),
      found(), favourites(), formats(), thickness(),
      rest.map(question), more(), ready));
    window.scrollTo(0, scrollY);
  }

  draw();
  ready.replaceChildren(el("span", { class: "spinner light" }));
  refreshEstimate();
}
