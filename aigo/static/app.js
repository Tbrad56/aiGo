"use strict";

// ---------- helpers ----------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (lat, lon) => `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
const km = (m) => (m >= 1000 ? `${(m / 1000).toFixed(m >= 10000 ? 0 : 1)} km` : `${Math.round(m)} m`);
const dur = (s) => {
  if (!isFinite(s)) return "—";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${m} min` : m ? `${m} min` : `${s} s`;
};

function haversine(a, b) {
  const R = 6371008.8, r = Math.PI / 180;
  const dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

let toastTimer;
function toast(msg, kind = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = kind;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), kind ? 7000 : 3000);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
    headers: { "Content-Type": "application/json" },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
    throw new Error(msg || `Request failed (${res.status})`);
  }
  return data;
}

async function act(fn) {
  try { return await fn(); } catch (e) { toast(e.message, "error"); }
}

// ---------- state ----------
const SPEEDS = [
  { name: "Walk", kmh: 5 },
  { name: "Run", kmh: 10 },
  { name: "Bike", kmh: 18 },
  { name: "Drive", kmh: 50 },
];
const state = {
  status: null,
  tab: "teleport",
  speed: Number(localGet("aigo.speed")) || 5,
  route: [],          // stops the user clicked (or an imported track)
  imported: false,    // route came from a GPX file: start at its first point
  path: [],           // planned path drawn on the map (follows roads when enabled)
  pathNote: "",
  pathSeq: 0,
  activePath: null,   // path of the route currently running
  followRoads: localGet("aigo.roads") !== "0",
  vary: localGet("aigo.vary") !== "0",
  lastPosKey: "",
  wasRunning: false,
  centered: false,
};
const VARIATION = 0.15;

function localGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } }

// ---------- map ----------
const map = L.map("map", { zoomControl: false }).setView([39.5, -98.35], 4);
L.control.zoom({ position: "topright" }).addTo(map);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · Routes <a href="https://routing.openstreetmap.de/">FOSSGIS OSRM</a>',
}).addTo(map);

const phoneMarker = L.circleMarker([0, 0], {
  radius: 9, weight: 3, color: "#ffffff", fillColor: "#2f6fec", fillOpacity: 1,
});
const phoneHalo = L.circle([0, 0], { radius: 30, weight: 0, fillColor: "#2f6fec", fillOpacity: 0.15, interactive: false });
const routeLine = L.polyline([], { color: "#2f6fec", weight: 4, opacity: 0.8, dashArray: "6 8" }).addTo(map);
const routeStops = L.layerGroup().addTo(map);
const pickPopup = L.popup({ closeButton: true, autoPan: true });

map.on("click", (e) => {
  const { lat, lng } = e.latlng;
  const lon = ((lng + 540) % 360) - 180;
  if (state.tab === "route") {
    state.route.push([lat, lon]);
    drawRoute();         // show the new stop right away
    schedulePath(250);   // then re-plan (debounced, so quick clicks make one road request)
    return;
  }
  openPick(lat, lon);
});

function openPick(lat, lon, title = "") {
  const html = `
    ${title ? `<b>${esc(title)}</b><br>` : ""}<span class="coords">${fmt(lat, lon)}</span>
    <div class="popup-actions">
      <button class="btn primary" data-act="tp">Teleport here</button>
      <button class="btn" data-act="route">Travel here</button>
      <button class="btn ghost" data-act="fav">★</button>
    </div>`;
  pickPopup.setLatLng([lat, lon]).setContent(html).openOn(map);
  const el = pickPopup.getElement();
  el.querySelector('[data-act="tp"]').onclick = () => { map.closePopup(); teleport(lat, lon); };
  el.querySelector('[data-act="route"]').onclick = () => { map.closePopup(); travelTo(lat, lon); };
  el.querySelector('[data-act="fav"]').onclick = () => saveFavorite(lat, lon, title);
}

// ---------- actions ----------
async function teleport(lat, lon) {
  const res = await act(() => api("/api/teleport", { body: { lat, lon } }));
  if (!res) return;
  if (res.warning) toast(res.warning, "warn");
  else toast(`Teleported to ${fmt(lat, lon)}`);
  loadHistory();
}

async function travelTo(lat, lon) {
  const start = startPoint();
  if (!start) {
    toast("Teleport somewhere first, then travel from there.", "warn");
    return;
  }
  let pts = [start, [lat, lon]];
  let note = "";
  if (state.followRoads) {
    try { pts = await roadPath(pts, state.speed); }
    catch (e) { note = ` (${e.message} Going in a straight line.)`; }
  }
  pts = decimate(pts, MAX_SEND_POINTS);
  const res = await act(() => api("/api/route", { body: {
    points: pts.map(([la, lo]) => ({ lat: la, lon: lo })),
    speed_kmh: state.speed,
    variation: state.vary ? VARIATION : 0,
  } }));
  if (!res) return;
  state.activePath = [start, ...pts];
  drawRoute();
  toast(`On the way: ${km(res.total_m)}, about ${dur(res.eta_s)}${note}`, note ? "warn" : "");
}

async function saveFavorite(lat, lon, suggested = "") {
  const name = prompt("Name this place", suggested || "");
  if (name === null) return;
  const fav = await act(() => api("/api/favorites", { body: { name, lat, lon } }));
  if (fav) { toast(`Saved “${fav.name}”`); loadFavorites(); }
}

$("#btn-clear").onclick = async () => {
  const s = await act(() => api("/api/clear", { body: {} }));
  if (s) toast("Stopped. Your phone is back on its real GPS.");
};
$("#btn-reconnect").onclick = () => act(() => api("/api/connect", { body: {} }));
$("#btn-save-fav").onclick = () => {
  const p = state.status && state.status.position;
  if (!p) return toast("Teleport somewhere first.", "warn");
  saveFavorite(p.lat, p.lon);
};

// ---------- tabs ----------
$$(".tab").forEach((t) => (t.onclick = () => setTab(t.dataset.tab)));
function setTab(tab) {
  state.tab = tab;
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
  $$(".tab-body").forEach((b) => (b.hidden = b.dataset.body !== tab));
  map.getContainer().style.cursor = tab === "route" ? "crosshair" : "";
  if (tab !== "joystick") stopJoystick();
  if (tab === "route") schedulePath(0);
  else drawRoute();
}

// ---------- speed ----------
function renderSpeeds() {
  $$("[data-speed-group]").forEach((g) => {
    g.innerHTML = SPEEDS.map((s) =>
      `<button data-kmh="${s.kmh}" class="${s.kmh === state.speed ? "active" : ""}"><b>${s.name}</b>${s.kmh} km/h</button>`
    ).join("");
    g.querySelectorAll("button").forEach((b) => (b.onclick = () => {
      state.speed = Number(b.dataset.kmh);
      localSet("aigo.speed", state.speed);
      renderSpeeds();
      if (state.followRoads) schedulePath(0);  // walking and driving take different roads
      else drawRoute();
    }));
  });
}

// ---------- route ----------
const ROUTER = "https://routing.openstreetmap.de";
const MAX_ROAD_WAYPOINTS = 100;
const MAX_SEND_POINTS = 20000;
const MAX_STOP_MARKERS = 60;

const routerProfile = (kmh) => (kmh <= 12 ? "routed-foot" : kmh <= 25 ? "routed-bike" : "routed-car");

// Ask OSRM for a path along real roads through `waypoints` ([[lat, lon], ...]).
async function roadPath(waypoints, kmh) {
  const coords = waypoints.map(([lat, lon]) => `${lon.toFixed(6)},${lat.toFixed(6)}`).join(";");
  const url = `${ROUTER}/${routerProfile(kmh)}/route/v1/driving/${coords}?overview=full&geometries=geojson`;
  let data;
  try {
    const res = await fetch(url);
    data = await res.json();
  } catch {
    throw new Error("Couldn't reach the road routing service.");
  }
  if (data.code !== "Ok" || !data.routes || !data.routes.length) throw new Error("No road route found there.");
  return data.routes[0].geometry.coordinates.map(([lon, lat]) => [lat, lon]);
}

// Keep at most `max` points, always keeping the first and last.
function decimate(pts, max) {
  if (pts.length <= max) return pts;
  const stride = Math.ceil((pts.length - 1) / (max - 1));
  const out = [];
  for (let i = 0; i < pts.length - 1; i += stride) out.push(pts[i]);
  out.push(pts[pts.length - 1]);
  return out;
}

function pathLength(pts) {
  let d = 0;
  for (let i = 1; i < pts.length; i++) d += haversine(pts[i - 1], pts[i]);
  return d;
}

function startPoint() {
  const p = state.status && state.status.position;
  return p ? [p.lat, p.lon] : null;
}

// Straight-line plan: from where the phone is (unless it's an imported track) through the stops.
function straightPath() {
  const start = startPoint();
  const path = start && !state.imported ? [start, ...state.route] : state.route.slice();
  return $("#route-loop").checked && path.length > 1 ? [...path, path[0]] : path;
}

let pathTimer = null;
function schedulePath(delay = 400) {
  clearTimeout(pathTimer);
  pathTimer = setTimeout(computePath, delay);
}

async function computePath() {
  clearTimeout(pathTimer);
  const seq = ++state.pathSeq;
  const straight = straightPath();
  state.path = straight;
  state.pathNote = "";
  if (state.followRoads && straight.length >= 2) {
    if (straight.length > MAX_ROAD_WAYPOINTS) {
      state.pathNote = "Too many stops to follow roads, so this uses straight lines.";
    } else {
      state.pathNote = "Finding roads…";
      drawRoute();
      try {
        const road = await roadPath(straight, state.speed);
        if (seq !== state.pathSeq) return;  // a newer plan replaced this one
        state.path = road;
        state.pathNote = "";
      } catch (e) {
        if (seq !== state.pathSeq) return;
        state.pathNote = `${e.message} Using straight lines.`;
      }
    }
  }
  drawRoute();
}

const stopIcon = (n) => L.divIcon({
  className: "",
  html: `<div class="stop-pin">${n}</div>`,
  iconSize: [22, 22],
  iconAnchor: [11, 11],
});

function drawRoute() {
  const running = state.status && state.status.mode === "route";
  const show = running || state.tab === "route";
  routeLine.setLatLngs(show ? (running && state.activePath ? state.activePath : state.path) : []);
  routeStops.clearLayers();
  if (show && state.route.length <= MAX_STOP_MARKERS) {
    state.route.forEach((pt, i) => {
      const marker = L.marker(pt, { icon: stopIcon(i + 1), title: `Stop ${i + 1}` }).addTo(routeStops);
      marker.on("click", () => openStopMenu(pt));
    });
  }
  const dist = pathLength(state.path);
  const n = state.route.length;
  $("#route-count").textContent = state.imported ? `${n} track points` : `${n} stop${n === 1 ? "" : "s"}`;
  $("#route-dist").textContent = km(dist);
  $("#route-eta").textContent = dist ? dur(dist / (state.speed / 3.6)) : "—";
  $("#route-note").textContent = state.pathNote;
  $("#route-note").hidden = !state.pathNote;
}

// Popup for a stop pin. Keyed by the point itself (not its index) so it stays right if stops change.
function openStopMenu(pt) {
  const i = state.route.indexOf(pt);
  if (i < 0) return;
  const el = document.createElement("div");
  el.innerHTML = `<b>Stop ${i + 1}</b><br><span class="coords">${fmt(pt[0], pt[1])}</span>
    <div class="popup-actions"><button class="btn danger" data-act="remove">Remove stop</button></div>`;
  el.querySelector('[data-act="remove"]').onclick = () => { map.closePopup(); removeStop(pt); };
  L.popup({ offset: [0, -6] }).setLatLng(pt).setContent(el).openOn(map);
}

function removeStop(pt) {
  const i = state.route.indexOf(pt);
  if (i < 0) return;
  state.route.splice(i, 1);
  if (!state.route.length) state.imported = false;
  drawRoute();
  schedulePath(150);
}

// Called on every live status update.
function routeOnStatus(s) {
  const running = s.mode === "route";
  if (state.wasRunning && !running) state.activePath = null;
  state.wasRunning = running;
  const posKey = s.position ? `${s.position.lat.toFixed(5)},${s.position.lon.toFixed(5)}` : "";
  if (posKey !== state.lastPosKey) {
    state.lastPosKey = posKey;
    // The plan starts where the phone is, so re-plan when it moves (not while a route is driving it).
    if (!running && state.tab === "route" && state.route.length) return schedulePath(800);
  }
  drawRoute();
}

function resetRoute() {
  state.route = [];
  state.imported = false;
  computePath();
}

$("#route-roads").checked = state.followRoads;
$("#route-vary").checked = state.vary;
$("#route-roads").onchange = (e) => {
  state.followRoads = e.target.checked;
  localSet("aigo.roads", state.followRoads ? "1" : "0");
  computePath();
};
$("#route-vary").onchange = (e) => {
  state.vary = e.target.checked;
  localSet("aigo.vary", state.vary ? "1" : "0");
};
$("#route-loop").onchange = () => computePath();
$("#btn-route-undo").onclick = () => {
  state.route.pop();
  if (!state.route.length) state.imported = false;
  computePath();
};
$("#btn-route-reset").onclick = resetRoute;

$("#btn-route-start").onclick = async () => {
  if (!state.route.length) return toast("Click the map to add at least one stop.", "warn");
  await computePath();  // make sure the plan matches what's on screen now
  const pts = decimate(state.path, MAX_SEND_POINTS);
  const res = await act(() => api("/api/route", { body: {
    points: pts.map(([lat, lon]) => ({ lat, lon })),
    speed_kmh: state.speed,
    loop: $("#route-loop").checked,
    variation: state.vary ? VARIATION : 0,
    from_current: !state.imported,
  } }));
  if (!res) return;
  const start = startPoint();
  state.activePath = start && !state.imported ? [start, ...pts] : pts;
  drawRoute();
  toast(`Route started: ${km(res.total_m)}, about ${dur(res.eta_s)}`);
};
$("#btn-route-stop").onclick = () => act(() => api("/api/route/stop", { body: {} }));

// ---------- GPX import ----------
function parseGpx(text) {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  if (doc.getElementsByTagName("parsererror").length) throw new Error("That file isn't valid GPX.");
  let nodes = doc.getElementsByTagName("trkpt");
  if (!nodes.length) nodes = doc.getElementsByTagName("rtept");
  if (!nodes.length) nodes = doc.getElementsByTagName("wpt");
  const pts = Array.from(nodes)
    .map((n) => [Number(n.getAttribute("lat")), Number(n.getAttribute("lon"))])
    .filter(([lat, lon]) => isFinite(lat) && isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180);
  if (pts.length < 2) throw new Error("No track points found in that file.");
  return pts;
}

$("#gpx-file").onchange = async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  let pts;
  try { pts = parseGpx(await file.text()); }
  catch (err) { return toast(err.message, "error"); }
  state.route = pts;
  state.imported = true;
  // A recorded track already follows the roads it was recorded on.
  state.followRoads = false;
  $("#route-roads").checked = false;
  setTab("route");
  map.fitBounds(L.latLngBounds(pts).pad(0.1));
  toast(`Imported ${pts.length} points from ${file.name}. The route starts at the track's first point.`);
};

// ---------- joystick ----------
const KEY_DIRS = { ArrowUp: "n", KeyW: "n", ArrowDown: "s", KeyS: "s", ArrowLeft: "w", KeyA: "w", ArrowRight: "e", KeyD: "e" };
const held = new Set();
const JOY_TICK_MS = 500;
let joyTimer = null;
let joyBusy = false;

function joyBearing() {
  let x = 0, y = 0;
  if (held.has("n")) y += 1;
  if (held.has("s")) y -= 1;
  if (held.has("e")) x += 1;
  if (held.has("w")) x -= 1;
  if (!x && !y) return null;
  return (Math.atan2(x, y) * 180 / Math.PI + 360) % 360;
}

async function joyTick() {
  const bearing = joyBearing();
  if (bearing === null) return stopJoystick();
  if (joyBusy) return;
  joyBusy = true;
  try {
    const meters = (state.speed / 3.6) * (JOY_TICK_MS / 1000);
    await api("/api/step", { body: { bearing, meters } });
  } catch (e) {
    toast(e.message, "error");
    held.clear();
    stopJoystick();
  } finally {
    joyBusy = false;
  }
}

function startJoystick() {
  if (joyTimer) return;
  joyTick();
  joyTimer = setInterval(joyTick, JOY_TICK_MS);
}
function stopJoystick() {
  clearInterval(joyTimer);
  joyTimer = null;
  $$(".pad button").forEach((b) => b.classList.remove("held"));
}
function syncJoystick() { held.size ? startJoystick() : stopJoystick(); }

document.addEventListener("keydown", (e) => {
  if (state.tab !== "joystick" || e.target.matches("input, textarea")) return;
  const d = KEY_DIRS[e.code];
  if (!d) return;
  e.preventDefault();
  held.add(d);
  syncJoystick();
});
document.addEventListener("keyup", (e) => {
  const d = KEY_DIRS[e.code];
  if (!d) return;
  held.delete(d);
  syncJoystick();
});
window.addEventListener("blur", () => { held.clear(); stopJoystick(); });

const PAD_DIRS = { 0: "n", 90: "e", 180: "s", 270: "w" };
$$(".pad button").forEach((b) => {
  const d = PAD_DIRS[b.dataset.dir];
  const down = (e) => { e.preventDefault(); b.classList.add("held"); held.add(d); syncJoystick(); };
  const up = () => { b.classList.remove("held"); held.delete(d); syncJoystick(); };
  b.addEventListener("pointerdown", down);
  b.addEventListener("pointerup", up);
  b.addEventListener("pointerleave", up);
  b.addEventListener("pointercancel", up);
});

// ---------- search ----------
const COORD_RE = /^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/;
$("#search-form").onsubmit = async (e) => {
  e.preventDefault();
  const q = $("#search").value.trim();
  const list = $("#search-results");
  if (!q) return;
  const m = q.match(COORD_RE);
  if (m) {
    const lat = Number(m[1]), lon = Number(m[2]);
    if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return toast("Those coordinates are out of range.", "error");
    list.hidden = true;
    map.setView([lat, lon], 16);
    openPick(lat, lon);
    return;
  }
  list.hidden = false;
  list.innerHTML = '<li class="empty">Searching…</li>';
  try {
    const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&q=${encodeURIComponent(q)}`;
    const res = await fetch(url, { headers: { "Accept-Language": navigator.language || "en" } });
    const results = await res.json();
    if (!results.length) { list.innerHTML = '<li class="empty">No matches.</li>'; return; }
    list.innerHTML = results.map((r, i) => `<li data-i="${i}"><span class="name" title="${esc(r.display_name)}">${esc(r.display_name)}</span></li>`).join("");
    list.querySelectorAll("li").forEach((li) => (li.onclick = () => {
      const r = results[Number(li.dataset.i)];
      const lat = Number(r.lat), lon = Number(r.lon);
      list.hidden = true;
      map.setView([lat, lon], 16);
      openPick(lat, lon, r.name || r.display_name.split(",")[0]);
    }));
  } catch {
    list.innerHTML = '<li class="empty">Search failed. Check your internet connection.</li>';
  }
};

// ---------- favorites & history ----------
async function loadFavorites() {
  const favs = await act(() => api("/api/favorites"));
  if (!favs) return;
  const ul = $("#favorites");
  if (!favs.length) { ul.innerHTML = '<li class="empty">No favorites yet.</li>'; return; }
  ul.innerHTML = favs.map((f) =>
    `<li data-id="${esc(f.id)}"><span class="name">${esc(f.name)}</span><button class="x" title="Delete">×</button></li>`).join("");
  ul.querySelectorAll("li").forEach((li, i) => {
    const f = favs[i];
    li.onclick = () => { map.setView([f.lat, f.lon], Math.max(map.getZoom(), 15)); openPick(f.lat, f.lon, f.name); };
    li.querySelector(".x").onclick = async (e) => {
      e.stopPropagation();
      if (await act(() => api(`/api/favorites/${encodeURIComponent(f.id)}`, { method: "DELETE" }))) loadFavorites();
    };
  });
}

async function loadHistory() {
  const hist = await act(() => api("/api/history"));
  if (!hist) return;
  const ul = $("#history");
  const items = hist.slice(0, 8);
  if (!items.length) { ul.innerHTML = '<li class="empty">Nothing yet.</li>'; return; }
  ul.innerHTML = items.map((h) => {
    const when = new Date(h.at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    return `<li><span class="name">${fmt(h.lat, h.lon)}</span><span class="meta">${esc(when)}</span></li>`;
  }).join("");
  ul.querySelectorAll("li").forEach((li, i) => {
    const h = items[i];
    li.onclick = () => { map.setView([h.lat, h.lon], Math.max(map.getZoom(), 15)); openPick(h.lat, h.lon); };
  });
}

// ---------- live status ----------
function renderStatus(s) {
  state.status = s;
  const pill = $("#conn");
  pill.dataset.state = s.connection;
  const dev = s.device || {};
  const label = {
    connected: `${dev.name || "iPhone"}${dev.ios ? ` · iOS ${dev.ios}` : ""}${dev.transport && dev.transport !== "pretend" ? ` · ${dev.transport}` : ""}`,
    connecting: "Connecting to iPhone…",
    error: "Not connected",
    disconnected: "Not connected",
  }[s.connection];
  $("#conn-text").textContent = label;
  pill.title = label;

  const showErr = s.connection === "error" || (s.error && s.connection !== "connecting");
  $("#conn-error").hidden = !showErr;
  $("#conn-error-text").textContent = s.error || "";

  if (s.position) {
    const ll = [s.position.lat, s.position.lon];
    $("#now-pos").textContent = fmt(s.position.lat, s.position.lon);
    phoneMarker.setLatLng(ll).addTo(map);
    phoneHalo.setLatLng(ll).addTo(map);
    if (!state.centered) { map.setView(ll, 15); state.centered = true; }
    else if (s.mode === "route" && !map.getBounds().pad(-0.2).contains(ll)) map.panTo(ll);
  } else {
    $("#now-pos").textContent = "Real location";
    phoneMarker.remove();
    phoneHalo.remove();
  }
  $("#btn-clear").disabled = !s.position;

  const r = s.route;
  $("#route-progress").hidden = !r;
  $("#btn-route-start").disabled = !!r;
  if (r) {
    const pct = r.total_m ? Math.min(100, (r.done_m / r.total_m) * 100) : 0;
    $("#route-bar").style.width = `${pct}%`;
    $("#route-progress-text").textContent = `${km(r.done_m)} of ${km(r.total_m)} · ${dur(r.eta_s)} left`;
  }
  routeOnStatus(s);
}

let wsDelay = 500;
function connectWs() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => (wsDelay = 500);
  ws.onmessage = (e) => renderStatus(JSON.parse(e.data));
  ws.onclose = () => {
    $("#conn").dataset.state = "error";
    $("#conn-text").textContent = "aiGo app not running";
    setTimeout(connectWs, wsDelay);
    wsDelay = Math.min(wsDelay * 2, 8000);
  };
}

// ---------- boot ----------
renderSpeeds();
setTab("teleport");
loadFavorites();
loadHistory();
connectWs();
