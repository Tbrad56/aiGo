// Schedule tab: timed moves that run inside aiGo (even with this page closed).
import { $, act, api, bus, esc, state, toast, SPEEDS } from "./util.js";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function daysText(days) {
  const d = [...days].sort();
  if (d.length === 7) return "Every day";
  if (d.join() === "0,1,2,3,4") return "Weekdays";
  if (d.join() === "5,6") return "Weekends";
  return d.map((i) => DAYS[i]).join(", ");
}

function timeText(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return new Date(2000, 0, 1, h, m).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function actionText(e) {
  const fav = state.favorites.find((f) => f.id === e.favorite_id);
  const rt = state.routes.find((r) => r.id === e.route_id);
  const missing = "(deleted)";
  switch (e.action) {
    case "teleport": return `Teleport to ${fav ? fav.name : missing}`;
    case "travel": {
      const how = (SPEEDS.find((s) => s.kmh === e.speed_kmh) || { name: "Drive" }).name;
      return `${how} to ${fav ? fav.name : missing}`;
    }
    case "route": return `Play route ${rt ? rt.name : missing}`;
    case "stop": return "Stop spoofing";
    default: return e.action;
  }
}

function lastRunText(r) {
  if (!r) return "";
  const when = new Date(r.at).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
  return `${r.ok ? "Ran" : "Failed"} ${when}: ${r.message}`;
}

export async function loadSchedules() {
  const list = await act(() => api("/api/schedules"));
  if (!list) return;
  const ul = $("#schedules");
  if (!list.length) {
    ul.innerHTML = '<li class="empty-sched">No schedules yet. Add one below, for example “drive to Work at 9:00 on weekdays”.</li>';
    return;
  }
  list.sort((a, b) => a.time.localeCompare(b.time));
  ul.innerHTML = list.map((e) => `
    <li class="${e.enabled ? "" : "off"}">
      <span class="time">${esc(timeText(e.time))}</span>
      <span class="t">${esc(e.name || actionText(e))}</span>
      <span class="acts">
        <label class="switch" title="${e.enabled ? "On" : "Off"}"><input type="checkbox" ${e.enabled ? "checked" : ""} aria-label="Schedule on"><span></span></label>
        <button class="btn small ghost" data-act="run" title="Run it now">Run now</button>
        <button class="x" data-act="del" title="Delete schedule" aria-label="Delete schedule">×</button>
      </span>
      <span class="d">${esc(daysText(e.days))}${e.name ? `, ${esc(actionText(e))}` : ""}</span>
      ${e.last_run ? `<span class="last ${e.last_run.ok ? "" : "bad"}">${esc(lastRunText(e.last_run))}</span>` : ""}
    </li>`).join("");
  ul.querySelectorAll("li").forEach((li, i) => {
    const e = list[i];
    const body = { name: e.name, enabled: e.enabled, time: e.time, days: e.days, action: e.action,
                   favorite_id: e.favorite_id, route_id: e.route_id, speed_kmh: e.speed_kmh };
    li.querySelector(".switch input").onchange = async (ev) => {
      await act(() => api(`/api/schedules/${encodeURIComponent(e.id)}`, { method: "PUT", body: { ...body, enabled: ev.target.checked } }));
      loadSchedules();
    };
    li.querySelector('[data-act="run"]').onclick = async () => {
      const r = await act(() => api(`/api/schedules/${encodeURIComponent(e.id)}/run`, { body: {} }));
      if (r) loadSchedules();
    };
    li.querySelector('[data-act="del"]').onclick = async () => {
      if (!confirm("Delete this schedule?")) return;
      if (await act(() => api(`/api/schedules/${encodeURIComponent(e.id)}`, { method: "DELETE" }))) loadSchedules();
    };
  });
}

function fillTargets() {
  const action = $("#sf-action").value;
  const sel = $("#sf-target");
  $("#sf-target-wrap").hidden = action === "stop";
  $("#sf-speed-wrap").hidden = action !== "travel";
  const items = action === "route" ? state.routes : state.favorites;
  const prev = sel.value;
  if (!items.length) {
    const what = action === "route" ? "Save a route on the Route tab first" : "Save a favorite on the Places tab first";
    sel.innerHTML = `<option value="">${what}</option>`;
    sel.disabled = true;
    return;
  }
  sel.disabled = false;
  sel.innerHTML = items.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("");
  if (items.some((x) => x.id === prev)) sel.value = prev;
}

export function initSchedule() {
  $("#sf-days").innerHTML = DAYS.map((d, i) =>
    `<label><input type="checkbox" value="${i}" ${i < 5 ? "checked" : ""}><span>${d.slice(0, 2)}</span></label>`).join("");
  $("#sf-action").onchange = fillTargets;
  bus.on("favorites", () => { fillTargets(); loadSchedules(); });
  bus.on("routes", () => { fillTargets(); loadSchedules(); });
  bus.on("event", () => loadSchedules()); // a schedule just ran: refresh its "Ran ..." line
  fillTargets();
  $("#sched-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const action = $("#sf-action").value;
    const days = Array.from(document.querySelectorAll("#sf-days input:checked")).map((c) => Number(c.value));
    if (!days.length) return toast("Pick at least one day.", "warn");
    const target = $("#sf-target").value;
    if (action !== "stop" && !target) return toast(`${$("#sf-target").options[0].text}.`, "warn");
    const body = {
      name: $("#sf-name").value.trim(),
      time: $("#sf-time").value,
      days,
      action,
      favorite_id: action === "teleport" || action === "travel" ? target : null,
      route_id: action === "route" ? target : null,
      speed_kmh: action === "travel" ? Number($("#sf-speed").value) : null,
    };
    const saved = await act(() => api("/api/schedules", { body }));
    if (!saved) return;
    toast(`Scheduled for ${timeText(saved.time)}, ${daysText(saved.days).toLowerCase()}.`);
    $("#sf-name").value = "";
    $("#sched-form-wrap").open = false;
    loadSchedules();
  };
  loadSchedules();
}
