/** Results table rendering, pagination, CSV export, job selector. */
import { api } from "./api.js";
import { toast } from "./app.js";
import { startPolling } from "./job.js";

const PER_PAGE = 50;
let _jobId = null;
let _page  = 1;
let _total = 0;

export async function initResults() {
  await refreshJobSelector();
  document.getElementById("job-select").addEventListener("change", onJobSelect);
  document.getElementById("export-btn").addEventListener("click", onExport);
  document.getElementById("prev-page").addEventListener("click", () => changePage(_page - 1));
  document.getElementById("next-page").addEventListener("click", () => changePage(_page + 1));
}

async function refreshJobSelector(selectId) {
  const sel = document.getElementById("job-select");
  try {
    const jobs = await api.listJobs();
    sel.innerHTML = '<option value="">— select a job —</option>' +
      jobs.map(j =>
        `<option value="${j.id}">${j.app_name || "Job #" + j.id} — ${j.status} (${j.total_reviews_found} reviews)</option>`
      ).join("");
    if (selectId) sel.value = selectId;
  } catch (_) {}
}

async function onJobSelect() {
  const id = parseInt(this.value, 10);
  if (!id) return;
  await loadJobResults(id);
}

export async function loadJobResults(jobId) {
  _jobId = jobId;
  _page  = 1;
  document.getElementById("job-select").value = jobId;
  document.getElementById("export-btn").disabled = false;
  await fetchAndRender();
  // Refresh selector so counts update
  refreshJobSelector(jobId);
}

async function fetchAndRender() {
  if (!_jobId) return;
  try {
    const data = await api.getStores(_jobId, _page, PER_PAGE);
    _total = data.total;
    renderTable(data.stores);
    renderPagination();
  } catch (err) {
    toast("Error loading results: " + err.message);
  }
}

function changePage(p) {
  const maxPage = Math.ceil(_total / PER_PAGE) || 1;
  _page = Math.max(1, Math.min(p, maxPage));
  fetchAndRender();
}

function renderTable(stores) {
  const wrap = document.getElementById("results-table-wrap");
  if (!stores.length) {
    wrap.innerHTML = '<p class="empty-msg">No results yet.</p>';
    return;
  }

  const rows = stores.map(s => {
    const emails = (s.emails || []).join(", ") || "—";
    const url    = s.store_url ? `<a href="${s.store_url}" target="_blank">${_trunc(s.store_url, 30)}</a>` : "—";
    return `<tr>
      <td title="${_esc(s.store_name)}">${_esc(_trunc(s.store_name, 28))}</td>
      <td>${_esc(s.country || "—")}</td>
      <td>${url}</td>
      <td title="${_esc(emails)}">${_esc(_trunc(emails, 40))}</td>
      <td>${s.rating ?? "—"}</td>
      <td>${_badge(s.status)}</td>
    </tr>`;
  }).join("");

  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Store</th><th>Country</th><th>URL</th>
          <th>Emails</th><th>Rating</th><th>Status</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderPagination() {
  const maxPage = Math.ceil(_total / PER_PAGE) || 1;
  const pg = document.getElementById("pagination");
  if (maxPage <= 1) { pg.classList.add("hidden"); return; }
  pg.classList.remove("hidden");
  document.getElementById("page-info").textContent = `Page ${_page} of ${maxPage}  (${_total} total)`;
  document.getElementById("prev-page").disabled = _page <= 1;
  document.getElementById("next-page").disabled = _page >= maxPage;
}

function onExport() {
  if (!_jobId) return;
  window.location.href = api.exportUrl(_jobId);
}

function _badge(status) {
  const map = {
    emails_found:  ["success",  "emails found"],
    no_emails:     ["warning",  "no emails"],
    url_not_found: ["warning",  "URL not found"],
    failed:        ["danger",   "failed"],
    pending:       ["neutral",  "pending"],
    processing:    ["neutral",  "processing"],
    url_found:     ["neutral",  "URL found"],
  };
  const [cls, label] = map[status] || ["neutral", status];
  return `<span class="badge badge-${cls}">${label}</span>`;
}

function _esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function _trunc(s, n) {
  return s && s.length > n ? s.slice(0, n) + "…" : (s || "");
}
