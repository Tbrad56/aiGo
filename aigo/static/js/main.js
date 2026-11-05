// aiGo page: status band, connection, panel, search, places, shortcuts. Wires the other modules.
import {
  $, $$, act, api, bus, clockIn, esc, fmtCoord, haversine, km, local, mmss, moveWord, nearestSpeed, setSpeed, state, toast,
  SPEEDS,
} from "./util.js";
import { placeName, cachedPlace, suggest } from "./places.js";
import { actions, closeCtx, map, openPick, showHome, showPhone } from "./mapview.js";
import { addStop, draw as drawRoute, initRoute, travelTo } from "./route.js";
import { initSchedule } from "./schedule.js";
import { initJoystick, stop as stopJoystick } from "./joystick.js";

// ---------- actions shared with the map ----------
async function teleport(lat, lon) {
  const res = await act(() => api("/api/teleport", { body: { lat, lon } }));
  if (!res) return;
  if (res.warning) toast(res.warning, "warn");
  loadHistory();
}

async function saveFavorite(lat, lon, suggested = "") {
  const guess = suggested || (cachedPlace(lat, lon) || {}).title || "";
  const name = prompt("Name this place", guess);
  if (name === null) return;
  const fav = await act(() => api("/api/favorites", { body: { name, lat, lon } }));
  if (fav) { toast(`Saved “${fav.name}”`); loadFavorites(); }
}

Object.assign(actions, {
  teleport,
  travel: travelTo,
  addStop: (lat, lon) => { setTab("route"); addStop(lat, lon); },
  favorite: saveFavorite,
  setHome,
  toastCopied: () => toast("Coordinates copied."),
});

// ---------- tabs ----------
function setTab(tab) {
  state.tab = tab;
  local.set("aigo.tab", tab);
  $$(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === tab)));
  $$(".tab-body").forEach((b) => (b.hidden = b.dataset.body !== tab));
  map.getContainer().style.cursor = tab === "route" ? "crosshair" : "";
  if (tab !== "joystick") stopJoystick();
  bus.emit("tab", tab);
}
$$(".tab").forEach((t) => (t.onclick = () => setTab(t.dataset.tab)));
bus.on("goto-tab", setTab);

// ---------- speed pickers (route + joystick tabs) ----------
function renderSpeeds() {
  $$("[data-speed-group]").forEach((g) => {
    g.innerHTML = SPEEDS.map((s) =>
      `<button type="button" data-kmh="${s.kmh}" class="${s.kmh === state.speed ? "active" : ""}" aria-pressed="${s.kmh === state.speed}"><b>${s.name}</b>${s.kmh} km/h</button>`).join("");
    g.querySelectorAll("button").forEach((b) => (b.onclick = () => setSpeed(Number(b.dataset.kmh))));
  });
}
bus.on("speed", renderSpeeds);
bus.on("set-speed", (kmh) => setSpeed(kmh));

// ---------- status band ----------
let placeTimer = null;
let placeDue = 0;        // when the scheduled lookup fires
let lastPlaceLookup = 0;

function wantPlace(pos, inTrip) {
  const cached = cachedPlace(pos.lat, pos.lon);
  if (cached) return cached;
  // On a trip, refresh at most every 30 s, but soon if there's no name to show at all.
  const delay = inTrip && shownPlace ? Math.max(0, 30000 - (Date.now() - lastPlaceLookup)) : 1200;
  // Status updates arrive every second while moving: never push a scheduled lookup back (it would
  // never fire). Only bring it forward when this request needs a name sooner.
  if (placeTimer && Date.now() + delay >= placeDue) return null;
  clearTimeout(placeTimer);
  placeDue = Date.now() + delay;
  placeTimer = setTimeout(async () => {
    placeTimer = null;
    const s = state.status;
    if (!s || !s.position) return;
    const { lat, lon } = s.position; // where the phone is now, not when this was scheduled
    lastPlaceLookup = Date.now();
    const found = await placeName(lat, lon);
    if (found) shownPlace = { ...found, lat, lon };
    if (state.status) renderBand(state.status);
  }, delay);
  return null;
}

let shownPlace = null; // {title, detail, lat, lon}: the name only stays while the phone is near it
const NAME_RADIUS_M = 150;       // a held spot: a new name once the phone moves a block
const TRIP_NAME_RADIUS_M = 400;  // on a trip: ~30 s of driving between name refreshes
function renderBand(s) {
  const band = $("#band");
  const r = s.route;
  const st = s.mode === "route" && r ? "trip" : s.position ? "spoof" : "real";
  band.dataset.state = st;
  $("#btn-expand").dataset.band = st;

  if (st === "trip" && !s.position) {
    // A trip from real GPS: the first position (Home) is a moment away.
    $("#band-title").textContent = "Starting trip…";
    $("#band-detail").textContent = "";
    $("#mini-text").textContent = "Starting trip…";
  } else if (st === "real") {
    shownPlace = null;
    $("#band-tag").textContent = "Real GPS";
    $("#band-title").textContent = "Your real location";
    $("#band-detail").textContent = "Your phone reports where it really is.";
    $("#mini-text").textContent = "Real GPS";
  } else {
    const place = wantPlace(s.position, st === "trip");
    if (place) shownPlace = { ...place, lat: s.position.lat, lon: s.position.lon };
    else if (shownPlace && haversine([shownPlace.lat, shownPlace.lon], [s.position.lat, s.position.lon])
             > (st === "trip" ? TRIP_NAME_RADIUS_M : NAME_RADIUS_M)) {
      shownPlace = null; // moved away: don't show the old name with new coordinates
    }
    const title = shownPlace ? shownPlace.title : fmtCoord(s.position.lat, s.position.lon);
    $("#band-title").textContent = title;
    $("#band-detail").textContent = shownPlace ? shownPlace.detail : "Finding the place name…";
    $("#band-coords").textContent = fmtCoord(s.position.lat, s.position.lon);
    $("#mini-text").textContent = st === "trip" ? `${moveWord(r.speed_kmh)} · ${title}` : `Spoofing · ${title}`;
    if (st === "spoof") $("#band-tag").textContent = "Spoofing";
  }

  $("#band-foot").hidden = st !== "spoof";
  $("#band-trip").hidden = st !== "trip";
  if (st === "trip") {
    const tag = r.paused ? "Paused" : r.waiting_s > 0 ? "Waiting at a stop" : moveWord(r.speed_kmh);
    $("#band-tag").textContent = tag;
    $("#trip-bar").style.width = `${r.total_m ? Math.min(100, (r.done_m / r.total_m) * 100) : 0}%`;
    const stopBit = r.legs > 1 ? `stop ${r.leg} of ${r.legs}` : "";
    const line = r.waiting_s > 0
      ? `${mmss(r.waiting_s)} left at ${stopBit || "the stop"}, arrives ${clockIn(r.eta_s)}`
      : `${km(r.done_m)} of ${km(r.total_m)}${stopBit ? `, ${stopBit}` : ""}, arrives ${clockIn(r.eta_s)}`;
    $("#trip-line").textContent = r.loop ? `${line} (lap ${r.lap})` : line;
    $("#btn-pause").textContent = r.paused ? "Resume" : "Pause";
    const active = nearestSpeed(r.speed_kmh).kmh;
    $$("#trip-speeds button").forEach((b) => b.classList.toggle("active", Number(b.dataset.kmh) === active));
  }
}

$("#trip-speeds").innerHTML = SPEEDS.map((s) => `<button type="button" data-kmh="${s.kmh}" title="${s.kmh} km/h">${s.name}</button>`).join("");
$$("#trip-speeds button").forEach((b) => (b.onclick = () => act(() => api("/api/route/speed", { body: { speed_kmh: Number(b.dataset.kmh) } }))));
const togglePause = () => {
  const r = state.status && state.status.route;
  if (!r) return;
  act(() => api(r.paused ? "/api/route/resume" : "/api/route/pause", { body: {} }));
};
$("#btn-pause").onclick = togglePause;
const stopTrip = () => act(() => api("/api/route/stop", { body: {} }));
$("#btn-trip-stop").onclick = stopTrip;
$("#btn-clear").onclick = async () => {
  if (await act(() => api("/api/clear", { body: {} }))) toast("Stopped. Your phone is back on its real GPS.");
};

// ---------- connection ----------
function renderConn(s) {
  const dev = s.device || {};
  const pill = $("#conn");
  pill.dataset.state = s.connection;
  $("#btn-expand").dataset.state = s.connection;
  const via = dev.transport && dev.transport !== "pretend" ? ` · ${dev.transport}` : "";
  const label = {
    connected: `${dev.name || "iPhone"}${dev.ios ? ` · iOS ${dev.ios}` : ""}${via}`,
    connecting: "Connecting…",
    error: "Not connected",
    disconnected: "Not connected",
  }[s.connection];
  $("#conn-text").textContent = label;
  pill.title = label;
  const showErr = s.connection === "error" || (s.error && s.connection !== "connecting");
  $("#conn-error").hidden = !showErr;
  $("#conn-error-text").textContent = s.error || "";
}
$("#btn-reconnect").onclick = () => act(() => api("/api/connect", { body: {} }));

let lastEventId = null;
function onStatus(s) {
  state.status = s;
  renderConn(s);
  renderBand(s);
  showPhone(s.position, s.mode === "route");
  if (s.event && lastEventId !== null && s.event.id !== lastEventId) {
    toast(s.event.text, s.event.ok ? "" : "error");
    bus.emit("event", s.event);
  }
  lastEventId = s.event ? s.event.id : 0;
  bus.emit("status", s);
}

let wsDelay = 500;
function connectWs() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => (wsDelay = 500);
  ws.onmessage = (e) => onStatus(JSON.parse(e.data));
  ws.onclose = () => {
    $("#conn").dataset.state = "error";
    $("#conn-text").textContent = "aiGo isn't running";
    setTimeout(connectWs, wsDelay);
    wsDelay = Math.min(wsDelay * 2, 8000);
  };
}

// ---------- collapsible panel ----------
const narrow = window.matchMedia("(max-width: 720px)");
function setCollapsed(on) {
  const panel = $("#panel");
  panel.classList.toggle("collapsed", on);
  $("#btn-collapse").setAttribute("aria-expanded", String(!on));
  $("#btn-collapse").title = on ? "Show panel" : "Hide panel";
  $("#btn-expand").hidden = !on || narrow.matches;
  local.set("aigo.collapsed", on ? "1" : "0");
  setTimeout(() => map.invalidateSize(), 250);
}
$("#btn-collapse").onclick = () => setCollapsed(!$("#panel").classList.contains("collapsed"));
$("#btn-expand").onclick = () => { setCollapsed(false); $("#btn-collapse").focus(); };
narrow.addEventListener("change", () => setCollapsed($("#panel").classList.contains("collapsed")));

// ---------- search as you type ----------
const COORD_RE = /^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/;
const searchBox = $("#search");
const results = $("#search-results");
let found = [];
let selected = -1;
let searchTimer = null;
let searchAbort = null;

function showResults(items, emptyText = "") {
  found = items;
  selected = items.length ? 0 : -1;
  results.innerHTML = items.length
    ? items.map((r, i) => `<li role="option" id="sr-${i}" aria-selected="${i === selected}"><div class="t">${esc(r.title)}</div>${r.detail ? `<div class="d">${esc(r.detail)}</div>` : ""}</li>`).join("")
    : `<li class="empty">${esc(emptyText)}</li>`;
  results.hidden = !items.length && !emptyText;
  searchBox.setAttribute("aria-expanded", String(!results.hidden));
  results.querySelectorAll("li[role=option]").forEach((li, i) => (li.onmousedown = (e) => { e.preventDefault(); pick(i); }));
}
function hideResults() {
  results.hidden = true;
  searchBox.setAttribute("aria-expanded", "false");
}
function moveSel(d) {
  if (!found.length) return;
  selected = (selected + d + found.length) % found.length;
  results.querySelectorAll("li[role=option]").forEach((li, i) => li.setAttribute("aria-selected", String(i === selected)));
  searchBox.setAttribute("aria-activedescendant", `sr-${selected}`);
}
function pick(i) {
  const r = found[i];
  if (!r) return;
  hideResults();
  searchBox.blur();
  map.setView([r.lat, r.lon], Math.max(map.getZoom(), 16));
  openPick(r.lat, r.lon, r.coords ? "" : r.title);
}

searchBox.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = searchBox.value.trim();
  const m = q.match(COORD_RE);
  if (m) {
    const lat = Number(m[1]), lon = Number(m[2]);
    if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return showResults([], "Those coordinates are out of range.");
    return showResults([{ title: `Go to ${fmtCoord(lat, lon)}`, detail: "Coordinates", lat, lon, coords: true }]);
  }
  if (q.length < 3) return hideResults();
  searchTimer = setTimeout(async () => {
    if (searchAbort) searchAbort.abort();
    searchAbort = new AbortController();
    const near = state.status && state.status.position
      ? [state.status.position.lat, state.status.position.lon]
      : [map.getCenter().lat, map.getCenter().lng];
    try {
      const items = await suggest(q, near, searchAbort.signal);
      if (searchBox.value.trim() === q) showResults(items, "No matches.");
    } catch (e) {
      if (e.name !== "AbortError") showResults([], "Search isn't reachable. Check your internet connection.");
    }
  }, 250);
});
searchBox.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); moveSel(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); moveSel(-1); }
  else if (e.key === "Enter") { e.preventDefault(); pick(Math.max(selected, 0)); }
  else if (e.key === "Escape") { hideResults(); searchBox.blur(); }
});
searchBox.addEventListener("blur", () => setTimeout(hideResults, 120));

// ---------- favorites & recent ----------
function itemHtml(title, detail, del) {
  return `<span class="main"><span class="t">${esc(title)}</span>${detail ? `<span class="d">${esc(detail)}</span>` : ""}</span>${del ? `<button class="x" title="Delete" aria-label="Delete ${esc(title)}">×</button>` : ""}`;
}

async function loadFavorites() {
  const favs = await act(() => api("/api/favorites"));
  if (!favs) return;
  state.favorites = favs;
  bus.emit("favorites", favs);
  const ul = $("#favorites");
  if (!favs.length) {
    ul.innerHTML = '<li class="empty">Right-click the map and choose Save as favorite.</li>';
    return;
  }
  ul.innerHTML = favs.map((f) => `<li>${itemHtml(f.name, fmtCoord(f.lat, f.lon), true)}</li>`).join("");
  ul.querySelectorAll("li").forEach((li, i) => {
    const f = favs[i];
    li.onclick = () => { map.setView([f.lat, f.lon], Math.max(map.getZoom(), 15)); openPick(f.lat, f.lon, f.name); };
    li.querySelector(".x").onclick = async (e) => {
      e.stopPropagation();
      if (await act(() => api(`/api/favorites/${encodeURIComponent(f.id)}`, { method: "DELETE" }))) loadFavorites();
    };
  });
}

function ago(at) {
  const s = Date.now() / 1000 - at;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(at * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
}

async function loadHistory() {
  const hist = await act(() => api("/api/history"));
  if (!hist) return;
  const items = hist.slice(0, 8);
  const ul = $("#history");
  if (!items.length) {
    ul.innerHTML = '<li class="empty">Places you teleport to show up here.</li>';
    return;
  }
  const render = () => {
    ul.innerHTML = items.map((h) => {
      const p = cachedPlace(h.lat, h.lon);
      return `<li>${itemHtml(p ? p.title : fmtCoord(h.lat, h.lon), p ? `${p.detail ? `${p.detail}, ` : ""}${ago(h.at)}` : ago(h.at))}</li>`;
    }).join("");
    ul.querySelectorAll("li").forEach((li, i) => {
      const h = items[i];
      li.onclick = () => { map.setView([h.lat, h.lon], Math.max(map.getZoom(), 15)); openPick(h.lat, h.lon); };
    });
  };
  render();
  items.forEach((h) => { if (!cachedPlace(h.lat, h.lon)) placeName(h.lat, h.lon).then((p) => p && render()); });
}
$("#btn-save-fav").onclick = () => {
  const p = state.status && state.status.position;
  if (!p) return toast("Teleport somewhere first.", "warn");
  saveFavorite(p.lat, p.lon);
};

// ---------- home ----------
let home = null;

async function setHome(lat, lon) {
  const name = (cachedPlace(lat, lon) || {}).title || "Home";
  const res = await act(() => api("/api/home", { method: "PUT", body: { lat, lon, name } }));
  if (res) { renderHome(res); toast("Home set. Trips now start there."); }
}

function renderHome(h) {
  home = h.home;
  showHome(home);
  $("#home-start").checked = h.start_from_home;
  $("#home-start").disabled = !home;
  const card = $("#home-card");
  if (!home) {
    card.className = "home-card empty";
    card.textContent = "Right-click your house on the map and choose Set as home. Trips will start there.";
  } else {
    card.className = "home-card";
    card.innerHTML = `<span class="main"><span class="t">${esc(home.name)}</span><span class="d">${fmtCoord(home.lat, home.lon)}</span></span>
      <button class="btn small" data-act="go">Go home</button><button class="x" data-act="clear" title="Clear home" aria-label="Clear home">×</button>`;
    card.querySelector('[data-act="go"]').onclick = () => { map.setView([home.lat, home.lon], Math.max(map.getZoom(), 15)); teleport(home.lat, home.lon); };
    card.querySelector('[data-act="clear"]').onclick = async () => {
      if (!confirm("Clear your home location?")) return;
      const res = await act(() => api("/api/home", { method: "DELETE" }));
      if (res) renderHome(res);
    };
  }
  bus.emit("home", home);
}

async function loadHome() {
  const res = await act(() => api("/api/home"));
  if (res) renderHome(res);
}
$("#home-start").onchange = async (e) => {
  const res = await act(() => api("/api/home/settings", { method: "PUT", body: { start_from_home: e.target.checked } }));
  if (res) renderHome(res);
};

// ---------- keyboard shortcuts ----------
document.addEventListener("keydown", (e) => {
  const typing = e.target.matches("input, textarea, select, [contenteditable]");
  if (e.key === "/" && !typing) { e.preventDefault(); setCollapsed(false); searchBox.focus(); return; }
  if (e.key === "Escape") {
    closeCtx();
    map.closePopup();
    if (!typing && state.status && state.status.mode === "route") stopTrip();
    return;
  }
  if (e.code === "Space" && !typing && !e.target.matches("button, summary, a") && state.status && state.status.route) {
    e.preventDefault();
    togglePause();
  }
});

// ---------- boot ----------
renderSpeeds();
initRoute();
initJoystick();
initSchedule();
setTab(local.get("aigo.tab") || "teleport");
setCollapsed(local.get("aigo.collapsed") === "1");
loadHome();
loadFavorites();
loadHistory();
connectWs();
drawRoute();
