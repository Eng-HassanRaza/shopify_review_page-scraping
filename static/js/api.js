/** All HTTP calls in one place. */
const BASE = "";

async function _req(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(BASE + path, opts);
  if (!r.ok) {
    const err = await r.json().catch(() => ({ error: r.statusText }));
    throw new Error(err.error || r.statusText);
  }
  return r.json();
}

export const api = {
  createJob:  (appUrl, limitCount) => _req("POST", "/api/jobs", { app_url: appUrl, limit_count: limitCount || null }),
  listJobs:   ()         => _req("GET",  "/api/jobs"),
  getJob:     (id)       => _req("GET",  `/api/jobs/${id}`),
  pauseJob:   (id)       => _req("POST", `/api/jobs/${id}/pause`),
  resumeJob:  (id)       => _req("POST", `/api/jobs/${id}/resume`),
  getStores:  (id, page, perPage) => _req("GET", `/api/jobs/${id}/stores?page=${page}&per_page=${perPage}`),
  deleteJob:  (id)       => _req("DELETE", `/api/jobs/${id}`),
  exportUrl:  (id)       => `/api/jobs/${id}/export`,
};
