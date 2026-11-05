// Joystick tab: arrow keys / W A S D / on-screen pad move the phone from where it is.
import { $$, api, state, toast } from "./util.js";

const KEY_DIRS = { ArrowUp: "n", KeyW: "n", ArrowDown: "s", KeyS: "s", ArrowLeft: "w", KeyA: "w", ArrowRight: "e", KeyD: "e" };
const PAD_DIRS = { 0: "n", 90: "e", 180: "s", 270: "w" };
const TICK_MS = 500;
const held = new Set();
let timer = null;
let busy = false;

function bearing() {
  let x = 0, y = 0;
  if (held.has("n")) y += 1;
  if (held.has("s")) y -= 1;
  if (held.has("e")) x += 1;
  if (held.has("w")) x -= 1;
  if (!x && !y) return null;
  return ((Math.atan2(x, y) * 180) / Math.PI + 360) % 360;
}

async function tick() {
  const b = bearing();
  if (b === null) return stop();
  if (busy) return;
  busy = true;
  try {
    await api("/api/step", { body: { bearing: b, meters: (state.speed / 3.6) * (TICK_MS / 1000) } });
  } catch (e) {
    toast(e.message, "error");
    held.clear();
    stop();
  } finally {
    busy = false;
  }
}

function start() {
  if (!timer) {
    tick();
    timer = setInterval(tick, TICK_MS);
  }
}

export function stop() {
  clearInterval(timer);
  timer = null;
  $$(".pad button").forEach((b) => b.classList.remove("held"));
}

const sync = () => (held.size ? start() : stop());

export function initJoystick() {
  document.addEventListener("keydown", (e) => {
    if (state.tab !== "joystick" || e.target.matches("input, textarea, select")) return;
    const d = KEY_DIRS[e.code];
    if (!d) return;
    e.preventDefault();
    held.add(d);
    sync();
  });
  document.addEventListener("keyup", (e) => {
    const d = KEY_DIRS[e.code];
    if (d) { held.delete(d); sync(); }
  });
  window.addEventListener("blur", () => { held.clear(); stop(); });
  $$(".pad button").forEach((b) => {
    const d = PAD_DIRS[b.dataset.dir];
    const down = (e) => { e.preventDefault(); b.classList.add("held"); held.add(d); sync(); };
    const up = () => { b.classList.remove("held"); held.delete(d); sync(); };
    b.addEventListener("pointerdown", down);
    b.addEventListener("pointerup", up);
    b.addEventListener("pointerleave", up);
    b.addEventListener("pointercancel", up);
  });
}
