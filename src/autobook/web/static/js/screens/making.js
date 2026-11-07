// While autobook works: looking at the photos, choosing the best moments, writing the titles.
// The job runs on the server, so this page can be closed and opened again.

import { bookUrl, poll, thumbUrl } from "../api.js";
import { el, icon, progressBar, show } from "../dom.js";
import { num, t } from "../i18n.js";

const PHASES = { score: 0, rank: 1, titles: 2 };

export async function making(B) {
  const job = B.job;
  if (!job) {
    location.hash = bookUrl(B.name, B.plan ? "selection" : "wishes");
    return;
  }
  const scan = job.kind === "scan";
  const samples = (B.summary?.events || []).flatMap(e => e.sample || []).slice(0, 12);
  const tiles = samples.map(id => el("div", { class: "m-tile" }, el("img", { src: thumbUrl(B.name, id), alt: "" })));
  const steps = scan
    ? [{ title: t("making.scan"), sub: "" }]
    : [{ title: t("making.look"), sub: "" }, { title: t("making.choose"), sub: "" }, { title: t("making.titles"), sub: "" }];
  const list = el("ol", { class: "phases" });
  const lead = el("p", { class: "lead" }, scan ? t("making.leadScan") : t("making.lead"));
  show(el("div", { class: "page narrow center" },
    tiles.length ? el("div", { class: "m-grid" }, tiles) : null,
    el("h1", {}, scan ? t("making.titleScan") : t("making.title")), lead, list,
    el("p", { class: "muted" }, t("making.leave"))));

  let phase = 0;
  function draw(j) {
    if (j && j.stage in PHASES) phase = PHASES[j.stage];
    const frac = j && j.total ? j.done / j.total : 0;
    list.replaceChildren(...steps.map((s, i) => {
      const state = i < phase ? "done" : i === phase ? "now" : "todo";
      let sub = "";
      if (state === "now" && j && j.total > 1 && j.stage !== "release") {
        sub = scan ? t("making.scanCount", { done: num(j.done), total: num(j.total) })
          : phase === 0 ? t("making.lookCount", { done: num(j.done), total: num(j.total) })
            : phase === 1 ? t("making.chooseCount", { done: j.done, total: j.total }) : "";
      }
      return el("li", { class: `phase ${state}` },
        state === "done" ? el("span", { class: "ph-dot done" }, icon("check", 18, { color: "#fff", width: 3 }))
          : el("span", { class: `ph-dot ${state}` }),
        el("span", { class: "col grow" }, el("span", { class: "ph-title" }, s.title),
          sub ? el("span", { class: "muted small" }, sub) : null,
          state === "now" && j && j.total > 1 ? progressBar(frac) : null));
    }));
    const seen = phase > 0 ? tiles.length : Math.round(frac * tiles.length);
    tiles.forEach((tile, i) => {
      tile.classList.toggle("seen", i < seen);
      tile.classList.toggle("looking", i === seen && phase === 0);
    });
  }
  draw(job);
  try {
    await poll(job.id, draw);
    if (location.hash.includes("/making")) location.hash = bookUrl(B.name, scan ? "wishes" : "selection");
  } catch (e) {
    list.replaceChildren(el("li", { class: "phase error" }, icon("warn", 22), el("span", { class: "col" },
      el("span", { class: "ph-title" }, t("making.failed")),
      el("details", {}, el("summary", {}, t("print.details")), el("pre", {}, e.message)))));
    lead.replaceChildren(el("a", { class: "btn", href: bookUrl(B.name, "wishes") }, t("making.back")));
  }
}
