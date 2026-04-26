/** Entry point — wires all modules together. */
import { initJob } from "./job.js";
import { initResults } from "./results.js";

// Toast helper — exported so other modules can call toast()
export function toast(msg, ms = 3000) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.add("hidden"), ms);
}

document.addEventListener("DOMContentLoaded", () => {
  initJob();
  initResults();
});
