"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const TOKEN_KEY = "ps-token";

const STATES = {
  VACANT: { color: "var(--st-vacant)", desc: "Nobody in range. Lamps off." },
  OCCUPIED: { color: "var(--st-occupied)", desc: "Presence confirmed (3 consecutive in-range samples). Lamps on." },
  OCCUPIED_DAYLIGHT: { color: "var(--st-daylight)", desc: "Occupied, but daylight is sufficient (LDR inhibit above the lux threshold). Lamps held off." },
  GRACE: { color: "var(--st-grace)", desc: "Presence lost. Holding the lamps for the 6 s grace period." },
  OVERRIDE: { color: "var(--st-override)", desc: "A manual command is in force. Automation will not touch the lamps until resumed." },
};

let token = "";
try { token = sessionStorage.getItem(TOKEN_KEY) || ""; } catch (_) { /* storage blocked */ }
let pollTimer = null;
let slowTimer = null;
let historyMinutes = 60;
let daylightLux = { on: 500, off: 300 };  // replaced by the hub's thresholds from /api/snapshot

// --- helpers ---------------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

function ago(ts, now = Date.now() / 1000) {
  if (!ts) return "never";
  const s = Math.max(0, Math.round(now - ts));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${(s / 3600).toFixed(1)} h ago`;
}

const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const pct = (v) => `${Math.round((v / 255) * 100)}%`;

function seg(id) {
  const group = $(`#${id}`);
  group.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    $$("button", group).forEach((x) => x.classList.toggle("on", x === b));
    group.dispatchEvent(new CustomEvent("change", { detail: b.dataset.value }));
  });
  return () => $("button.on", group).dataset.value;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  if (res.status === 401) {
    signOut("That token was rejected.");
    throw new Error("unauthorized");
  }
  return res;
}

// --- auth ------------------------------------------------------------------

function signOut(message = "") {
  token = "";
  try { sessionStorage.removeItem(TOKEN_KEY); } catch (_) {}
  clearInterval(pollTimer);
  clearInterval(slowTimer);
  $("#app").hidden = true;
  $("#signout").hidden = true;
  $("#login").hidden = false;
  $("#login-error").textContent = message;
  $("#token-input").focus();
}

async function signIn(candidate) {
  token = candidate;
  const res = await fetch("/api/auth/check", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) return signOut("That token was rejected.");
  try { sessionStorage.setItem(TOKEN_KEY, token); } catch (_) {}
  $("#login").hidden = true;
  $("#app").hidden = false;
  $("#signout").hidden = false;
  start();
}

$("#login-form").addEventListener("submit", (e) => {
  e.preventDefault();
  signIn($("#token-input").value.trim());
});
$("#signout").addEventListener("click", () => signOut());

// --- rendering -------------------------------------------------------------

function setPill(id, label, value, cls) {
  const p = $(`#${id}`);
  p.textContent = `${label}: ${value}`;
  p.className = `pill ${cls || ""}`;
}

function renderSnapshot(s) {
  setPill("pill-hub", "Hub", s.liveness, { online: "good", stale: "warn", offline: "bad" }[s.liveness]);
  setPill("pill-broker", "Broker", s.broker_connected ? "connected" : "disconnected", s.broker_connected ? "good" : "bad");
  setPill("pill-rest", "REST", s.rest_enabled ? "enabled" : "off", s.rest_enabled ? "good" : "");
  $("#transport button[data-value=REST]").disabled = !s.rest_enabled;

  if (s.daylight_lux) daylightLux = s.daylight_lux;
  const t = s.telemetry;
  const state = t ? t.state : null;
  const info = STATES[state] || { color: "var(--muted)", desc: "No telemetry received yet." };
  $("#state-card").style.setProperty("--state-color", info.color);
  $("#state-label").textContent = state ? state.replace("_", " · ") : "No data";
  $("#state-desc").textContent = s.liveness === "offline" ? "Hub is offline; showing the last known state." : info.desc;
  $("#state-age").textContent = t ? `telemetry ${ago(t.server_ts, s.now)}` : "";
  $("#override-banner").hidden = state !== "OVERRIDE";

  if (t) {
    if (t.lux == null) {
      $("#r-ambient").textContent = `raw ${t.ambient_raw}`;
    } else {
      const daylight = t.lux >= daylightLux.on ? "daylight" : t.lux < daylightLux.off ? "dark" : "between";
      $("#r-ambient").textContent = `${Math.round(t.lux)} lux (${daylight})`;
    }
    $("#r-distance").textContent = t.distance_cm == null ? "n/a" : t.distance_cm < 0 ? "no echo" : `${Math.round(t.distance_cm)} cm`;
    $("#r-fault").textContent = t.fault ? "FAULT" : t.fault === 0 ? "OK" : "n/a";
    for (const [id, v] of [["A", t.brightness_a], ["B", t.brightness_b]]) {
      $(`#lamp-${id} .lamp-val`).textContent = `${pct(v)} · ${v}`;
      $(`#lamp-${id} .bar-fill`).style.width = pct(v);
    }
  }

  const alerts = $("#alerts");
  alerts.replaceChildren(
    ...s.alerts.slice(0, 4).map((a) =>
      el("div", { class: `alert ${a.cleared_ts ? "cleared" : ""}` },
        el("strong", {}, `${a.kind.replace(/_/g, " ")}${a.cleared_ts ? " (cleared)" : ""}`),
        `${a.detail || ""} · ${ago(a.raised_ts, s.now)}`))
  );

  $("#events").replaceChildren(
    ...s.events.map((e) =>
      el("li", {},
        el("span", {}, el("span", { class: "tag", style: `background:${(STATES[e.state] || {}).color}` }, e.state), ` #${e.seq_num}`),
        el("span", { class: "muted" }, clock(e.server_ts))))
  );
  if (!s.events.length) $("#events").append(el("li", { class: "muted" }, "No events yet."));

  $("#commands").replaceChildren(
    ...s.commands.map((c) => {
      const what = c.cmd_type ? `${c.label || c.cmd_type} ${c.cmd_type === "resume" ? "" : c.target}` : "external command";
      const extra = c.cmd_type === "brightness" ? ` ${c.value}` : "";
      const res = c.result
        ? el("span", { class: c.result === "OK" ? "res-ok" : "res-bad" }, c.result)
        : el("span", { class: "res-wait" }, "no ack");
      const rtt = c.rtt_ms != null ? ` · ${Math.round(c.rtt_ms)} ms` : "";
      return el("li", {},
        el("span", {}, `${what}${extra}`, el("span", { class: "muted" }, c.source === "BUTTON" ? " via wall button on the hub" : ` via ${c.source}${c.origin === "schedule" ? " (scheduled)" : ""}`)),
        el("span", {}, res, rtt));
    })
  );
  if (!s.commands.length) $("#commands").append(el("li", { class: "muted" }, "No commands yet."));

  const sched = $("#schedules");
  sched.replaceChildren(
    ...s.schedules.map((r) =>
      el("li", {},
        el("span", {}, `${new Date(r.due_ts * 1000).toLocaleString()} · ${r.label || r.cmd_type} ${r.target} via ${r.transport}`),
        el("button", { class: "ghost small", onclick: () => cancelSchedule(r.id) }, "Cancel")))
  );

  const sceneSel = $("#scene");
  if (!sceneSel.options.length) {
    for (const [name, v] of Object.entries(s.scenes)) sceneSel.append(el("option", { value: name }, `${name} (${pct(v)})`));
  }
}

function renderChart(points) {
  const svg = $("#chart");
  const W = 800, L = 34, R = 790, T1 = 22, B1 = 150, T2 = 168, B2 = 216;
  const ns = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs) => {
    const n = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };
  svg.replaceChildren();
  const now = Date.now() / 1000;
  const x0 = now - historyMinutes * 60;
  const x = (ts) => L + ((ts - x0) / (now - x0)) * (R - L);
  const yLamp = (v) => B1 - (v / 255) * (B1 - T1);
  // Light spans 1 to 100 000 lux, so it is drawn on a log scale.
  const yAmb = (lux) => B2 - (Math.log10(Math.min(Math.max(lux, 1), 1e5)) / 5) * (B2 - T2);

  for (const v of [0, 128, 255]) {
    svg.append(mk("line", { x1: L, x2: R, y1: yLamp(v), y2: yLamp(v), class: "grid-line" }));
    const t = mk("text", { x: L - 6, y: yLamp(v) + 4, "text-anchor": "end", class: "axis" });
    t.textContent = pct(v);
    svg.append(t);
  }
  svg.append(mk("line", { x1: L, x2: R, y1: B2, y2: B2, class: "grid-line" }));
  for (const v of [daylightLux.on, daylightLux.off]) svg.append(mk("line", { x1: L, x2: R, y1: yAmb(v), y2: yAmb(v), class: "thr" }));
  const ambLabel = mk("text", { x: L - 6, y: yAmb(daylightLux.on) + 4, "text-anchor": "end", class: "axis" });
  ambLabel.textContent = "lux";
  svg.append(ambLabel);
  for (let i = 0; i <= 4; i++) {
    const ts = x0 + (i / 4) * (now - x0);
    const t = mk("text", { x: x(ts), y: 234, "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle", class: "axis" });
    t.textContent = new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    svg.append(t);
  }

  if (!points.length) {
    const t = mk("text", { x: W / 2, y: 90, "text-anchor": "middle", class: "axis" });
    t.textContent = "No telemetry in this window yet";
    svg.append(t);
    return;
  }

  // State band along the top.
  for (let i = 0; i < points.length; i++) {
    const [ts, , , , state] = points[i];
    const next = i + 1 < points.length ? points[i + 1][0] : Math.min(now, ts + 5);
    const r = mk("rect", { x: x(ts), y: 4, width: Math.max(0.5, x(next) - x(ts)), height: 10, fill: (STATES[state] || {}).color || "gray" });
    const title = mk("title", {});
    title.textContent = `${state} at ${clock(ts)}`;
    r.append(title);
    svg.append(r);
  }

  const step = (idx, y) => {
    let d = "";
    points.forEach((p, i) => {
      const px = x(p[0]), py = y(p[idx]);
      d += i === 0 ? `M${px},${py}` : `H${px}V${py}`;
    });
    return d + `H${x(Math.min(now, points[points.length - 1][0] + 5))}`;
  };
  const line = (idx) => points.filter((p) => p[idx] != null).map((p, i) => `${i ? "L" : "M"}${x(p[0])},${yAmb(p[idx])}`).join("");
  svg.append(mk("path", { d: line(3), fill: "none", stroke: "var(--ambient)", "stroke-width": 1.5 }));
  svg.append(mk("path", { d: step(2, yLamp), fill: "none", stroke: "var(--lamp-b)", "stroke-width": 2, "stroke-dasharray": "5 3" }));
  svg.append(mk("path", { d: step(1, yLamp), fill: "none", stroke: "var(--lamp-a)", "stroke-width": 2 }));
}

function renderEnergy(r) {
  $("#e-saved").textContent = `${r.net_saved_wh.toFixed(2)} Wh`;
  $("#e-saved-pct").textContent = r.baseline_wh ? `${r.net_saved_pct.toFixed(1)} % of ${r.baseline_wh.toFixed(2)} Wh` : "";
  const rows = r.lamps.map((l) =>
    el("tr", {}, el("td", {}, l.name), el("td", {}, `${l.duty_pct.toFixed(0)} %`),
      el("td", {}, l.actual_wh.toFixed(2)), el("td", {}, l.baseline_wh.toFixed(2))));
  rows.push(el("tr", {}, el("td", {}, `Hub (${r.hub_w} W)`), el("td", {}, "100 %"), el("td", {}, r.hub_wh.toFixed(2)), el("td", {}, "–")));
  $("#energy-table").replaceChildren(
    el("thead", {}, el("tr", {}, el("th", {}, "Line item"), el("th", {}, "Duty"), el("th", {}, "Actual Wh"), el("th", {}, "Always-on Wh"))),
    el("tbody", {}, ...rows)
  );
  $("#energy-warnings").replaceChildren(...r.warnings.map((w) => el("li", {}, w)));
}

function metric(k, v, target) {
  const children = [el("div", { class: "k" }, k), el("div", { class: "v" }, v)];
  if (target) children.push(el("div", { class: `t ${target.pass == null ? "" : target.pass ? "pass" : "fail"}` }, target.text));
  return el("div", { class: "metric" }, ...children);
}

function renderMetrics(m) {
  const rtt = (s) => (s.median_ms != null ? `${Math.round(s.median_ms)} ms` : "–");
  const rttTarget = (s) =>
    s.median_ms == null
      ? { text: `target < ${s.target_ms} ms` }
      : { text: `p95 ${Math.round(s.p95_ms)} ms · ${s.within_target}/${s.acked} under ${s.target_ms} ms`, pass: s.within_target === s.acked };
  $("#metrics").replaceChildren(
    metric("Event delivery", m.delivery_pct == null ? "–" : `${m.delivery_pct} %`,
      { text: `${m.events_received} received, ${m.events_missing} missing · target ≥ 95 %`, pass: m.delivery_pct == null ? null : m.delivery_pct >= 95 }),
    metric("Override violations", m.override_violations, { text: "target 0", pass: m.override_violations === 0 }),
    metric("MQTT round-trip (median)", rtt(m.mqtt), rttTarget(m.mqtt)),
    metric("REST round-trip (median)", rtt(m.rest), rttTarget(m.rest)),
    metric("Rejected tokens", `${m.dashboard_auth_rejections} / ${m.device_auth_failures}`, { text: "dashboard / hub" }),
    metric("Malformed messages", m.rejected_messages, { text: "rejected + logged, not stored" })
  );
}

// --- data loading ------------------------------------------------------------

async function loadSnapshot() {
  try {
    renderSnapshot(await (await api("/api/snapshot")).json());
  } catch (e) {
    if (e.message !== "unauthorized") setPill("pill-hub", "Hub", "backend unreachable", "bad");
  }
}

async function loadSlow() {
  try {
    const [h, e, m] = await Promise.all([
      api(`/api/history?minutes=${historyMinutes}`).then((r) => r.json()),
      api(`/api/energy?minutes=${$("#energy-window").value}`).then((r) => r.json()),
      api(`/api/metrics?minutes=${$("#metrics-window").value}`).then((r) => r.json()),
    ]);
    renderChart(h.points);
    renderEnergy(e);
    renderMetrics(m);
  } catch (_) { /* next tick retries */ }
}

function start() {
  loadSnapshot();
  loadSlow();
  clearInterval(pollTimer);
  clearInterval(slowTimer);
  pollTimer = setInterval(loadSnapshot, 2000);
  slowTimer = setInterval(loadSlow, 10000);
}

// --- controls ----------------------------------------------------------------

const transport = seg("transport");
const target = seg("target");
seg("history-span");
$("#history-span").addEventListener("change", (e) => { historyMinutes = Number(e.detail); loadSlow(); });
$("#energy-window").addEventListener("change", loadSlow);
$("#metrics-window").addEventListener("change", loadSlow);
$("#brightness").addEventListener("input", (e) => { $("#brightness-val").textContent = `${e.target.value} (${pct(e.target.value)})`; });

function showResult(text, ok) {
  const r = $("#cmd-result");
  r.textContent = text;
  r.className = `cmd-result ${ok ? "ok" : "bad"}`;
}

async function sendCommand(type) {
  const body = { type, target: target(), transport: transport() };
  if (type === "brightness") body.value = Number($("#brightness").value);
  if (type === "scene") body.scene = $("#scene").value;

  if ($("#schedule-on").checked && type !== "resume") {
    const at = $("#schedule-at").value;
    if (!at) return showResult("Pick a date and time to schedule.", false);
    body.due_ts = new Date(at).getTime() / 1000;
    const res = await api("/api/schedule", { method: "POST", body: JSON.stringify(body) });
    const data = await res.json();
    showResult(res.ok ? `Scheduled for ${new Date(body.due_ts * 1000).toLocaleString()}` : data.error, res.ok);
    return loadSnapshot();
  }

  const buttons = $$("[data-cmd], [data-quick]");
  buttons.forEach((b) => (b.disabled = true));
  showResult(`Sending ${type} via ${body.transport}…`, true);
  try {
    const res = await api("/api/command", { method: "POST", body: JSON.stringify(body) });
    const c = await res.json();
    if (!res.ok && res.status !== 202) showResult(c.error || `HTTP ${res.status}`, false);
    else if (!c.acked_at) showResult(`No ack within the timeout (${c.correlation_id}). It may still arrive.`, false);
    else showResult(`${c.result} · ${Math.round(c.rtt_ms)} ms round-trip via ${c.source} (${c.correlation_id})`, c.result === "OK");
  } catch (e) {
    if (e.message !== "unauthorized") showResult(`Request failed: ${e.message}`, false);
  } finally {
    buttons.forEach((b) => (b.disabled = false));
    $("#transport button[data-value=REST]").disabled = $("#pill-rest").textContent.endsWith("off");
    loadSnapshot();
  }
}

async function cancelSchedule(id) {
  await api(`/api/schedule/${id}`, { method: "DELETE" });
  loadSnapshot();
}

$$("[data-cmd]").forEach((b) => b.addEventListener("click", () => sendCommand(b.dataset.cmd)));
$$("[data-quick]").forEach((b) => b.addEventListener("click", () => sendCommand(b.dataset.quick)));

$("#energy-download").addEventListener("click", async () => {
  const res = await api(`/api/energy.md?minutes=${$("#energy-window").value}`);
  const url = URL.createObjectURL(await res.blob());
  el("a", { href: url, download: "energy-report.md" }).click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

$("#state-legend").append(
  ...Object.entries(STATES).map(([name, s]) => el("span", {}, el("i", { class: "sw st", style: `background:${s.color}` }), name.replace("_", " ").toLowerCase()))
);

if (token) signIn(token);
else signOut();
