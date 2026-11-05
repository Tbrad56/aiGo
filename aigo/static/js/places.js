// Place names (OpenStreetMap Nominatim reverse lookup) and search-as-you-type (Photon).
// Only coordinates the user picks or that the phone is set to are ever sent.
import { local } from "./util.js";

const NOMINATIM = "https://nominatim.openstreetmap.org/reverse";
const PHOTON = "https://photon.komoot.io/api/";
const CACHE_KEY = "aigo.places";
const CACHE_MAX = 400;

const cache = new Map(JSON.parse(local.get(CACHE_KEY) || "[]"));
const pending = new Map();
let queue = Promise.resolve();
let lastCall = 0;

const keyOf = (lat, lon) => `${lat.toFixed(4)},${lon.toFixed(4)}`; // ~11 m grid
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function saveCache() {
  const entries = Array.from(cache.entries()).slice(-CACHE_MAX);
  local.set(CACHE_KEY, JSON.stringify(entries));
}

// Address keys that name a landmark or venue (e.g. "commercial": "Times Square"), best first.
const LANDMARK_KEYS = ["tourism", "attraction", "historic", "leisure", "amenity", "aeroway", "railway",
  "university", "college", "school", "hospital", "shop", "commercial", "retail", "office", "building", "man_made"];
const ADMIN_NOISE = /community board|ward \d|precinct|census/i;

function describe(r) {
  const a = r.address || {};
  const street = [a.house_number, a.road].filter(Boolean).join(" ");
  const landmark = LANDMARK_KEYS.map((k) => a[k]).find((v) => v && v !== "yes");
  const title = landmark || r.name || street || a.quarter || a.neighbourhood || a.suburb || a.hamlet
    || a.village || a.town || a.city || (r.display_name || "").split(",")[0] || "Unnamed place";
  const area = [a.suburb, a.quarter, a.neighbourhood, a.city || a.town || a.village || a.county, a.state]
    .filter((x, i, arr) => x && x !== title && !ADMIN_NOISE.test(x) && arr.indexOf(x) === i)
    .slice(0, 2)
    .join(", ");
  return { title, detail: area };
}

/** Resolve a friendly name for a point. Cached; at most one network call per second. */
export function placeName(lat, lon) {
  const key = keyOf(lat, lon);
  if (cache.has(key)) return Promise.resolve(cache.get(key));
  if (pending.has(key)) return pending.get(key);
  const p = (queue = queue.then(async () => {
    const wait = 1100 - (Date.now() - lastCall);
    if (wait > 0) await sleep(wait);
    lastCall = Date.now();
    try {
      const url = `${NOMINATIM}?format=jsonv2&zoom=18&addressdetails=1&lat=${lat}&lon=${lon}`;
      const res = await fetch(url, { headers: { "Accept-Language": navigator.language || "en" } });
      const data = await res.json();
      if (data.error) return null;
      const place = describe(data);
      cache.set(key, place);
      saveCache();
      return place;
    } catch {
      return null;
    } finally {
      pending.delete(key);
    }
  }));
  pending.set(key, p);
  return p;
}

export const cachedPlace = (lat, lon) => cache.get(keyOf(lat, lon)) || null;

/** Suggestions for a partly typed place name, biased toward `near` ([lat, lon]). */
export async function suggest(q, near, signal) {
  const bias = near ? `&lat=${near[0].toFixed(4)}&lon=${near[1].toFixed(4)}` : "";
  const res = await fetch(`${PHOTON}?q=${encodeURIComponent(q)}&limit=6&lang=en${bias}`, { signal });
  const data = await res.json();
  return (data.features || []).map((f) => {
    const p = f.properties || {};
    const street = [p.housenumber, p.street].filter(Boolean).join(" ");
    const title = p.name || street || p.city || "Unnamed place";
    const detail = [p.name && street ? street : null, p.city || p.district || p.county, p.state, p.country]
      .filter((x, i, arr) => x && x !== title && arr.indexOf(x) === i)
      .slice(0, 3)
      .join(", ");
    const [lon, lat] = f.geometry.coordinates;
    return { title, detail, lat, lon };
  });
}
