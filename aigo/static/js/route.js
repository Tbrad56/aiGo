// Route tab: stops (with waits), road preview from the server, start, save, saved routes, GPX.
import { $, act, api, bus, clockIn, dur, esc, fmtCoord, km, local, pathLength, state, toast, SPEEDS } from "./util.js";
import { map, openPick, pinIcon, routeLine, stopsLayer } from "./mapview.js";
import { placeName, cachedPlace } from "./places.js";

const MAX_PINS = 60;
const route = {
  stops: [],        // [{lat, lon, wait_min}]
  track: null,      // imported GPX points [[lat, lon], ...] (replayed as-is)
  path: [],         // planned path for the preview
  note: "",
  seq: 0,
  active: null,     // path of the trip that's running, for drawing
};
const opts = {
  roads: local.get("aigo.roads") !== "0",
  vary: local.get("aigo.vary") !== "0",
};

const startPoint = () => (state.status && state.status.position ? [state.status.position.lat, state.status.position.lon] : null);
const running = () => state.status && state.status.mode === "route";
const waitTotalS = () => route.stops.reduce((t, s) => t + (Number(s.wait_min) || 0) * 60, 0);

function tripBody() {
  return {
    stops: route.stops.map((s) => ({ lat: s.lat, lon: s.lon, wait_min: Number(s.wait_min) || 0 })),
    speed_kmh: state.speed,
    follow_roads: opts.roads,
    loop: $("#route-loop").checked,
    vary: opts.vary,
  };
}

// ---------- preview ----------
let timer = null;
export function schedulePreview(delay = 350) {
  clearTimeout(timer);
  timer = setTimeout(preview, delay);
}

async function preview() {
  clearTimeout(timer);
  const seq = ++route.seq;
  if (route.track) {
    route.path = route.track;
    route.note = "";
    return draw();
  }
  const start = startPoint();
  route.path = start ? [start, ...route.stops.map((s) => [s.lat, s.lon])] : route.stops.map((s) => [s.lat, s.lon]);
  if (!route.stops.length) { route.note = ""; return draw(); }
  route.note = opts.roads ? "Finding roads…" : "";
  draw();
  try {
    const plan = await api("/api/directions", { body: tripBody() });
    if (seq !== route.seq) return;
    route.path = plan.points;
    route.note = plan.note || "";
  } catch (e) {
    if (seq !== route.seq) return;
    route.note = e.message;
  }
  draw();
}

// ---------- drawing ----------
export function draw() {
  const show = running() || state.tab === "route";
  routeLine.setLatLngs(show ? (running() && route.active ? route.active : route.path) : []);
  stopsLayer.clearLayers();
  if (show && !route.track && route.stops.length <= MAX_PINS) {
    route.stops.forEach((s, i) => {
      const m = L.marker([s.lat, s.lon], { icon: pinIcon(i + 1), title: `Stop ${i + 1}` }).addTo(stopsLayer);
      m.on("click", () => openStopMenu(s));
    });
  }
  renderStops();
  const dist = pathLength(route.path);
  $("#route-dist").textContent = km(dist);
  const n = route.track ? route.track.length : route.stops.length;
  if (!n) {
    $("#route-eta").textContent = "No stops yet";
  } else {
    const secs = dist / (state.speed / 3.6) + waitTotalS();
    $("#route-eta").textContent = `${dur(secs)} · arrives ${clockIn(secs)}`;
  }
  $("#route-note").textContent = route.note;
  $("#route-note").hidden = !route.note;
  $("#btn-route-save").disabled = !!route.track || !route.stops.length;
}

function renderStops() {
  const ol = $("#stops");
  if (route.track) {
    ol.innerHTML = `<li class="track"><span class="main"><span class="t">Imported track</span>
      <span class="wait">${route.track.length} points, starts at its first point</span></span></li>`;
    return;
  }
  // Keep focus in a wait box while typing: only rebuild when the stop list itself changed.
  const sig = route.stops.map((s) => `${s.lat},${s.lon}`).join("|");
  if (ol.dataset.sig === sig) {
    route.stops.forEach((s, i) => {
      const name = cachedPlace(s.lat, s.lon);
      const t = ol.children[i] && ol.children[i].querySelector(".t");
      if (t && name) t.textContent = name.title;
    });
    return;
  }
  ol.dataset.sig = sig;
  ol.innerHTML = route.stops.map((s, i) => {
    const name = cachedPlace(s.lat, s.lon);
    return `<li data-i="${i}">
      <span class="main"><span class="t">${esc(name ? name.title : fmtCoord(s.lat, s.lon))}</span>
        <label class="wait">Wait <input type="number" min="0" max="1440" step="1" value="${Number(s.wait_min) || 0}" aria-label="Wait at stop ${i + 1}, minutes"> min</label>
      </span>
      <button class="x" title="Remove stop" aria-label="Remove stop ${i + 1}">×</button></li>`;
  }).join("");
  ol.querySelectorAll("li").forEach((li) => {
    const s = route.stops[Number(li.dataset.i)];
    li.querySelector("input").oninput = (e) => { s.wait_min = Math.max(0, Number(e.target.value) || 0); draw(); };
    li.querySelector(".x").onclick = () => removeStop(s);
    if (!cachedPlace(s.lat, s.lon)) placeName(s.lat, s.lon).then(() => renderStops());
  });
}

function openStopMenu(stop) {
  const i = route.stops.indexOf(stop);
  if (i < 0) return;
  const el = document.createElement("div");
  const name = cachedPlace(stop.lat, stop.lon);
  el.innerHTML = `<div class="t">Stop ${i + 1}${name ? ` · ${esc(name.title)}` : ""}</div>
    <div class="coords">${fmtCoord(stop.lat, stop.lon)}</div>
    <div class="popup-actions"><button class="btn" data-act="remove">Remove stop</button></div>`;
  el.querySelector("[data-act]").onclick = () => { map.closePopup(); removeStop(stop); };
  L.popup({ offset: [0, -8] }).setLatLng([stop.lat, stop.lon]).setContent(el).openOn(map);
}

// ---------- editing ----------
export function addStop(lat, lon) {
  if (route.track) { route.track = null; route.stops = []; }
  route.stops.push({ lat, lon, wait_min: 0 });
  draw();
  schedulePreview(250);
  placeName(lat, lon).then(() => renderStops());
}

function removeStop(stop) {
  const i = route.stops.indexOf(stop);
  if (i < 0) return;
  route.stops.splice(i, 1);
  draw();
  schedulePreview(150);
}

export function clearRoute() {
  route.stops = [];
  route.track = null;
  preview();
}

// ---------- start / travel ----------
export async function startRoute() {
  if (route.track) {
    const pts = decimate(route.track, 20000);
    const res = await act(() => api("/api/route", { body: {
      points: pts.map(([lat, lon]) => ({ lat, lon })), speed_kmh: state.speed,
      loop: $("#route-loop").checked, variation: opts.vary ? 0.15 : 0, from_current: false,
    } }));
    if (res) { route.active = pts; draw(); toast(`Route started: ${km(res.total_m)}, arrives ${clockIn(res.eta_s)}`); }
    return;
  }
  if (!route.stops.length) return toast("Click the map to add at least one stop.", "warn");
  const res = await act(() => api("/api/trip", { body: tripBody() }));
  if (!res) return;
  route.active = res.path;
  draw();
  toast(`Route started: ${km(res.total_m)}, arrives ${clockIn(res.eta_s)}${res.note ? ` (${res.note})` : ""}`, res.note ? "warn" : "");
}

export async function travelTo(lat, lon) {
  // The server picks the start (Home, or where the phone is) and explains if it can't.
  const res = await act(() => api("/api/trip", { body: {
    stops: [{ lat, lon }], speed_kmh: state.speed, follow_roads: opts.roads, vary: opts.vary,
  } }));
  if (!res) return;
  route.active = res.path;
  draw();
  toast(`On the way: ${km(res.total_m)}, arrives ${clockIn(res.eta_s)}${res.note ? ` (${res.note})` : ""}`, res.note ? "warn" : "");
}

function decimate(pts, max) {
  if (pts.length <= max) return pts;
  const stride = Math.ceil((pts.length - 1) / (max - 1));
  const out = [];
  for (let i = 0; i < pts.length - 1; i += stride) out.push(pts[i]);
  out.push(pts[pts.length - 1]);
  return out;
}

// ---------- saved routes ----------
export async function loadSavedRoutes() {
  const routes = await act(() => api("/api/routes"));
  if (!routes) return;
  state.routes = routes;
  bus.emit("routes", routes);
  const ul = $("#saved-routes");
  if (!routes.length) {
    ul.innerHTML = '<li class="empty">Build a route above and press Save to keep it.</li>';
    return;
  }
  ul.innerHTML = routes.map((r) => {
    const speed = SPEEDS.find((s) => s.kmh === r.speed_kmh);
    const bits = [`${r.stops.length} stop${r.stops.length === 1 ? "" : "s"}`, speed ? speed.name : `${r.speed_kmh} km/h`];
    if (r.loop) bits.push("loops");
    return `<li data-id="${esc(r.id)}"><span class="main"><span class="t">${esc(r.name)}</span><span class="d">${esc(bits.join(", "))}</span></span>
      <button class="btn small" data-act="play">Play</button><button class="x" data-act="del" title="Delete route" aria-label="Delete ${esc(r.name)}">×</button></li>`;
  }).join("");
  ul.querySelectorAll("li").forEach((li, i) => {
    const r = routes[i];
    li.onclick = () => loadIntoEditor(r);
    li.querySelector('[data-act="play"]').onclick = async (e) => {
      e.stopPropagation();
      const res = await act(() => api(`/api/routes/${encodeURIComponent(r.id)}/play`, { body: {} }));
      if (res) { route.active = res.path; draw(); toast(`Playing “${r.name}”: arrives ${clockIn(res.eta_s)}`); }
    };
    li.querySelector('[data-act="del"]').onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete the route “${r.name}”?`)) return;
      if (await act(() => api(`/api/routes/${encodeURIComponent(r.id)}`, { method: "DELETE" }))) loadSavedRoutes();
    };
  });
}

function loadIntoEditor(r) {
  route.track = null;
  route.stops = r.stops.map((s) => ({ ...s }));
  $("#route-loop").checked = !!r.loop;
  opts.roads = r.follow_roads !== false;
  opts.vary = r.vary !== false;
  $("#route-roads").checked = opts.roads;
  $("#route-vary").checked = opts.vary;
  bus.emit("set-speed", r.speed_kmh);
  if (route.stops.length) map.fitBounds(L.latLngBounds(route.stops.map((s) => [s.lat, s.lon])).pad(0.2));
  preview();
}

async function saveRoute() {
  const name = prompt("Name this route", "");
  if (!name || !name.trim()) return;
  const saved = await act(() => api("/api/routes", { body: { name: name.trim(), ...tripBody() } }));
  if (saved) { toast(`Saved “${saved.name}”`); loadSavedRoutes(); }
}

// ---------- GPX ----------
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

// ---------- wiring ----------
export function initRoute() {
  $("#route-roads").checked = opts.roads;
  $("#route-vary").checked = opts.vary;
  $("#route-roads").onchange = (e) => { opts.roads = e.target.checked; local.set("aigo.roads", opts.roads ? "1" : "0"); preview(); };
  $("#route-vary").onchange = (e) => { opts.vary = e.target.checked; local.set("aigo.vary", opts.vary ? "1" : "0"); };
  $("#route-loop").onchange = () => preview();
  $("#btn-route-start").onclick = startRoute;
  $("#btn-route-save").onclick = saveRoute;
  $("#btn-route-reset").onclick = clearRoute;
  $("#gpx-file").onchange = async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try { route.track = parseGpx(await file.text()); }
    catch (err) { return toast(err.message, "error"); }
    route.stops = [];
    bus.emit("goto-tab", "route");
    map.fitBounds(L.latLngBounds(route.track).pad(0.1));
    preview();
    toast(`Imported ${route.track.length} points from ${file.name}. The route starts at the track's first point.`);
  };
  bus.on("speed", () => (opts.roads && route.stops.length ? schedulePreview(0) : draw()));
  bus.on("tab", (tab) => (tab === "route" ? schedulePreview(0) : draw()));
  bus.on("home", () => (route.stops.length ? schedulePreview(0) : draw()));
  let wasRunning = false, lastPos = "";
  bus.on("status", (s) => {
    const isRunning = s.mode === "route";
    if (wasRunning && !isRunning) route.active = null;
    wasRunning = isRunning;
    const pos = s.position ? `${s.position.lat.toFixed(5)},${s.position.lon.toFixed(5)}` : "";
    if (pos !== lastPos) {
      lastPos = pos;
      // The plan starts where the phone is, so re-plan when it moves (not while a route drives it).
      if (!isRunning && state.tab === "route" && route.stops.length) return schedulePreview(800);
    }
    draw();
  });
  loadSavedRoutes();
}
