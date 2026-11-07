// Texts of the app live in /static/i18n/<lang>.json. A new language is one more file with the same
// keys (and an entry in LANGS); a missing key falls back to English.

export const LANGS = { de: "Deutsch", en: "English" };

let lang = "en";
let dict = {};
let fallback = {};
const cache = {};

function load(code) {
  cache[code] = cache[code] || fetch(`/static/i18n/${code}.json`).then(r => r.json());
  return cache[code];
}

function stored() {
  try { return localStorage.getItem("autobook.lang"); } catch { return null; }
}

export async function initI18n(code) {
  const want = (code || stored() || navigator.language || "en").slice(0, 2).toLowerCase();
  lang = LANGS[want] ? want : "en";
  fallback = await load("en");
  dict = lang === "en" ? fallback : await load(lang);
  document.documentElement.lang = lang;
}

export async function setLang(code) {
  try { localStorage.setItem("autobook.lang", code); } catch { /* private window: just for now */ }
  await initI18n(code);
}

export const getLang = () => lang;

// t("sel.photos", { n: 3 }) → "3 Fotos"; plural texts are objects { one, other }.
export function t(key, vars = {}) {
  let s = dict[key] ?? fallback[key];
  if (s == null) {
    console.warn("missing text:", key);
    return key;
  }
  if (typeof s === "object") s = s[new Intl.PluralRules(lang).select(Number(vars.n ?? 0))] ?? s.other;
  return s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
}

export const num = (n, digits = 0) =>
  new Intl.NumberFormat(lang, { maximumFractionDigits: digits }).format(n);

function day(iso) {
  if (!iso) return null;
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}

export function date(iso, opts = { day: "numeric", month: "long", year: "numeric" }) {
  const d = day(iso);
  return d ? new Intl.DateTimeFormat(lang, opts).format(d) : "";
}

// "30. Aug. – 6. Sep. 2025"; a folder date like "2026-07" becomes "Juli 2026".
export function dateRange(a, b, opts = { day: "numeric", month: "short", year: "numeric" }) {
  const x = day(a);
  if (!x) return "";
  if (a.length === 7) return new Intl.DateTimeFormat(lang, { month: "long", year: "numeric" }).format(x);
  const y = day(b || a);
  const f = new Intl.DateTimeFormat(lang, opts);
  return y && y.getTime() !== x.getTime() && f.formatRange ? f.formatRange(x, y) : f.format(x);
}

export const weekday = iso => date(iso, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
export const shortDay = iso => date(iso, { weekday: "short", day: "numeric", month: "short" });

export function clock(iso) {
  if (!iso || iso.length < 16) return "";
  return new Intl.DateTimeFormat(lang, { hour: "2-digit", minute: "2-digit" }).format(new Date(iso.slice(0, 19)));
}
