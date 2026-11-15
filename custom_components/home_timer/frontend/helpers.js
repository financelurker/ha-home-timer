// Home Timer panel: texts, durations, routes and live state (no rendering here).

const cache = {};

async function load(lang) {
  if (!cache[lang]) {
    cache[lang] = fetch(new URL(`./i18n/${lang}.json`, import.meta.url)).then((r) => (r.ok ? r.json() : {}));
  }
  return cache[lang];
}

// t(key, {name: value}) in Home Assistant's language; missing keys fall back to English
export async function translator(language) {
  const lang = String(language || "en").split("-")[0];
  const en = await load("en");
  const own = lang === "en" ? en : await load(lang).catch(() => ({}));
  return (key, vars = {}) => {
    // "<key>_one" is the singular, used when vars.count is 1
    const one = vars.count === 1 ? own[`${key}_one`] ?? en[`${key}_one`] : undefined;
    const text = one ?? own[key] ?? en[key] ?? key;
    return text.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  };
}

// 600 → "10:00", 3725 → "1:02:05"
export function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const two = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(sec)}` : `${m}:${two(sec)}`;
}

// what is typed into a duration field: "9" = 9 min, "9:30", "1:05:00"; null if unreadable
export function parseDuration(text) {
  const raw = String(text ?? "").trim().replace(",", ".");
  if (!raw) return null;
  if (/^\d+(\.\d+)?$/.test(raw)) return Math.round(parseFloat(raw) * 60);
  const parts = raw.split(":");
  if (parts.length > 3 || parts.some((p) => !/^\d+$/.test(p))) return null;
  const nums = parts.map(Number);
  if (nums.slice(1).some((n) => n >= 60)) return null;
  return parts.length === 2 ? nums[0] * 60 + nums[1] : nums[0] * 3600 + nums[1] * 60 + nums[2];
}

// the panel's routes: "" | area/<entry> | area/<entry>/options | station/<entry>/<id|new>
export function parseRoute(path, search) {
  const parts = String(path || "").replace(/^\/+|\/+$/g, "").split("/").filter(Boolean);
  const fromCog = new URLSearchParams(search || "").get("config_entry");
  if (!parts.length && fromCog) return { page: "area", entry: fromCog };
  if (parts[0] === "area" && parts[1]) return { page: parts[2] === "options" ? "options" : "area", entry: parts[1] };
  if (parts[0] === "station" && parts[1] && parts[2]) return { page: "station", entry: parts[1], station: parts[2] };
  return { page: "home" };
}

// a station's live state from its entities: {status, name, seconds, kind: countdown|elapsed|overtime|paused}
export function stationLive(hass, station, now = Date.now()) {
  const state = station.entities?.timer ? hass.states[station.entities.timer] : null;
  const running = station.entities?.running ? hass.states[station.entities.running]?.state === "on" : false;
  const sensor = station.mode === "power" && station.power?.sensor ? hass.states[station.power.sensor] : null;
  const watts = sensor && !["unknown", "unavailable"].includes(sensor.state) ? Number(sensor.state) : null;
  const live = { status: state?.state || "unavailable", running, watts, unit: sensor?.attributes?.unit_of_measurement || "W" };
  if (!state) return live;
  const a = state.attributes;
  live.name = a.timer_name;
  if (state.state === "active" && a.ends_at) {
    live.kind = "countdown";
    live.seconds = (Date.parse(a.ends_at) - now) / 1000;
  } else if (state.state === "active" && a.started_at) {
    live.kind = "elapsed";
    live.seconds = (now - Date.parse(a.started_at)) / 1000;
  } else if (state.state === "ringing" && a.finished_at) {
    live.kind = "overtime";
    live.seconds = (now - Date.parse(a.finished_at)) / 1000;
  } else if (state.state === "paused") {
    live.kind = "paused";
    live.seconds = a.remaining;
  }
  return live;
}

// timers of the area without a station, from the area's Timers sensor
export function looseTimers(hass, area) {
  const state = area.entities?.timers ? hass.states[area.entities.timers] : null;
  return (state?.attributes?.timers || []).filter((t) => !t.station);
}

export const clone = (x) => JSON.parse(JSON.stringify(x));
export const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
export const tempId = () => `new-${Math.random().toString(36).slice(2, 10)}`;
