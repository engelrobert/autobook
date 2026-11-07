// Talking to the local autobook server, and the state shared by all screens.

export const enc = encodeURIComponent;

// Global state from the page (books, profiles, whether an OpenAI key is configured).
export const G = JSON.parse(document.getElementById("initial-state").textContent);

export async function api(method, path, body) {
  const r = await fetch(path, {
    method, headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(data.detail || data.error || r.statusText);
    err.status = r.status;
    throw err;
  }
  return data;
}

// Follow a background job; onProgress(job) gets {stage, done, total}. Resolves with its result.
export async function poll(jobId, onProgress = () => {}) {
  for (;;) {
    const j = await api("GET", `/api/job/${jobId}`);
    if (j.status === "done") return j.result;
    if (j.status === "error") throw new Error(j.error);
    onProgress(j);
    await new Promise(r => setTimeout(r, 1200));
  }
}

export async function runJob(path, body, onProgress) {
  const { job } = await api("POST", path, body);
  return poll(job, onProgress);
}

export const bookApi = (name, path) => `/api/books/${enc(name)}/${path}`;
export const bookUrl = (name, step) => `#/b/${enc(name)}/${step}`;
export const thumbUrl = (name, id, size = 320) => `/thumb/${enc(name)}/${size}/${id}.jpg`;

export async function loadBook(name) {
  return api("GET", bookApi(name, "state"));
}

export function profileOf(name) {
  return G.profiles.find(p => p.name === name) || G.profiles.find(p => p.name === "square-21") || G.profiles[0];
}
