/*
  Headref app.js. Vanilla JS, 1 file, no build step.
  Allowed jobs only (DESIGN.md 4):
    1. open and close the evidence drawer
    2. poll a running workspace sync and reload when it ends
    3. load a member's summary after the page
  plus 1 helper: rewrite <time data-local> to the viewer's local time.
  Every page works without this file; the summary then shows its loading note.
*/

// Named constants, keep at the top.
const SYNC_POLL_INTERVAL_MS = 2000;
const SYNC_POLL_TIMEOUT_MS = 5 * 60 * 1000;
const REQUEST_TIMEOUT_MS = 10000;
const REQUEST_MAX_RETRIES = 2;
const ANSWER_SLOW_MS = 20000;
// The member summary is the 1 model call. A cold local model measured 45 s on this
// machine (W7), so it gets a longer limit than other requests.
const SUMMARY_TIMEOUT_MS = 120000;
const DRAWER_ANIMATION_MS = 150;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

document.documentElement.classList.add("js");

// Network ----------------------------------------------------------------------

/*
  fetch with a timeout and a small retry limit. Retries only on network
  errors, timeouts and 5xx; never on 4xx. Logs what was attempted, what came
  back and what was skipped.
*/
async function requestJSON(url, options = {}) {
  let lastError = null;
  for (let attempt = 0; attempt <= REQUEST_MAX_RETRIES; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      console.info("[headref] request", options.method || "GET", url, "attempt", attempt + 1);
      const res = await fetch(url, {
        credentials: "same-origin",
        headers: { Accept: "application/json", ...(options.headers || {}) },
        ...options,
        signal: controller.signal,
      });
      clearTimeout(timer);
      console.info("[headref] response", url, res.status);
      if (res.status >= 500 && attempt < REQUEST_MAX_RETRIES) {
        lastError = new Error(`HTTP ${res.status}`);
        await wait(500 * (attempt + 1));
        continue;
      }
      const body = res.headers.get("content-type")?.includes("application/json") ? await res.json() : null;
      return { status: res.status, ok: res.ok, body };
    } catch (err) {
      clearTimeout(timer);
      lastError = err;
      console.warn("[headref] request failed", url, err.name || err);
      if (attempt < REQUEST_MAX_RETRIES) await wait(500 * (attempt + 1));
    }
  }
  console.warn("[headref] giving up after retries", url);
  throw lastError || new Error("request failed");
}

async function requestText(url, timeoutMs = ANSWER_SLOW_MS * 2) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { credentials: "same-origin", signal: controller.signal });
    return { status: res.status, ok: res.ok, text: await res.text() };
  } finally {
    clearTimeout(timer);
  }
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Local time -------------------------------------------------------------------

function pad(n) {
  return String(n).padStart(2, "0");
}

function localizeTimes(root = document) {
  root.querySelectorAll("time[data-local][datetime]").forEach((el) => {
    const d = new Date(el.getAttribute("datetime"));
    if (Number.isNaN(d.getTime())) return;
    const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    const short = el.getAttribute("data-local") === "short";
    el.textContent = short
      ? `${d.getDate()} ${MONTHS[d.getMonth()]}, ${hm}`
      : `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}, ${hm}`;
  });
}

// Evidence drawer ----------------------------------------------------------------

const drawer = {
  el: null,
  backdrop: null,
  returnFocus: null,

  init() {
    this.el = document.querySelector("[data-drawer]");
    this.backdrop = document.querySelector("[data-drawer-backdrop]");
    if (!this.el) return;

    // With JS the inline details fallback hides; show the drawer buttons.
    document.querySelectorAll("[data-claim] [data-ev-open][hidden]").forEach((b) => b.removeAttribute("hidden"));

    document.addEventListener("click", (e) => {
      const opener = e.target.closest("[data-ev-open]");
      if (opener) {
        e.preventDefault();
        this.open(opener.getAttribute("data-ev-open"), opener.getAttribute("data-ev-target"), opener);
        return;
      }
      if (e.target.closest("[data-drawer-close]") || e.target === this.backdrop) this.close();
    });

    document.addEventListener("keydown", (e) => {
      if (this.el.hidden) return;
      if (e.key === "Escape") {
        e.preventDefault();
        this.close();
      } else if (e.key === "Tab") {
        this.trapFocus(e);
      }
    });
  },

  open(claimId, targetEvId, trigger) {
    const claim = document.getElementById(claimId);
    const list = claim?.querySelector(".evidence-details .ev-list");
    if (!claim || !list) return;

    this.returnFocus = trigger || document.activeElement;
    this.el.querySelector("[data-drawer-claim]").textContent = claim.querySelector(".claim__text").textContent;

    const badges = this.el.querySelector("[data-drawer-badges]");
    badges.replaceChildren();
    claim.querySelectorAll("[data-claim-meta] .badge").forEach((b) => {
      const copy = b.cloneNode(true);
      copy.classList.remove("badge--lg");
      if (copy.classList.contains("badge--outline")) {
        copy.classList.remove("badge--outline");
        copy.classList.add("badge--on-dark");
      }
      badges.appendChild(copy);
    });
    const count = document.createElement("span");
    const n = list.children.length;
    count.className = "badge";
    count.style.background = "var(--mint)";
    count.style.color = "#000";
    count.textContent = `${n} item${n === 1 ? "" : "s"}`;
    badges.appendChild(count);

    const body = this.el.querySelector("[data-drawer-body]");
    const clone = list.cloneNode(true);
    clone.querySelectorAll("[id]").forEach((node) => node.setAttribute("id", `drawer-${node.id}`));
    body.replaceChildren(clone);

    this.backdrop.hidden = false;
    this.el.hidden = false;
    document.body.classList.add("drawer-open");

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!reduce) {
      this.el.classList.add("drawer--entering");
      requestAnimationFrame(() => requestAnimationFrame(() => this.el.classList.remove("drawer--entering")));
    }

    if (targetEvId) {
      const target = clone.querySelector(`[data-ev-id="${CSS.escape(targetEvId)}"]`);
      if (target) {
        target.classList.add("ev-item--target");
        setTimeout(() => target.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" }), reduce ? 0 : DRAWER_ANIMATION_MS);
      }
    } else {
      body.scrollTop = 0;
    }
    this.el.querySelector("#drawer-title").focus();
  },

  close() {
    if (!this.el || this.el.hidden) return;
    this.el.hidden = true;
    this.backdrop.hidden = true;
    document.body.classList.remove("drawer-open");
    if (this.returnFocus && document.contains(this.returnFocus)) this.returnFocus.focus();
  },

  trapFocus(e) {
    const focusable = [...this.el.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])')]
      .filter((n) => n.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement.id === "drawer-title")) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  },
};

// Logout --------------------------------------------------------------------------

function initLogout() {
  document.querySelectorAll("form[data-logout]").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await requestJSON("/api/auth/logout", { method: "POST" });
      } finally {
        window.location.assign("/login");
      }
    });
  });
}

// Workspace sync (W5): while a sync runs, poll its status and reload once it ends.
// Stops after SYNC_POLL_TIMEOUT_MS rather than polling forever.
async function pollWorkspaceSync() {
  const box = document.querySelector("[data-ws-sync-poll]");
  if (!box) return;
  const url = box.getAttribute("data-ws-sync-poll");
  const note = box.querySelector("[data-ws-sync-note]");
  const started = Date.now();
  while (Date.now() - started < SYNC_POLL_TIMEOUT_MS) {
    await wait(SYNC_POLL_INTERVAL_MS);
    try {
      const status = await requestJSON(url);
      if (status && status.running === false) {
        window.location.reload();
        return;
      }
    } catch (err) {
      if (note) note.textContent = "Sync status could not be read. Reload the page to check again.";
      return;
    }
  }
  if (note) note.textContent = "The sync is taking longer than expected. Reload the page to check again.";
}

// Member summary (W7): the only model call, fetched after the page so the findings,
// already complete, never wait for it.
async function loadMemberSummary() {
  const box = document.querySelector("[data-summary-src]");
  if (!box) return;
  const status = box.querySelector("[data-summary-status]");
  try {
    const res = await requestText(box.getAttribute("data-summary-src"), SUMMARY_TIMEOUT_MS);
    if (!res.ok) throw new Error(`summary ${res.status}`);
    const doc = new DOMParser().parseFromString(res.text, "text/html");
    box.replaceChildren(...doc.body.childNodes);
  } catch (err) {
    if (status) status.textContent = "The summary could not be loaded. The findings below are complete without it.";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  localizeTimes();
  pollWorkspaceSync();
  loadMemberSummary();
  drawer.init();
  initLogout();
});
