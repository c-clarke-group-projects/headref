/*
  Headref app.js. Vanilla JS, 1 file, no build step.
  Allowed jobs only (DESIGN.md 4):
    1. open and close the evidence drawer
    2. switch question tabs without a full reload (progressive enhancement)
    3. start a refresh and poll the sync status
  plus 1 helper: rewrite <time data-local> to the viewer's local time.
  Every page works without this file except the Refresh button.
*/

// Named constants, keep at the top.
const SYNC_POLL_INTERVAL_MS = 2000;
const SYNC_POLL_TIMEOUT_MS = 5 * 60 * 1000;
const REQUEST_TIMEOUT_MS = 10000;
const REQUEST_MAX_RETRIES = 2;
const ANSWER_SLOW_MS = 20000;
const DRAWER_ANIMATION_MS = 150;

const ERROR_LABELS = {
  TIMEOUT: "the request timed out",
  RATE_LIMITED: "rate limited by the source",
  AUTH_FAILED: "access token rejected",
  NOT_FOUND: "the project or repository was not found",
  UPSTREAM_ERROR: "the source returned an error",
  SCHEMA_INVALID: "some records could not be read and were left out",
};
const SOURCE_LABELS = { github: "GitHub", jira: "Jira" };
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

async function requestText(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ANSWER_SLOW_MS * 2);
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

// Tabs ---------------------------------------------------------------------------

/*
  Tabs are real links. With JS: arrow keys move focus (WAI ARIA tabs), and if
  the PROPOSED fragment route exists the panel swaps in place. If the fragment
  request fails, fall back to a normal page load.
*/
const tabs = {
  USE_FRAGMENT_ROUTE: true, // set false if GET /members/{id}/panel is not agreed

  init() {
    const list = document.querySelector("[data-tabs]");
    if (!list) return;
    const items = [...list.querySelectorAll('[role="tab"]')];

    list.addEventListener("keydown", (e) => {
      const i = items.indexOf(document.activeElement);
      if (i < 0) return;
      let next = null;
      if (e.key === "ArrowRight") next = items[(i + 1) % items.length];
      if (e.key === "ArrowLeft") next = items[(i - 1 + items.length) % items.length];
      if (e.key === "Home") next = items[0];
      if (e.key === "End") next = items[items.length - 1];
      if (next) {
        e.preventDefault();
        next.focus();
      }
    });

    if (!this.USE_FRAGMENT_ROUTE) return;
    list.addEventListener("click", (e) => {
      const tab = e.target.closest('[role="tab"]');
      if (!tab || e.metaKey || e.ctrlKey || e.shiftKey) return;
      e.preventDefault();
      this.select(list, items, tab);
    });
  },

  async select(list, items, tab) {
    const panel = document.querySelector("[data-panel]");
    const memberId = list.getAttribute("data-member-id");
    const question = tab.getAttribute("data-question");
    items.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.setAttribute("tabindex", on ? "0" : "-1");
    });
    panel.setAttribute("aria-labelledby", tab.id);
    panel.setAttribute("aria-busy", "true");
    panel.innerHTML =
      '<div class="skeleton layout-main"><div><div class="skeleton__bar skeleton__bar--w60"></div>' +
      '<div class="skeleton__bar skeleton__bar--w90"></div><div class="skeleton__bar skeleton__bar--w90"></div>' +
      '<p class="skeleton__text" data-loading-text>Building the answer from recorded data. This can take up to 20 seconds.</p></div></div>';
    const slow = setTimeout(() => {
      const t = panel.querySelector("[data-loading-text]");
      if (t) t.textContent += " Still working.";
    }, ANSWER_SLOW_MS);

    try {
      const res = await requestText(`/members/${memberId}/panel?question=${encodeURIComponent(question)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      panel.innerHTML = res.text; // server rendered and autoescaped by Jinja
      history.replaceState(null, "", tab.getAttribute("href"));
      localizeTimes(panel);
      panel.querySelectorAll("[data-claim] [data-ev-open][hidden]").forEach((b) => b.removeAttribute("hidden"));
      panel.querySelector("[data-panel-heading]")?.focus();
    } catch (err) {
      console.warn("[headref] panel swap failed, loading the page", err);
      window.location.assign(tab.getAttribute("href"));
    } finally {
      clearTimeout(slow);
      panel.removeAttribute("aria-busy");
    }
  },
};

// Refresh --------------------------------------------------------------------------

const refresh = {
  init() {
    document.querySelectorAll("[data-refresh]").forEach((btn) => {
      btn.addEventListener("click", () => this.start(btn));
    });
  },

  setState(btn, busy, statusText) {
    const label = btn.querySelector("[data-refresh-label]");
    const icon = btn.querySelector("[data-refresh-icon]");
    btn.disabled = busy;
    btn.setAttribute("aria-busy", busy ? "true" : "false");
    if (label) label.textContent = busy ? "Refreshing" : "Refresh";
    if (icon) icon.innerHTML = busy ? '<span class="btn__spinner" aria-hidden="true"></span>' : icon.dataset.idle || icon.innerHTML;
    const status = document.getElementById("refresh-status");
    if (status && statusText !== undefined) status.textContent = statusText;
  },

  showBanner(text) {
    const banner = document.querySelector("[data-refresh-banner]");
    if (!banner) return;
    banner.querySelector("[data-refresh-banner-text]").textContent = text;
    banner.hidden = false;
  },

  async start(btn) {
    const teamId = btn.getAttribute("data-team-id");
    const icon = btn.querySelector("[data-refresh-icon]");
    if (icon && !icon.dataset.idle) icon.dataset.idle = icon.innerHTML;
    this.setState(btn, true, "Starting sync");

    let res;
    try {
      res = await requestJSON(`/api/teams/${teamId}/sync`, { method: "POST" });
    } catch (err) {
      this.setState(btn, false, "");
      this.showBanner("Refresh failed: the request timed out. Showing data from the last successful sync.");
      return;
    }
    if (res.status === 403) {
      this.setState(btn, false, "");
      this.showBanner("You can only refresh your own team.");
      return;
    }
    if (res.status !== 202 && !res.ok) {
      this.setState(btn, false, "");
      this.showBanner("Refresh failed: the source returned an error. Showing data from the last successful sync.");
      return;
    }

    this.setState(btn, true, "Syncing GitHub and Jira");
    const started = Date.now();
    let status = res.body;
    while (!status?.all_finished) {
      if (Date.now() - started > SYNC_POLL_TIMEOUT_MS) {
        this.setState(btn, false, "");
        this.showBanner("Refresh is taking longer than expected. It will continue in the background.");
        return;
      }
      await wait(SYNC_POLL_INTERVAL_MS);
      try {
        const poll = await requestJSON(`/api/teams/${teamId}/sync/status`);
        if (poll.ok) status = poll.body;
      } catch (err) {
        console.warn("[headref] status poll failed, will retry", err);
      }
    }

    const failed = (status.runs || []).filter((r) => r.status === "failed");
    if (failed.length) {
      // A failed refresh never blanks the page. Cached data stays.
      const parts = failed.map((r) => `${SOURCE_LABELS[r.source] || r.source}, ${ERROR_LABELS[r.error_type] || "the source returned an error"}`);
      this.setState(btn, false, "");
      this.showBanner(`Refresh failed: ${parts.join("; ")}. Showing data from the last successful sync.`);
      return;
    }
    const skipped = (status.runs || []).reduce((sum, r) => sum + (r.items_skipped || 0), 0);
    if (skipped > 0) sessionStorage.setItem("headref-skipped", String(skipped));
    window.location.reload();
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

function showSkippedNote() {
  let skipped = null;
  try {
    skipped = sessionStorage.getItem("headref-skipped");
    sessionStorage.removeItem("headref-skipped");
  } catch (err) {
    return;
  }
  const status = document.getElementById("refresh-status");
  if (skipped && status) status.textContent = `Last sync skipped ${skipped} records.`;
}

document.addEventListener("DOMContentLoaded", () => {
  localizeTimes();
  drawer.init();
  tabs.init();
  refresh.init();
  initLogout();
  showSkippedNote();
});
