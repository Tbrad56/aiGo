// Shared helpers, app state and a tiny event bus.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
export const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const fmtCoord = (lat, lon) => `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
export const km = (m) => (m >= 1000 ? `${(m / 1000).toFixed(m >= 10000 ? 0 : 1)} km` : `${Math.round(m)} m`);
export function dur(s) {
  if (!isFinite(s)) return "—";
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${m} min` : m ? `${m} min` : `${s} s`;
}
export const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
export const clockIn = (secs) =>
  new Date(Date.now() + secs * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

export function haversine(a, b) {
  const R = 6371008.8, r = Math.PI / 180;
  const dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}
export function pathLength(pts) {
  let d = 0;
  for (let i = 1; i < pts.length; i++) d += haversine(pts[i - 1], pts[i]);
  return d;
}

export const local = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode etc. */ } },
};

export const SPEEDS = [
  { name: "Walk", kmh: 5 },
  { name: "Run", kmh: 10 },
  { name: "Bike", kmh: 18 },
  { name: "Drive", kmh: 50 },
];
export const moveWord = (kmh) => (kmh <= 6 ? "Walking" : kmh <= 12 ? "Running" : kmh <= 25 ? "Biking" : "Driving");
export const nearestSpeed = (kmh) =>
  SPEEDS.reduce((best, s) => (Math.abs(s.kmh - kmh) < Math.abs(best.kmh - kmh) ? s : best), SPEEDS[0]);

export const state = {
  status: null,
  tab: "teleport",
  speed: Number(local.get("aigo.speed")) || 5,
  favorites: [],
  routes: [],
};

const handlers = {};
export const bus = {
  on(evt, fn) { (handlers[evt] ||= []).push(fn); },
  emit(evt, data) { (handlers[evt] || []).forEach((fn) => fn(data)); },
};

export async function api(path, opts = {}) {
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

let toastTimer;
export function toast(msg, kind = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = kind;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), kind ? 7000 : 3500);
}

export async function act(fn) {
  try { return await fn(); } catch (e) { toast(e.message, "error"); return undefined; }
}

export function setSpeed(kmh) {
  state.speed = kmh;
  local.set("aigo.speed", kmh);
  bus.emit("speed", kmh);
}
