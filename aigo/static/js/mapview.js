// The map: themed tiles, the phone marker, route drawing, popups and the right-click menu.
import { $, esc, fmtCoord, state } from "./util.js";
import { placeName } from "./places.js";

export const actions = {}; // filled by main.js: teleport, travel, addStop, favorite

export const map = L.map("map", { zoomControl: false }).setView([39.5, -98.35], 4);
L.control.zoom({ position: "topright" }).addTo(map);

const dark = window.matchMedia("(prefers-color-scheme: dark)");
const OSM_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const ROUTE_ATTR = ' · Routes <a href="https://routing.openstreetmap.de/">FOSSGIS OSRM</a>';
// One tile source for both themes; dark mode darkens it with a CSS filter on the tile pane only
// (markers and routes keep their colors). No extra map service or API key needed.
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: OSM_ATTR + ROUTE_ATTR }).addTo(map);
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

// The phone's dot sits in its own pane above map pins, so Home or a stop never hides it.
map.createPane("phone").style.zIndex = 650;
const phoneHalo = L.circle([0, 0], { radius: 30, weight: 0, fillOpacity: 0.18, interactive: false, pane: "phone" });
const phoneDot = L.circleMarker([0, 0], { radius: 9, weight: 3, color: "#ffffff", fillOpacity: 1, pane: "phone" });
export const routeLine = L.polyline([], { weight: 5, opacity: 0.85, lineCap: "round", lineJoin: "round" }).addTo(map);
export const stopsLayer = L.layerGroup().addTo(map);

function applyTheme() {
  map.getContainer().classList.toggle("dark-map", dark.matches);
  const signal = cssVar("--signal") || "#2d3fe0";
  phoneDot.setStyle({ fillColor: signal });
  phoneHalo.setStyle({ fillColor: signal });
  routeLine.setStyle({ color: signal });
}
dark.addEventListener("change", applyTheme);
applyTheme();

let centered = false;
export function showPhone(pos, follow) {
  if (!pos) {
    phoneDot.remove();
    phoneHalo.remove();
    return;
  }
  const ll = [pos.lat, pos.lon];
  phoneDot.setLatLng(ll).addTo(map);
  phoneHalo.setLatLng(ll).addTo(map);
  if (!centered) { map.setView(ll, 15); centered = true; }
  else if (follow && !map.getBounds().pad(-0.2).contains(ll)) map.panTo(ll);
}

const HOUSE_SVG = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 7.5 8 2.5l6 5V14H9.5v-4h-3v4H2z" fill="currentColor"/></svg>';
const homeMarker = L.marker([0, 0], {
  icon: L.divIcon({ className: "", html: `<div class="home-pin">${HOUSE_SVG}</div>`, iconSize: [28, 28], iconAnchor: [14, 14] }),
  title: "Home",
  keyboard: false,
});
export function showHome(home) {
  if (home) homeMarker.setLatLng([home.lat, home.lon]).addTo(map);
  else homeMarker.remove();
}

export const pinIcon = (label) => L.divIcon({ className: "", html: `<div class="pin">${esc(label)}</div>`, iconSize: [24, 24], iconAnchor: [12, 12] });

// ---------- popup for a picked point ----------
const pickPopup = L.popup({ autoPan: true, maxWidth: 300 });
export function openPick(lat, lon, title = "") {
  const el = document.createElement("div");
  el.innerHTML = `
    <div class="t">${esc(title || "Dropped pin")}</div>
    <div class="coords">${fmtCoord(lat, lon)}</div>
    <div class="popup-actions">
      <button class="btn primary" data-act="teleport">Teleport here</button>
      <button class="btn" data-act="travel">Travel here</button>
      <button class="btn ghost" data-act="favorite" title="Save as favorite">Save</button>
    </div>`;
  el.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => {
    map.closePopup();
    actions[b.dataset.act](lat, lon, title);
  }));
  pickPopup.setLatLng([lat, lon]).setContent(el).openOn(map);
  if (!title) placeName(lat, lon).then((p) => { if (p && pickPopup.isOpen()) el.querySelector(".t").textContent = p.title; });
}

map.on("click", (e) => {
  closeCtx();
  const { lat } = e.latlng;
  const lon = ((e.latlng.lng + 540) % 360) - 180;
  if (state.tab === "route") actions.addStop(lat, lon);
  else openPick(lat, lon);
});

// ---------- right-click menu ----------
const ctx = $("#ctx");
const CTX_ITEMS = [
  ["teleport", "Teleport here"],
  ["travel", "Travel here"],
  ["addStop", "Add as route stop"],
  ["favorite", "Save as favorite"],
  ["setHome", "Set as home"],
  ["copy", "Copy coordinates"],
];

export function closeCtx() {
  if (!ctx.hidden) ctx.hidden = true;
}

map.on("contextmenu", (e) => {
  e.originalEvent.preventDefault();
  const { lat } = e.latlng;
  const lon = ((e.latlng.lng + 540) % 360) - 180;
  ctx.innerHTML = `<div class="head">${fmtCoord(lat, lon)}</div>` +
    CTX_ITEMS.map(([k, label]) => `<button role="menuitem" data-act="${k}">${label}</button>`).join("");
  ctx.querySelectorAll("button").forEach((b) => (b.onclick = async () => {
    closeCtx();
    if (b.dataset.act === "copy") {
      try { await navigator.clipboard.writeText(`${lat.toFixed(6)}, ${lon.toFixed(6)}`); actions.toastCopied(); }
      catch { /* clipboard blocked */ }
      return;
    }
    actions[b.dataset.act](lat, lon, "");
  }));
  ctx.hidden = false;
  const { clientX: x, clientY: y } = e.originalEvent;
  const w = ctx.offsetWidth, h = ctx.offsetHeight;
  ctx.style.left = `${Math.min(x, innerWidth - w - 8)}px`;
  ctx.style.top = `${Math.min(y, innerHeight - h - 8)}px`;
  ctx.querySelector("button").focus();
});
ctx.addEventListener("keydown", (e) => {
  const items = Array.from(ctx.querySelectorAll("button"));
  const i = items.indexOf(document.activeElement);
  if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
  if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
});
document.addEventListener("pointerdown", (e) => { if (!ctx.contains(e.target)) closeCtx(); });
map.on("movestart zoomstart", closeCtx);
