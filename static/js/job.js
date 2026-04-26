/** Job creation, progress polling, pause/resume. */
import { api } from "./api.js";
import { toast } from "./app.js";
import { loadJobResults } from "./results.js";

const POLL_MS = 3000;
let _pollTimer = null;
let _activeJobId = null;

export function initJob() {
  document.getElementById("job-form").addEventListener("submit", onSubmit);
  document.getElementById("pause-btn").addEventListener("click", onPause);
  document.getElementById("resume-btn").addEventListener("click", onResume);
}

async function onSubmit(e) {
  e.preventDefault();
  const appUrl     = document.getElementById("app-url").value.trim();
  const limitInput = document.getElementById("limit-count").value.trim();
  const limit      = limitInput ? parseInt(limitInput, 10) : null;

  const btn = document.getElementById("start-btn");
  btn.disabled = true;
  btn.textContent = "Starting…";

  try {
    const job = await api.createJob(appUrl, limit);
    _activeJobId = job.id;
    showProgress(job);
    startPolling(job.id);
    toast("Job started");
  } catch (err) {
    toast("Error: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Start Scraping";
  }
}

async function onPause() {
  if (!_activeJobId) return;
  try {
    await api.pauseJob(_activeJobId);
    stopPolling();
    document.getElementById("pause-btn").classList.add("hidden");
    document.getElementById("resume-btn").classList.remove("hidden");
    document.getElementById("progress-label").textContent = "Paused";
    toast("Job paused");
  } catch (err) {
    toast("Error: " + err.message);
  }
}

async function onResume() {
  if (!_activeJobId) return;
  try {
    await api.resumeJob(_activeJobId);
    document.getElementById("resume-btn").classList.add("hidden");
    document.getElementById("pause-btn").classList.remove("hidden");
    startPolling(_activeJobId);
    toast("Job resumed");
  } catch (err) {
    toast("Error: " + err.message);
  }
}

export function startPolling(jobId) {
  stopPolling();
  _activeJobId = jobId;
  _pollTimer = setInterval(() => pollJob(jobId), POLL_MS);
  pollJob(jobId);
}

function stopPolling() {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
}

async function pollJob(jobId) {
  try {
    const data = await api.getJob(jobId);
    updateProgress(data);

    const done = ["completed", "failed", "paused"].includes(data.status);
    if (done && !data.running) {
      stopPolling();
      if (data.status === "completed") toast("Job completed!");
      if (data.status === "failed")    toast("Job failed: " + (data.error || "unknown error"));
      loadJobResults(jobId);
    }
  } catch (_) { /* ignore transient errors */ }
}

function showProgress(job) {
  document.getElementById("progress-section").classList.remove("hidden");
  document.getElementById("job-app-name").textContent = job.app_name || job.id;
}

function updateProgress(data) {
  const stats = data.stats || {};
  const total    = data.total_reviews_found || 0;
  const processed = data.stores_processed || 0;
  const urlsFound  = (stats.url_found || 0) + (stats.emails_found || 0) + (stats.no_emails || 0);
  const withEmails = stats.emails_found || 0;

  document.getElementById("stat-reviews").textContent   = total;
  document.getElementById("stat-urls").textContent      = urlsFound;
  document.getElementById("stat-emails").textContent    = withEmails;
  document.getElementById("stat-processed").textContent = processed;

  const pct = total > 0 ? Math.min(100, Math.round(processed / total * 100)) : 0;
  document.getElementById("progress-bar").style.width   = pct + "%";

  const STATUS_LABELS = {
    idle:      "Waiting…",
    running:   `Processing… ${processed} / ${total}`,
    paused:    "Paused",
    completed: "Completed",
    failed:    "Failed",
  };
  document.getElementById("progress-label").textContent =
    STATUS_LABELS[data.status] || data.status;

  // Show pause / resume correctly
  const isRunning = data.running || data.status === "running";
  document.getElementById("pause-btn").classList.toggle("hidden",  !isRunning);
  document.getElementById("resume-btn").classList.toggle("hidden", isRunning || data.status === "completed");
}
