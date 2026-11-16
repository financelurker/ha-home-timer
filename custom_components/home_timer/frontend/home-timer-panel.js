// Home Timer: the configuration panel.
// Areas on the left, the chosen area or station on the right (on a phone one at a
// time). Editors work on a draft until saved: one bar saves or discards it, because
// saving reloads the area. Live state comes from the entities, not from the panel's
// own data. The checks are the integration's (panel/edit.py); errors show at the field.

import { LitElement, html, svg, css, nothing } from "./vendor/lit.js";
import { clone, formatDuration, looseTimers, parseDuration, parseRoute, same, stationLive, tempId, translator } from "./helpers.js";
import { MIN_VIEW, chartModel, overviewModel, renderChart, renderOverview, secondsAt, valueAt, zoomView } from "./runs.js";

const PANEL = "home-timer";
const WS = "home_timer";
const DOCS = "https://github.com/financelurker/ha-home-timer/blob/main/docs/panel.md";
const FOLD = "home_timer.fold"; // what is folded on a phone, remembered per device

function loadFold() {
  try {
    return JSON.parse(localStorage.getItem(FOLD) || "{}") || {};
  } catch {
    return {};
  }
}

// the done rule as a list of steps (one step is stored as the plain rule)
const STEP_KEYS = ["phase", "repeat", "optional"]; // reserved per step for phases (later): kept as they are
const keepKeys = (step) => Object.fromEntries(STEP_KEYS.filter((k) => step && k in step).map((k) => [k, step[k]]));
const doneSteps = (done) => (done?.steps?.length ? done.steps : [done || { type: "off" }]);

class HomeTimerPanel extends LitElement {
  static properties = {
    hass: { attribute: false },
    narrow: { type: Boolean, reflect: true },
    route: { attribute: false },
    panel: { attribute: false },
    _config: { state: true },
    _error: { state: true },
    _notice: { state: true },
    _dialog: { state: true },
    _saving: { state: true },
    _now: { state: true },
    _drafts: { state: true },
    _errors: { state: true },
    _menu: { state: true },
    _open: { state: true },
    _runs: { state: true },
    _run: { state: true },
    _hover: { state: true },
    _chartWidth: { state: true },
    _zoom: { state: true },
    _yFull: { state: true },
    _reread: { state: true },
    _confirm: { state: true },
    _fold: { state: true },
    _preview: { state: true },
    _suggest: { state: true },
  };

  constructor() {
    super();
    this._config = null;
    this._error = null;
    this._notice = null;
    this._dialog = null; // {type: "add_area"|"delete_area"|"delete_station"|"discard"|"settings", ...}
    this._saving = false;
    this._now = Date.now();
    this._drafts = {}; // key → draft; key: "options:<entry>" | "station:<entry>:<id|new>"
    this._base = {}; // key → the draft as loaded (what "unsaved" is measured against)
    this._versions = {}; // key → the stored version the draft started from (sent with the save)
    this._lastKey = null;
    this._errors = {}; // key → {field: error}
    this._t = (k) => k;
    this._lang = null;
    this._dragIndex = null;
    this._menu = false; // the ⋮ menu is open
    this._open = {}; // what is unfolded: "more:<id>" help texts, "<draft key>:<preset id>" presets on a phone
    this._runs = {}; // "<entry>:<station>" → {runs, current, sensor, imported} as runs/list returns it
    this._run = null; // the run shown: {key, id, data (with its curve), at}
    this._hover = null; // seconds under the pointer in the chart
    this._chartWidth = 0;
    this._confirm = null; // the run whose delete waits for a confirmation
    this._fold = loadFold();
    this._running = {}; // "<entry>:<station>" → its appliance ran at the last update
  }

  connectedCallback() {
    super.connectedCallback();
    this._timer = setInterval(() => {
      this._now = Date.now();
      this._tickRuns();
    }, 1000);
    this._beforeUnload = (e) => {
      if (this._dirtyKey()) e.preventDefault();
    };
    window.addEventListener("beforeunload", this._beforeUnload);
    if (this.hass && !this._config) this._load();
    // back on the page while a search is shown going on: ask again how far it is (the result may come from there)
    const ctx = this._searchCtx;
    if (ctx && this._suggest?.key === ctx.key && this._suggest.loading && !this._searchPoll) this._pollSearch(ctx.area, ctx.station, ctx.key, true);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    cancelAnimationFrame(this._wheelFrame);
    this._wheelFrame = null;
    this._wheelSum = null;
    clearInterval(this._searchPoll);
    this._searchPoll = null;
    clearInterval(this._timer);
    clearTimeout(this._previewTimer);
    this._resize?.disconnect();
    this._observed = null;
    window.removeEventListener("beforeunload", this._beforeUnload);
  }

  updated(changed) {
    if (changed.has("_dialog") && this._dialog) this._focusDialog();
    if (changed.has("_menu") && this._menu) this.shadowRoot?.querySelector(".menu button")?.focus();
    this._observeChart();
    if (changed.has("hass") && this._config) this._watchRuns();
    if (changed.has("route")) {
      // entering a station's page: fetch its runs again (runs may have come meanwhile)
      const r = this._route;
      const key = r.page === "station" ? this._runsKey(r.entry, r.station) : null;
      if (key && key !== this._runsShown && this._runs[key]) this._loadRuns(r.entry, r.station);
      this._runsShown = key;
    }
    if (changed.has("route")) {
      // left a page by any way (browser back, a link …): its unsaved draft is dropped
      const key = this._key();
      if (this._lastKey && this._lastKey !== key && this._drafts[this._lastKey]) {
        const dirty = !same(this._drafts[this._lastKey], this._base[this._lastKey]);
        this._drop(this._lastKey);
        if (dirty) this._notice = { kind: "warn", text: this._t("notice.discarded") };
      }
      this._lastKey = key;
    }
    if (changed.has("hass") && this.hass) {
      if (this._lang !== this.hass.language) {
        this._lang = this.hass.language;
        translator(this.hass.language).then((t) => {
          this._t = t;
          this.requestUpdate();
        });
      }
      if (!this._config && !this._loading && !this._error) this._load();
    }
  }

  async _load() {
    this._loading = true;
    try {
      this._config = await this.hass.callWS({ type: `${WS}/config/get` });
      this._error = null;
      // a draft whose data changed elsewhere meanwhile is dropped (it would overwrite that)
      for (const key of Object.keys(this._drafts)) {
        if (this._versions[key] !== this._currentVersion(key)) {
          const dirty = !same(this._drafts[key], this._base[key]);
          this._drop(key);
          if (dirty) this._notice = { kind: "warn", text: this._t("notice.changed") };
        }
      }
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._loading = false;
    }
  }

  // ------------------------------------------------------------------ routes

  get _route() {
    return parseRoute(this.route?.path, location.search);
  }

  _area(entryId) {
    return this._config?.areas.find((a) => a.entry_id === entryId);
  }

  _go(path, { replace = false, force = false } = {}) {
    const dirty = this._dirtyKey();
    if (dirty && !force) {
      this._dialog = { type: "discard", path, replace };
      return;
    }
    if (dirty) this._drop(dirty);
    const url = `/${PANEL}${path ? `/${path}` : ""}`;
    if (replace) history.replaceState(null, "", url);
    else history.pushState(null, "", url);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace } }));
    this._notice = null;
    this.shadowRoot?.querySelector(".pane")?.scrollTo?.(0, 0);
  }

  // ------------------------------------------------------------------ drafts

  _key() {
    const r = this._route;
    if (r.page === "options") return `options:${r.entry}`;
    if (r.page === "station") return `station:${r.entry}:${r.station}`;
    return null;
  }

  _dirtyKey() {
    const key = this._key();
    return key && this._drafts[key] && !same(this._drafts[key], this._base[key]) ? key : null;
  }

  // the stored version of what a draft key edits (null: a new station)
  _currentVersion(key) {
    const [kind, entry, id] = key.split(":");
    const area = this._area(entry);
    if (!area) return undefined;
    if (kind === "options") return area.version;
    if (id === "new") return null;
    return area.stations.find((s) => s.id === id)?.version;
  }

  _draft(key, make) {
    if (!this._drafts[key]) {
      const base = make();
      if (!base) return null;
      this._base[key] = base;
      this._versions[key] = this._currentVersion(key);
      this._drafts = { ...this._drafts, [key]: clone(base) };
    }
    return this._drafts[key];
  }

  _change(key, fn) {
    const draft = clone(this._drafts[key]);
    fn(draft);
    this._drafts = { ...this._drafts, [key]: draft };
  }

  _drop(key) {
    const drafts = { ...this._drafts };
    delete drafts[key];
    delete this._base[key];
    delete this._versions[key];
    this._open = Object.fromEntries(Object.entries(this._open || {}).filter(([k]) => !k.startsWith(`${key}:`)));
    this._drafts = drafts;
    this._errors = { ...this._errors, [key]: {} };
  }

  static presetDraft(p) {
    return { id: p.id, name: p.name, duration: p.duration, text: formatDuration(p.duration), note: p.note || "" };
  }

  static presetPayload(p) {
    return { id: p.id, name: p.name, duration: p.duration, ...(p.note ? { note: p.note } : {}) };
  }

  // rows left completely empty are not presets
  static presetsPayload(list) {
    return list.filter((p) => p.name.trim() || p.text.trim() || p.note.trim()).map(HomeTimerPanel.presetPayload);
  }

  _optionsDraft(area) {
    return { options: clone(area.options), quick_timers: area.quick_timers.map(HomeTimerPanel.presetDraft) };
  }

  _stationDraft(area, id) {
    if (id === "new") {
      return {
        name: "",
        icon: "",
        mode: "manual",
        presets: [],
        power: { ...clone(this._config.specs.power_defaults), sensor: "" },
      };
    }
    const s = area.stations.find((x) => x.id === id);
    if (!s) return null;
    return { name: s.name, icon: s.icon || "", mode: s.mode, presets: s.presets.map(HomeTimerPanel.presetDraft), power: clone(s.power) };
  }

  async _save() {
    const key = this._key();
    const draft = this._drafts[key];
    const r = this._route;
    const area = this._area(r.entry);
    if (!draft || !area) return;
    this._saving = true;
    try {
      let result;
      if (r.page === "options") {
        result = await this.hass.callWS({
          type: `${WS}/area/save`,
          entry_id: area.entry_id,
          version: this._versions[key],
          options: draft.options,
          quick_timers: HomeTimerPanel.presetsPayload(draft.quick_timers),
        });
      } else {
        const station = area.stations.find((s) => s.id === r.station);
        result = await this.hass.callWS({
          type: `${WS}/station/save`,
          entry_id: area.entry_id,
          station_id: station ? station.id : null,
          version: station ? this._versions[key] : null,
          station: {
            name: draft.name,
            icon: draft.icon,
            mode: draft.mode,
            presets: HomeTimerPanel.presetsPayload(draft.presets),
            power: draft.power,
          },
        });
      }
      if (result.errors) {
        this._errors = { ...this._errors, [key]: result.errors };
        this._notice = { kind: "error", text: this._t("notice.fix_errors") };
        return;
      }
      if (result.changed) {
        this._drop(key);
        await this._load();
        this._notice = { kind: "warn", text: this._t("notice.changed") };
        return;
      }
      this._drop(key);
      await this._load();
      if (r.page === "station" && r.station === "new") this._go(`station/${area.entry_id}/${result.station_id}`, { replace: true, force: true });
      this._notice = { kind: "ok", text: this._t("notice.saved") };
    } catch (err) {
      this._notice = { kind: "error", text: err?.message || String(err) };
    } finally {
      this._saving = false;
    }
  }

  _discard() {
    const key = this._key();
    if (key) this._drop(key);
  }

  _err(key, field) {
    const code = this._errors[key]?.[field];
    return code ? html`<div class="err" role="alert">${this._t(`error.${code}`)}</div>` : nothing;
  }

  // ------------------------------------------------------------------ actions

  async _createArea(areaId) {
    try {
      const result = await this.hass.callWS({ type: `${WS}/area/create`, area_id: areaId });
      if (result.errors) {
        this._notice = { kind: "error", text: this._t(`error.${result.errors.area_id}`) };
        return;
      }
      this._dialog = null;
      await this._load();
      this._go(`area/${result.entry_id}`);
    } catch (err) {
      this._notice = { kind: "error", text: err?.message || String(err) };
    }
  }

  async _deleteArea(entryId) {
    this._dialog = null;
    try {
      await this.hass.callWS({ type: `${WS}/area/delete`, entry_id: entryId });
    } catch (err) {
      this._notice = { kind: "error", text: err?.message || String(err) };
      return;
    }
    this._drop(`options:${entryId}`);
    await this._load();
    this._go("", { force: true });
  }

  async _deleteStation(entryId, stationId) {
    this._dialog = null;
    try {
      await this.hass.callWS({ type: `${WS}/station/delete`, entry_id: entryId, station_id: stationId });
    } catch (err) {
      this._notice = { kind: "error", text: err?.message || String(err) };
      return;
    }
    this._drop(`station:${entryId}:${stationId}`);
    await this._load();
    this._go(`area/${entryId}`, { force: true });
  }

  async _saveSettings(settings) {
    try {
      const result = await this.hass.callWS({ type: `${WS}/settings/save`, ...settings });
      this._config = { ...this._config, settings: result.settings };
    } catch (err) {
      this._notice = { kind: "error", text: err?.message || String(err) };
      this._config = { ...this._config }; // the switch shows the stored setting again
    }
  }

  // ------------------------------------------------------------------ render

  render() {
    if (this._error) return html`<div class="msg">${this._t("load_failed")}: ${this._error}</div>`;
    if (!this._config) return html`<div class="msg">${this._t("loading")}</div>`;
    const r = this._route;
    const area = r.entry ? this._area(r.entry) : null;
    let page = r.page;
    if (page !== "home" && !area) page = "missing";
    if (page === "home" && !this.narrow && this._config.areas.length) {
      // the desktop shows the first area right away
      return this._layout(this._config.areas[0], "area", r);
    }
    return this._layout(area, page, r);
  }

  _layout(area, page, r) {
    const t = this._t;
    const showList = !this.narrow || page === "home";
    const showPane = !this.narrow || page !== "home";
    let title = t("title");
    let back = null;
    let crumb = null;
    if (page === "area" && this.narrow) (title = area.name), (back = "");
    if (page === "options") (title = t("options.short")), (back = `area/${area.entry_id}`), (crumb = area.name);
    if (page === "station") {
      const s = area.stations.find((x) => x.id === r.station);
      title = s ? s.name : t("station.new_title");
      back = `area/${area.entry_id}`;
      crumb = area.name;
    }
    if (this.narrow) crumb = null;
    if (!this.narrow) back = null; // the list is always there
    return html`
      <div class="toolbar">
        ${back !== null
          ? html`<button class="icon" aria-label=${t("back")} @click=${() => this._go(back)}><ha-icon icon="mdi:arrow-left"></ha-icon></button>`
          : html`<ha-menu-button .hass=${this.hass} .narrow=${this.narrow}></ha-menu-button>`}
        <div class="title">${crumb ? html`<button class="crumb" @click=${() => this._go(`area/${area.entry_id}`)}>${crumb}</button> › ` : nothing}${title}</div>
        ${this._renderMenu(area, page, r)}
      </div>
      <div class="body">
        ${showList ? this._renderAreas(area) : nothing}
        ${showPane
          ? html`<div class="pane">
              ${this._notice ? html`<div class="notice ${this._notice.kind}">${this._notice.text}</div>` : nothing}
              ${page === "area" ? this._renderArea(area) : nothing}
              ${page === "options" ? this._renderOptions(area) : nothing}
              ${page === "station" ? this._renderStation(area, r.station) : nothing}
              ${page === "missing" ? html`<div class="msg">${t("missing")}</div>` : nothing}
              ${page === "home" && !this._config.areas.length ? this._renderEmpty() : nothing}
            </div>`
          : nothing}
      </div>
      ${this._dirtyKey() ? this._renderSaveBar() : nothing}
      ${this._dialog ? this._renderDialog() : nothing}
    `;
  }

  // the ⋮ menu: panel settings, documentation, and deleting what the page shows
  _renderMenu(area, page, r) {
    const t = this._t;
    const station = page === "station" ? area?.stations.find((s) => s.id === r.station) : null;
    const items = [
      { icon: "mdi:cog-outline", label: t("menu.settings"), run: () => (this._dialog = { type: "settings" }) },
      { icon: "mdi:book-open-variant", label: t("menu.docs"), run: () => window.open(DOCS, "_blank", "noopener") },
    ];
    if (station) items.push({ icon: "mdi:delete-outline", label: t("station.delete"), danger: true, run: () => (this._dialog = { type: "delete_station", area, station }) });
    if (page === "options" || (page === "area" && area)) items.push({ icon: "mdi:delete-outline", label: t("area.delete"), danger: true, run: () => (this._dialog = { type: "delete_area", area }) });
    const close = () => ((this._menu = false), this.shadowRoot?.querySelector(".menuwrap > .icon")?.focus());
    const keys = (e) => {
      const buttons = [...this.shadowRoot.querySelectorAll(".menu button")];
      const i = buttons.indexOf(this.shadowRoot.activeElement);
      if (e.key === "Escape" && this._menu) (e.preventDefault(), close());
      else if (this._menu && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        buttons[(i + (e.key === "ArrowDown" ? 1 : buttons.length - 1)) % buttons.length]?.focus();
      }
    };
    return html`<div class="menuwrap" @keydown=${keys}>
      <button class="icon" aria-label=${t("menu.open")} aria-haspopup="menu" aria-expanded=${this._menu ? "true" : "false"} @click=${() => (this._menu = !this._menu)}><ha-icon icon="mdi:dots-vertical"></ha-icon></button>
      ${this._menu
        ? html`<div class="menuscrim" @click=${() => (this._menu = false)}></div>
            <div class="menu" role="menu">
              ${items.map(
                (it, i) => html`${it.danger && !items[i - 1]?.danger ? html`<div class="menusep"></div>` : nothing}
                  <button role="menuitem" class=${it.danger ? "danger" : ""} @click=${() => ((this._menu = false), it.run())}><ha-icon icon=${it.icon}></ha-icon>${it.label}</button>`
              )}
            </div>`
        : nothing}
    </div>`;
  }

  _renderEmpty() {
    const t = this._t;
    return html`<div class="empty"><ha-icon icon="mdi:timer-cog-outline"></ha-icon><p>${t("empty")}</p>
      <button class="btn filled" @click=${() => (this._dialog = { type: "add_area" })}>${t("area.add")}</button></div>`;
  }

  _renderAreas(selected) {
    const t = this._t;
    return html`<nav class="areas">
      <div class="h">${t("areas")}</div>
      ${this._config.areas.map((a) => {
        const ringing = a.entities.ringing && this.hass.states[a.entities.ringing]?.state === "on";
        const count = a.stations.length ? t("area.stations", { count: a.stations.length }) : t("area.no_stations");
        return html`<button class="ai ${selected?.entry_id === a.entry_id ? "sel" : ""}" @click=${() => this._go(`area/${a.entry_id}`)}>
          <span class="ic"><ha-icon icon=${a.icon || "mdi:texture-box"}></ha-icon></span>
          <span class="txt"><b>${a.name}</b><span>${count}${!a.loaded ? ` · ${t(a.disabled ? "area.disabled" : "area.not_loaded")}` : ""}</span></span>
          ${ringing ? html`<span class="dot" title=${t("live.ringing")}></span>` : nothing}
        </button>`;
      })}
      <button class="add" @click=${() => (this._dialog = { type: "add_area" })}>+ ${t("area.add")}</button>
    </nav>`;
  }

  _renderArea(area) {
    const t = this._t;
    const o = area.options;
    const loose = looseTimers(this.hass, area);
    const options = () => this._go(`area/${area.entry_id}/options`);
    const add = () => this._go(`station/${area.entry_id}/new`);
    return html`
      ${this.narrow ? nothing : html`<h1>${area.name}</h1>`}
      <div class="chips">
        <span class="chip">${o.ring_until_acknowledged ? t("chip.ring_until_ack") : t("chip.ring_once")}</span>
        ${o.ring_until_acknowledged && o.reminder_minutes ? html`<span class="chip">${t("chip.reminder", { min: o.reminder_minutes })}</span>` : nothing}
        ${o.ring_until_acknowledged && o.auto_dismiss_minutes ? html`<span class="chip">${t("chip.dismiss", { min: o.auto_dismiss_minutes })}</span>` : nothing}
        <button class="btn push" @click=${options}>${t("options.open")}</button>
      </div>
      <div class="sechead"><div class="sec">${t("stations")}</div>${area.stations.length ? html`<button class="btn" @click=${add}>+ ${t("station.add")}</button>` : nothing}</div>
      ${area.stations.length
        ? html`<div class="grid">${area.stations.map((s) => this._renderStationCard(area, s))}</div>`
        : html`<div class="emptycard"><b>${t("station.none")}</b><span>${t("station.none_hint")}</span><button class="btn filled" @click=${add}>+ ${t("station.add")}</button></div>`}
      <div class="sec">${t("quick_timers")} <span class="lc">– ${t("quick_timers.hint")}</span></div>
      <div class="qt">
        ${area.quick_timers.map((p) => html`<button class="q" @click=${options}>${p.name}<i>${formatDuration(p.duration)}</i></button>`)}
        <button class="link" @click=${options}>${area.quick_timers.length ? html`<ha-icon icon="mdi:pencil-outline"></ha-icon>${t("quick_timers.edit")}` : `+ ${t("quick_timers.add")}`}</button>
      </div>
      ${loose.length
        ? html`<div class="sec">${t("loose")}</div>
            <div class="qt">${loose.map((x) => html`<span class="q static">${x.name}<i>${t(`status.${x.status}`)}${x.ends_at ? ` · ${formatDuration((Date.parse(x.ends_at) - this._now) / 1000)}` : ""}</i></span>`)}</div>`
        : nothing}
    `;
  }

  _liveText(live) {
    const t = this._t;
    if (live.status === "unavailable") return t("live.unavailable");
    if (live.status === "idle") return live.running ? t("live.running_no_timer") : t("live.no_timer");
    const time = live.seconds != null ? formatDuration(live.seconds) : "";
    if (live.kind === "countdown") return `${live.name} · ${time}`;
    if (live.kind === "elapsed") return t("live.elapsed", { name: live.name, time });
    if (live.kind === "overtime") return t("live.ringing_since", { name: live.name, time });
    if (live.kind === "paused") return t("live.paused", { name: live.name, time });
    return live.name || t(`status.${live.status}`);
  }

  _stationSummary(s) {
    const t = this._t;
    if (s.mode !== "power") return t("station.summary_manual", { count: s.presets.length });
    const kind = t(`kind.${s.power.kind}`);
    const rule = s.power.done?.type || "off";
    if (s.power.kind === "until_off") return `${t("mode.power")} · ${rule === "off" ? kind : t(`rule.${rule}`)}`;
    if (s.power.kind === "start_preset") {
      const p = s.presets.find((x) => x.id === s.power.preset);
      return `${t("mode.power")} · ${kind}${p ? ` ${t("quote", { name: p.name })}` : ""}`;
    }
    return `${t("mode.power")} · ${kind}`;
  }

  _liveClass(live) {
    return live.status === "ringing" ? "ring" : live.running || live.status === "active" ? "run" : "";
  }

  _renderStationCard(area, s) {
    const live = stationLive(this.hass, s, this._now);
    const cls = this._liveClass(live);
    const icon = live.status === "ringing" ? "mdi:bell-ring" : s.icon || (s.mode === "power" ? "mdi:power-plug-outline" : "mdi:timer-outline");
    return html`<button class="card station ${cls}" @click=${() => this._go(`station/${area.entry_id}/${s.id}`)}>
      <div class="st"><span class="ic ${cls}"><ha-icon icon=${icon}></ha-icon></span>
        <span class="txt"><b>${s.name}</b><span>${this._stationSummary(s)}</span></span><span class="go">›</span></div>
      <div class="line"><span class="live ${cls} ${live.status === "idle" ? "quiet" : ""}">${this._liveText(live)}</span>
        ${live.running && live.watts != null ? html`<span class="watts">${Math.round(live.watts)} ${live.unit}</span>` : nothing}</div>
    </button>`;
  }

  // ------------------------------------------------------------------ fields
  // Home Assistant's own controls (ha-selector) where it provides them; a plain field
  // that looks alike when it does not (the element is not public API).

  get _haSelector() {
    return !!customElements.get("ha-selector");
  }

  _selector(key, field, label, selector, value, onChange, helper, required = false, disabled = false) {
    const kind = Object.keys(selector)[0];
    const changed = (v) => (this._clearErr(key, field), onChange(v));
    if (this._haSelector) {
      return html`<div class="fieldwrap">
        <ha-selector .hass=${this.hass} .selector=${selector} .value=${value ?? undefined} .label=${label} .helper=${helper} .required=${required} .disabled=${disabled}
          @value-changed=${(e) => changed(kind === "number" ? (e.detail.value ?? null) : e.detail.value)}></ha-selector>${this._err(key, field)}</div>`;
    }
    onChange = changed;
    if (kind === "select") {
      return html`<label class="fieldwrap"><span class="ff"><span class="fl">${label}</span>
          <select ?disabled=${disabled} .value=${value || ""} @change=${(e) => onChange(e.target.value || null)}>
            <option value="" ?selected=${!value}>–</option>
            ${selector.select.options.map((o) => html`<option value=${o.value} ?selected=${o.value === value}>${o.label}</option>`)}
          </select></span>${helper ? html`<span class="help">${helper}</span>` : nothing}${this._err(key, field)}</label>`;
    }
    if (kind === "boolean") return this._toggle(label, helper, value, onChange);
    const number = kind === "number";
    const unit = selector.number?.unit_of_measurement;
    return html`<label class="fieldwrap"><span class="ff">
        <span class="fl">${label}</span>
        <input type=${number ? "number" : "text"} inputmode=${number ? "decimal" : "text"} .value=${String(value ?? "")} ?disabled=${disabled}
          step=${selector.number?.step ?? ""} min=${selector.number?.min ?? ""} max=${selector.number?.max ?? ""}
          list=${kind === "entity" ? "ht-sensors" : nothing}
          @input=${(e) => onChange(number ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)}
          @change=${(e) => !number && onChange(e.target.value.trim())} />
        ${unit ? html`<span class="unit">${unit}</span>` : nothing}
      </span>
      ${kind === "entity" ? html`<datalist id="ht-sensors">${this._powerSensors().map((s) => html`<option value=${s.id}>${s.name}</option>`)}</datalist>` : nothing}
      ${helper ? html`<span class="help">${helper}</span>` : nothing}${this._err(key, field)}</label>`;
  }

  _number(key, field, label, value, range, unit, onChange, helper, step = 1, disabled = false) {
    return this._selector(key, field, label, { number: { min: range?.min, max: range?.max, step, mode: "box", unit_of_measurement: unit } }, value, onChange, helper, false, disabled);
  }

  _clearErr(key, field) {
    if (this._errors[key]?.[field]) {
      const errors = { ...this._errors[key] };
      delete errors[field];
      this._errors = { ...this._errors, [key]: errors };
    }
  }

  _toggle(label, hint, value, onChange, disabled = false) {
    return html`<label class="row ${disabled ? "off" : ""}">
      <span class="lbl"><b>${label}</b>${hint ? html`<span>${hint}</span>` : nothing}</span>
      <input type="checkbox" class="switch" .checked=${!!value} ?disabled=${disabled} @change=${(e) => onChange(e.target.checked)} />
    </label>`;
  }

  // a short help line, and its details behind ⓘ
  _help(text, more, id) {
    const open = this._open?.[`more:${id}`];
    return html`<div class="help">${text}${more
        ? html` <button class="more" aria-expanded=${open ? "true" : "false"} @click=${() => this._toggleOpen(`more:${id}`)}><ha-icon icon="mdi:information-outline"></ha-icon>${this._t(open ? "less" : "more")}</button>
            ${open ? html`<div class="moretext">${more}</div>` : nothing}`
        : nothing}</div>`;
  }

  _toggleOpen(id) {
    this._open = { ...(this._open || {}), [id]: !this._open?.[id] };
  }

  // a small number field inside a sentence
  _inline(key, field, value, range, unit, onChange, step = 1, label = null) {
    const bad = this._errors[key]?.[field];
    return html`<span class="inl ${bad ? "bad" : ""}"><input type="number" inputmode="decimal" aria-label=${label || this._t(`rule_field.${field.split(".").pop()}`)}
        .value=${String(value ?? "")} step=${step} min=${range?.min ?? ""} max=${range?.max ?? ""}
        style="width:${Math.max(2, String(value ?? "").length) + 1.5}ch"
        @input=${(e) => (this._clearErr(key, field), onChange(e.target.value === "" ? null : Number(e.target.value)))} /><i>${unit}</i></span>`;
  }

  // ------------------------------------------------------------------ area options

  _renderOptions(area) {
    const t = this._t;
    const key = `options:${area.entry_id}`;
    const d = this._draft(key, () => this._optionsDraft(area));
    const spec = this._config.specs.options;
    const set = (k, v) => this._change(key, (x) => (x.options[k] = v));
    const ringing = d.options.ring_until_acknowledged;
    return html`<div class="column">
      ${this.narrow ? nothing : html`<h1>${t("options.title", { area: area.name })}</h1>`}
      <div class="card"><div class="ch">${t("options.when_it_runs_out")}</div><div class="cb">
        ${this._toggle(t("options.ring_until_acknowledged"), t("options.ring_until_acknowledged_hint"), ringing, (v) => set("ring_until_acknowledged", v))}
        <div class="pair ${ringing ? "" : "off"}">
          ${this._number(key, "reminder_minutes", t("options.reminder_minutes"), d.options.reminder_minutes, spec.reminder_minutes, "min", (v) => set("reminder_minutes", v), t("options.reminder_minutes_hint"), 1, !ringing)}
          ${this._number(key, "auto_dismiss_minutes", t("options.auto_dismiss_minutes"), d.options.auto_dismiss_minutes, spec.auto_dismiss_minutes, "min", (v) => set("auto_dismiss_minutes", v), t("options.auto_dismiss_minutes_hint"), 1, !ringing)}
        </div>
        ${this._toggle(t("options.report_missed"), t("options.report_missed_hint"), d.options.report_missed, (v) => set("report_missed", v))}
      </div></div>
      <div class="card"><div class="ch">${t("quick_timers")} <span class="chs">– ${t("quick_timers.long_hint")}</span></div><div class="cb">
        ${this._presetTable(key, d.quick_timers, "quick_timers", (fn) => this._change(key, (x) => fn(x.quick_timers)))}
      </div></div>
    </div>`;
  }

  // a sortable list of presets (a station's or the area's quick timers)
  _presetTable(key, presets, prefix, change) {
    const t = this._t;
    const clear = () => (this._errors = { ...this._errors, [key]: {} });
    const move = (from, to) =>
      (clear(), change((list) => {
        if (to < 0 || to >= list.length) return;
        const [p] = list.splice(from, 1);
        list.splice(to, 0, p);
      }));
    const remove = (i) => (clear(), change((l) => l.splice(i, 1)));
    const field = (i, name, label, value, opts = {}) => html`<label class="ff small ${this._errors[key]?.[`${prefix}.${i}.${name}`] ? "bad" : ""}">
        <span class="fl">${label}</span>
        <input .value=${value} placeholder=${opts.placeholder || ""} inputmode=${opts.numeric ? "numeric" : "text"}
          @input=${(e) => change((l) => opts.input(l[i], e.target.value))} @blur=${opts.blur ? () => change((l) => opts.blur(l[i])) : null} /></label>`;
    return html`<div class="presets">
      ${presets.map((p, i) => {
        const open = this.narrow && this._open?.[`${key}:${p.id}`];
        const durErr = this._err(key, `${prefix}.${i}.duration`);
        return html`<div class="preset ${open ? "open" : ""}" draggable=${this.narrow ? "false" : "true"}
            @dragstart=${(e) => ((this._dragIndex = i), e.dataTransfer.setData("text/plain", String(i)))}
            @dragover=${(e) => e.preventDefault()}
            @drop=${(e) => (e.preventDefault(), this._dragIndex != null && move(this._dragIndex, i), (this._dragIndex = null))}>
          ${this.narrow ? nothing : html`<span class="handle" title=${t("preset.drag")}><ha-icon icon="mdi:drag"></ha-icon></span>`}
          ${field(i, "name", t("preset.name"), p.name, { input: (x, v) => (x.name = v) })}
          ${field(i, "duration", t("preset.duration"), p.text, {
            placeholder: "9:30",
            numeric: true,
            input: (x, v) => ((x.text = v), (x.duration = parseDuration(v))),
            blur: (x) => x.duration != null && (x.text = formatDuration(x.duration)),
          })}
          ${!this.narrow || open ? field(i, "note", t("preset.note"), p.note, { placeholder: t("preset.note_placeholder"), input: (x, v) => (x.note = v) }) : nothing}
          ${this.narrow
            ? html`<span class="acts">
                <button class="icon sm" aria-label=${t("preset.up")} ?disabled=${i === 0} @click=${() => move(i, i - 1)}><ha-icon icon="mdi:chevron-up"></ha-icon></button>
                <button class="icon sm" aria-label=${t("preset.down")} ?disabled=${i === presets.length - 1} @click=${() => move(i, i + 1)}><ha-icon icon="mdi:chevron-down"></ha-icon></button>
                <button class="icon sm" aria-label=${t("preset.details")} aria-expanded=${open ? "true" : "false"} @click=${() => this._toggleOpen(`${key}:${p.id}`)}><ha-icon icon=${open ? "mdi:chevron-double-up" : "mdi:dots-horizontal"}></ha-icon></button>
              </span>`
            : html`<span class="acts">
                <button class="icon sm" aria-label=${t("preset.up")} ?disabled=${i === 0} @click=${() => move(i, i - 1)}><ha-icon icon="mdi:chevron-up"></ha-icon></button>
                <button class="icon sm" aria-label=${t("preset.down")} ?disabled=${i === presets.length - 1} @click=${() => move(i, i + 1)}><ha-icon icon="mdi:chevron-down"></ha-icon></button>
                <button class="icon sm" aria-label=${t("preset.delete")} @click=${() => remove(i)}><ha-icon icon="mdi:delete-outline"></ha-icon></button>
              </span>`}
          ${open ? html`<button class="link danger" @click=${() => remove(i)}><ha-icon icon="mdi:delete-outline"></ha-icon>${t("preset.delete")}</button>` : nothing}
          <div class="rowerr">${this._err(key, `${prefix}.${i}.name`)}${durErr !== nothing ? durErr : p.text && p.duration == null ? html`<div class="err">${t("error.invalid_duration")}</div>` : nothing}</div>
        </div>`;
      })}
      <div class="presetfoot">
        <button class="btn" @click=${() => change((l) => l.push({ id: tempId(), name: "", duration: null, text: "", note: "" }))}>+ ${t("preset.add")}</button>
        <span class="help">${t("preset.duration_hint")}</span>
      </div>
    </div>`;
  }

  // ------------------------------------------------------------------ station

  _renderStation(area, stationId) {
    const t = this._t;
    const key = `station:${area.entry_id}:${stationId}`;
    const d = this._draft(key, () => this._stationDraft(area, stationId));
    if (!d) return html`<div class="msg">${t("missing")}</div>`;
    const station = area.stations.find((s) => s.id === stationId);
    const set = (fn) => this._change(key, fn);
    const presetErrors = Object.keys(this._errors[key] || {}).some((k) => k.startsWith("presets."));
    const showPresets = d.mode === "manual" || d.power.kind === "start_preset" || presetErrors;
    const icon = d.icon || (d.mode === "power" ? "mdi:power-plug-outline" : "mdi:timer-outline");
    const power = d.mode === "power";
    return html`
      <div class="cols ${power ? "wide" : ""}">
        <div class="col">
          <div class="card"><div class="cb ident">
            ${this._selector(key, "name", t("station.name"), { text: {} }, d.name, (v) => set((x) => (x.name = v ?? "")), t("station.name_placeholder"), true)}
            ${this._selector(key, "icon", t("station.icon"), { icon: { placeholder: icon } }, d.icon, (v) => set((x) => (x.icon = v || "")))}
            <div class="starts"><span class="fl">${t("station.starts")}</span>
              <div class="seg" role="radiogroup" aria-label=${t("station.starts")}>
                ${["manual", "power"].map((m) => html`<button role="radio" aria-checked=${d.mode === m} class=${d.mode === m ? "on" : ""} @click=${() => set((x) => (x.mode = m))}>${d.mode === m ? html`<ha-icon icon="mdi:check"></ha-icon>` : nothing}${t(`mode.${m}`)}</button>`)}
              </div>
            </div>
            <div class="identhelp">${this._help(t(`mode.${d.mode}_hint`))}</div>
          </div></div>
          ${power ? this._renderPowerPage(area, station, key, d, set) : nothing}
          ${showPresets
            ? html`<div class="card"><div class="ch">${t("presets")} <span class="chs">– ${t("presets.hint")}</span></div><div class="cb">
                ${this._presetTable(key, d.presets, "presets", (fn) => set((x) => fn(x.presets)))}
              </div></div>`
            : nothing}
        </div>
        ${station && !power ? html`<div class="colr">${this._renderNow(area, station)}</div>` : nothing}
      </div>
    `;
  }

  _renderNow(area, station) {
    const t = this._t;
    const live = stationLive(this.hass, station, this._now);
    const cls = this._liveClass(live);
    const stats = station.mode === "power" ? this._usual(station) : null;
    return html`
      <div class="card now ${cls}">
        <div class="ch nowh">${t("now")} <span class="livedot" title=${t("now.live")}></span></div>
        <div class="cb">
          <div class="big">${this._liveText(live)}</div>
          ${station.mode === "power"
            ? html`<div class="help">${live.running ? t("live.appliance_on") : t("live.appliance_off")}${live.running && live.watts != null ? ` · ${Math.round(live.watts)} ${live.unit}` : ""}${stats ? ` · ${t("now.usual", { time: stats })}` : ""}</div>`
            : nothing}
        </div>
        <div class="cf">
          ${station.device_id ? html`<a href="/config/devices/device/${station.device_id}">${t("link.device")} ↗</a>` : nothing}
          <a href="/config/entities?historyBack=1&config_entry=${area.entry_id}&sub_entry=${station.id}">${t("link.entities")} ↗</a>
          ${station.entities.timer ? html`<a href="/history?entity_id=${station.entities.timer}">${t("link.history")} ↗</a>` : nothing}
        </div>
      </div>
    `;
  }

  // the usual duration of a power station (its sensor), as text
  _usual(station) {
    const id = station.entities?.timer ? `${station.entities.timer}_usual_duration` : null;
    const state = id ? this.hass.states[id] : null;
    const seconds = state && !["unknown", "unavailable"].includes(state.state) ? Number(state.state) * (state.attributes.unit_of_measurement === "min" ? 60 : 1) : null;
    return seconds ? formatDuration(seconds) : null;
  }

  _powerSensors() {
    return Object.entries(this.hass.states)
      .filter(([id, s]) => id.startsWith("sensor.") && (s.attributes?.device_class === "power" || ["W", "kW"].includes(s.attributes?.unit_of_measurement)))
      .map(([id, s]) => ({ id, name: s.attributes.friendly_name || id }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  // ------------------------------------------------------------------ a station by power
  // One page: the curve of a stored run with the live preview of the draft, what the
  // timer should do (with "done" under "ring when it's done"), on and off, the runs.

  _renderPowerPage(area, station, key, d, set) {
    const t = this._t;
    const stored = station?.mode === "power"; // saved as a power station: it has runs
    if (!stored) {
      return html`
        <div class="card"><div class="ch">${t("runs.title")}</div><div class="cb">
          <div class="emptycard"><b>${t("runs.after_save")}</b><span>${t("runs.after_save_hint")}</span></div>
        </div></div>
        ${this._renderTodo(key, d, set, null, null)}
        ${this._renderOnOff(key, d, set, false)}`;
    }
    const rkey = this._runsKey(area.entry_id, station.id);
    const data = this._runs[rkey];
    if (!data) this._loadRunsSoon(area.entry_id, station.id);
    const preview = data ? this._previewFor(area, station, d) : null;
    return html`
      ${this._renderCurve(area, station, d, data, preview)}
      ${this._renderTodo(key, d, set, station, preview)}
      <div class="duo">
        <div class="duol">${this._renderOnOff(key, d, set, true, station)}${data ? this._renderSensor(data, station) : nothing}</div>
        <div class="duor">${data && !data.error ? this._renderRunList(area, station, d, data, preview) : nothing}</div>
      </div>`;
  }

  _loadRunsSoon(entryId, stationId) {
    const key = this._runsKey(entryId, stationId);
    if (this._pendingRuns === key) return;
    this._pendingRuns = key;
    Promise.resolve().then(() => this._loadRuns(entryId, stationId).finally(() => (this._pendingRuns = null)));
  }

  // the power settings as the preview compares them (what decides runs and rings)
  static powerSig(power, presets) {
    const { sensor, tolerance, ...rest } = power || {}; // the tolerance only judges, it changes no ring
    return JSON.stringify([rest, (presets || []).map((p) => [p.id, p.duration])]);
  }

  // the draft differs from the saved settings in anything the preview shows
  _draftDiffers(station, d) {
    return HomeTimerPanel.powerSig(d.power, d.presets) !== HomeTimerPanel.powerSig(station.power, station.presets);
  }

  // the preview of the draft for this station: fetched again (debounced) when the draft changes
  _previewFor(area, station, d) {
    const key = this._runsKey(area.entry_id, station.id);
    const list = this._runs[key];
    const sig = [key, HomeTimerPanel.powerSig(d.power, d.presets), HomeTimerPanel.powerSig(station.power, station.presets), list?.runs?.length, list?.runs?.[0]?.id, list?.current ? 1 : 0].join("|");
    const have = this._preview?.key === key ? this._preview : null;
    if (this._previewSig !== sig) {
      this._previewSig = sig;
      clearTimeout(this._previewTimer);
      const payload = { power: clone(d.power), presets: HomeTimerPanel.presetsPayload(d.presets) };
      this._previewTimer = setTimeout(() => this._loadPreview(area.entry_id, station.id, sig, payload), have ? 300 : 0);
    }
    return have?.data || null;
  }

  async _loadPreview(entryId, stationId, sig, payload) {
    const key = this._runsKey(entryId, stationId);
    try {
      const data = await this.hass.callWS({ type: `${WS}/runs/preview`, entry_id: entryId, station_id: stationId, ...payload });
      if (this._previewSig === sig) this._preview = { key, data };
    } catch (err) {
      if (this._previewSig === sig) this._preview = { key, data: null, error: err?.message || String(err) };
    }
  }

  // the run going on changed: only its preview is fetched again
  async _refreshCurrentPreview(entryId, stationId) {
    const key = this._runsKey(entryId, stationId);
    const area = this._area(entryId);
    const station = area?.stations.find((s) => s.id === stationId);
    const d = this._drafts[`station:${entryId}:${stationId}`] || (station && this._stationDraft(area, stationId));
    if (!d || this._preview?.key !== key || !this._preview.data) return;
    try {
      const res = await this.hass.callWS({ type: `${WS}/runs/preview`, entry_id: entryId, station_id: stationId, power: clone(d.power), presets: HomeTimerPanel.presetsPayload(d.presets), current_only: true });
      if (this._preview?.key === key && this._preview.data) this._preview = { key, data: { ...this._preview.data, current: res.current } };
    } catch {
      // the next full preview brings it
    }
  }

  // what saved settings or the draft make of a run, in words
  _outcome(res, kind) {
    const t = this._t;
    if (!res) return "";
    if (res.blip) return t("outcome.blip");
    if (res.open && res.rang == null) return t("outcome.open");
    const time = formatDuration((res.start || 0) + res.rang); // on the curve's time, as the marks
    if (res.trigger === "countdown") return t(kind === "warn" ? "outcome.warns" : "outcome.rings", { time });
    if (res.trigger === "none") return t(kind === "warn" ? "outcome.no_warning" : kind === "start_preset" ? "outcome.no_ring" : "outcome.not_yet");
    return t(`outcome.${res.trigger}`, { time });
  }

  // the label of a mark in the chart
  _markLabel(res, kind) {
    const t = this._t;
    const time = formatDuration((res.start || 0) + res.rang);
    if (res.trigger === "countdown") return t(kind === "warn" ? "mark.warns" : "mark.rings", { time });
    return t(`mark.${res.trigger}`, { time });
  }

  _renderCurve(area, station, d, data, preview) {
    const t = this._t;
    const live = stationLive(this.hass, station, this._now);
    const usual = this._usual(station);
    const head = html`<div class="ch curveh">
        <span class="curvet"><b>${this._liveText(live)}</b>
          <span class="chs">${live.running ? t("live.appliance_on") : t("live.appliance_off")}${live.running && live.watts != null ? ` · ${Math.round(live.watts)} ${live.unit}` : ""}${usual ? ` · ${t("now.usual", { time: usual })}` : ""}</span></span>
        <span class="links">
          ${station.device_id ? html`<a href="/config/devices/device/${station.device_id}">${t("link.device")} ↗</a>` : nothing}
          <a href="/config/entities?historyBack=1&config_entry=${area.entry_id}&sub_entry=${station.id}">${t("link.entities")} ↗</a>
          ${station.entities.timer ? html`<a href="/history?entity_id=${station.entities.timer}">${t("link.history")} ↗</a>` : nothing}
        </span>
      </div>`;
    const cls = this._liveClass(live);
    const sensorNote = d.power.sensor && d.power.sensor !== station.power.sensor
      ? html`<div class="note">${t("runs.sensor_changed", { old: this._name(station.power.sensor), new: this._name(d.power.sensor) })}</div>` : nothing;
    if (!data) return html`<div class="card curve ${cls}">${head}<div class="cb">${t("loading")}</div></div>`;
    if (data.error) return html`<div class="card curve ${cls}">${head}<div class="cb">${t("load_failed")}: ${data.error}</div></div>`;
    const key = this._runsKey(area.entry_id, station.id);
    const shown = this._run?.key === key ? this._run : null;
    if (!data.runs.length && !data.current) {
      return html`<div class="card curve ${cls}">${head}<div class="cb">${sensorNote}<div class="emptycard"><ha-icon icon="mdi:chart-bell-curve-cumulative"></ha-icon><b>${t("runs.none")}</b><span>${t("runs.none_hint")}</span></div></div></div>`;
    }
    if (!shown) return html`<div class="card curve ${cls}">${head}<div class="cb">${t("loading")}</div></div>`;
    const run = shown.data;
    const pv = shown.id === "current" ? preview?.current : preview?.runs.find((r) => r.id === shown.id);
    const differs = this._draftDiffers(station, d);
    const p = d.power;
    const ruleSteps = p.kind === "until_off" ? doneSteps(p.done) : [];
    const rule = ruleSteps.length ? ruleSteps[ruleSteps.length - 1] : null; // the ring's own rule: the last step
    const marks = [];
    const savedKind = station.power.kind;
    if (pv?.saved?.rang != null && !pv.saved.blip) {
      marks.push({ t: pv.saved.start + pv.saved.rang, label: this._markLabel(pv.saved, savedKind) + (differs ? ` ${t("mark.saved")}` : ""), color: pv.saved.trigger === "cancelled" ? "var(--ht-run)" : "" });
    }
    const apart = (a, b) => !a || !b || a.rang == null || b.rang == null || Math.abs(a.start + a.rang - (b.start + b.rang)) >= 1 || a.trigger !== b.trigger;
    if (differs && pv?.draft?.rang != null && !pv.draft.blip && apart(pv.saved, pv.draft)) {
      marks.push({ t: pv.draft.start + pv.draft.rang, label: `${this._markLabel(pv.draft, p.kind)} ${t("mark.draft")}`, dash: true, color: pv.draft.trigger === "cancelled" ? "var(--ht-run)" : "" });
    }
    const ignore = ruleSteps[0]?.ignore ? Number(ruleSteps[0].ignore) : 0; // later steps count theirs from the step before
    const winSec = rule && ["drop", "knee", "flatten", "upturn", "steeper_up", "cycling"].includes(rule.type) ? Number(rule.window || 0) : 0;
    // where each step was met (the draft's while it differs, else the saved one's)
    const stepsAt = (differs ? pv?.draft : pv?.saved)?.steps;
    const shownSteps = differs ? ruleSteps : doneSteps(station.power.done);
    const pins = p.kind === "until_off" && stepsAt && !(differs ? pv.draft : pv.saved).blip
      ? stepsAt.map((x, i) => (x == null ? null : { t: (differs ? pv.draft : pv.saved).start + x, n: i + 1, last: i === stepsAt.length - 1, label: `${i + 1}. ${shownSteps[i] ? this._ruleText(shownSteps[i]) : ""} – ${formatDuration(x)}` })).filter(Boolean)
      : [];
    const ring = pv?.draft?.trigger === "rule" ? pv.draft.start + pv.draft.rang : null;
    const window = winSec && ring != null ? { from: Math.max(0, ring - winSec), to: ring, label: this._learning(d) && run.marks?.done != null ? "" : `${winSec} s` } : null;
    const levelW = rule?.type === "pause" ? rule.below : rule?.type === "cycling" ? rule.level : null;
    const unit = this._unit(station);
    const level = levelW != null ? { w: Number(levelW), label: `${levelW} ${unit}` } : null;
    const learning = this._learning(d) && !run.running;
    const tol = Number(p.tolerance ?? 60);
    const markT = this._learning(d) ? run.marks?.done ?? null : null;
    const done = learning && markT != null ? { t: markT, label: t("mark.mark", { time: formatDuration(markT) }), tol, tolLabel: `${tol} s` } : null;
    const extra = [...marks.map((k) => k.t), ...(done ? [done.t + tol] : [])];
    const zkey = `${key}:${run.id}:${run.start}`; // the zoom belongs to the run shown (the run going on is always "current")
    const view = this._zoom?.key === zkey ? this._zoom.view : null;
    const m = chartModel(run, { width: this._chartWidth || 600, height: this.narrow ? 200 : 250, onAbove: Number(p.on_above || 0), ignore: ignore ? ignore + (pv?.draft?.start || 0) : 0, extra, view, fitY: !this._yFull, levels: level ? [level.w] : [] });
    const zoomable = m.zoomed || m.end > 2 * MIN_VIEW;
    const zoom = (factor, at) => (this._zoom = { key: zkey, view: zoomView(view, m.end, factor, at) });
    const pan = (share) => (this._zoom = { key: zkey, view: zoomView({ from: m.from + share * m.span, to: m.to + share * m.span }, m.end, 1, m.from) });
    // a button that disables itself (the whole run shown, the narrowest view) hands the focus to the chart
    const keepFocus = () => this.updateComplete.then(() => {
      if (this.shadowRoot?.activeElement?.disabled) this.shadowRoot.querySelector(".chartbox")?.focus();
    });
    // zooming in keeps your mark in view (where it is placed exactly); else the middle of what is shown
    const centre = () => (markT != null && markT >= m.from && markT <= m.to ? markT : (m.from + m.to) / 2);
    const outlier = this._isOutlier(key, run.id);
    const hv = this._hover != null ? valueAt(run, this._hover) : null;
    const labels = { aria: t("runs.chart.aria"), stopped: t("runs.chart.stopped"), unknown: t("runs.chart.unknown"), ignored: t("runs.chart.ignored") };
    const actual = run.rang != null && !run.running ? t(run.simulated ? "runs.actual_history" : "runs.actual", { time: formatDuration(run.rang) }) : "";
    return html`<div class="card curve ${cls}">${head}<div class="cb">
      ${sensorNote}
      <div class="runh2"><span>${run.running ? t("runs.now") : this._runWhen(run)} <span class="chs">· ${formatDuration(run.duration)}</span>
        ${run.source === "history" ? html`<span class="tag">${t("runs.tag.history")}</span>` : nothing}
        ${run.refilled ? html`<span class="tag">${t("runs.tag.refilled")}</span>` : nothing}
        ${this._otherSensor(run, station) ? html`<span class="tag">${t("runs.tag.other_sensor")}</span>` : nothing}</span>
        <span class="runacts">${learning ? html`<button class="btn sm" @click=${() => this._setSkip(area, station, run, !run.skip)}>${t(run.skip ? "learn.take_back" : "learn.leave_out")}</button>` : nothing}
        ${run.running ? html`<span class="tag live">${t("runs.tag.running")}</span>` : html`${this._renderReread(area, station, run)}${this._renderDeleteRun(area, station, run)}`}</span>
      </div>
      ${learning && run.skip ? html`<div class="note">${t("learn.left_out")}</div>` : nothing}
      ${this._renderRereadNote(run, key)}
      ${zoomable ? html`<div class="zoombar" role="group" aria-label=${t("zoom.label")}>
          ${m.zoomed ? html`<span class="chs">${formatDuration(m.from)} – ${formatDuration(m.to)}</span>` : this.narrow ? nothing : html`<span class="chs">${t("zoom.hint")}</span>`}
          <button class="icon sm" aria-label=${t("zoom.out")} title=${t("zoom.out")} ?disabled=${!m.zoomed} @click=${() => (zoom(2, centre()), keepFocus())}><ha-icon icon="mdi:magnify-minus-outline"></ha-icon></button>
          <button class="icon sm" aria-label=${t("zoom.in")} title=${t("zoom.in")} ?disabled=${m.span <= MIN_VIEW + 1e-6} @click=${() => (zoom(0.5, centre()), keepFocus())}><ha-icon icon="mdi:magnify-plus-outline"></ha-icon></button>
          ${m.zoomed ? html`<button class="icon sm ${this._yFull ? "" : "on"}" aria-pressed=${this._yFull ? "false" : "true"} aria-label=${t("zoom.fit_y")}
              title=${t(this._yFull ? "zoom.fit_y" : "zoom.full_y")} @click=${() => (this._yFull = !this._yFull)}><ha-icon icon="mdi:arrow-expand-vertical"></ha-icon></button>` : nothing}
          <button class="icon sm" aria-label=${t("zoom.all")} title=${t("zoom.all")} ?disabled=${!m.zoomed} @click=${() => ((this._zoom = null), keepFocus())}><ha-icon icon="mdi:arrow-expand-horizontal"></ha-icon></button>
        </div>` : nothing}
      <div class="chartbox ${learning ? "marking" : ""}" tabindex=${(learning && markT != null) || zoomable ? "0" : "-1"} role="group" aria-label=${t("runs.chart.aria")}
        aria-keyshortcuts=${zoomable ? "+ - 0" : nothing} aria-description=${zoomable ? t("zoom.keys") : nothing}
        @pointermove=${(e) => this._pointer(m, e)} @pointerleave=${() => (cancelAnimationFrame(this._frame), (this._hover = null))}
        @pointerdown=${(e) => learning && this._dragStart(e, m, area, station, run)} @pointerup=${(e) => this._dragEnd(e)} @pointercancel=${(e) => this._dragEnd(e, true)}
        @wheel=${(e) => zoomable && this._wheel(e, m, zoom, pan)}
        @keydown=${(e) => {
          if (zoomable && ["+", "=", "-", "0"].includes(e.key) && !e.ctrlKey && !e.metaKey) {
            e.preventDefault();
            if (e.key === "0") this._zoom = null;
            else zoom(e.key === "-" ? 2 : 0.5, centre());
            return;
          }
          if (learning && markT != null) return this._markKey(e, area, station, run, markT);
          if (m.zoomed && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { // not marking: the arrows move along
            e.preventDefault();
            pan((e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 0.5 : 0.1));
          }
        }}>
        ${renderChart(run, m, { labels, hover: this._hover, unit, marks, window, level, done, pins })}
      </div>
      ${m.zoomed ? this._renderOverview(run, m, extra, zkey) : nothing}
      <div class="readout">${this._hover != null && hv != null ? `${formatDuration(this._hover)} · ${Math.round(hv * 10) / 10} ${unit}` : html`&nbsp;`}</div>
      ${this._learning(d) ? this._renderMarkBar(area, station, run, pv, outlier) : nothing}
      <div class="legend">
        <span><i class="sw curve"></i>${t("runs.legend.power")}</span>
        ${p.on_above != null ? html`<span><i class="sw thr"></i>${t("runs.legend.threshold", { watts: p.on_above, unit })}</span>` : nothing}
        ${ignore > 0 ? html`<span><i class="sw ign"></i>${t("runs.legend.ignored")}</span>` : nothing}
        ${marks.length ? html`<span><i class="sw rang"></i>${t(differs ? "runs.legend.saved" : "runs.legend.ring")}</span>` : nothing}
        ${pins.length ? html`<span><i class="sw pinsw"></i>${t("runs.legend.steps")}</span>` : nothing}
        ${differs && marks.some((k) => k.dash) ? html`<span><i class="sw rang dash"></i>${t("runs.legend.draft")}</span>` : nothing}
        ${window ? html`<span><i class="sw win"></i>${t("runs.legend.window")}</span>` : nothing}
        ${done ? html`<span><i class="sw done"></i>${t("runs.legend.mark")}</span><span><i class="sw tol"></i>${t("runs.legend.tol", { s: tol })}</span>` : nothing}
      </div>
      <div class="runinfo">
        ${pv ? html`<div>${differs
            ? html`${t("runs.this_run")} <b>${this._outcome(pv.saved, savedKind)}</b> ${t("runs.saved_word")} → <b>${this._outcome(pv.draft, p.kind)}</b> ${t("runs.draft_word")}${this._delta(pv) ? ` (${this._delta(pv)})` : ""}.`
            : html`${t("runs.this_run")} <b>${this._outcome(pv.saved, savedKind)}</b>.`}
            ${actual ? html`<span class="chs">${actual}</span>` : nothing}</div>` : html`<div class="chs">${this._preview?.key === key && this._preview.error ? `${t("preview.failed")}: ${this._preview.error}` : t("loading")}</div>`}
        ${done && pv && !run.skip ? html`<div>${t("learn.your_mark", { time: formatDuration(markT) })} – ${t("runs.legend.saved")}: ${this._verdictHtml(this._verdict(pv.saved, markT, tol))}${differs ? html` · ${t("runs.legend.draft")}: ${this._verdictHtml(this._verdict(pv.draft, markT, tol))}` : nothing}</div>` : nothing}
        ${run.from > 0 ? html`<div class="chs">${t("runs.picked_up", { time: formatDuration(run.from) })}</div>` : nothing}
        ${run.running ? html`<div class="chs">${t("runs.running", { time: formatDuration(run.duration) })}</div>` : nothing}
      </div>
    </div></div>`;
  }

  // ------------------------------------------------------------------ learning: marks
  // A mark says when a run was really done (seconds on its curve). Marks are data,
  // saved at once (not with the save bar); the draft's tolerance decides "on time".

  _learning(d) {
    return d.power.kind === "until_off";
  }

  // on time / too early / too late of a ring against the user's mark
  _verdict(res, mark, tol) {
    if (mark == null || !res) return null;
    if (res.blip || res.rang == null) return { kind: "late", delta: null };
    const delta = res.start + res.rang - mark;
    return { kind: delta < -0.5 ? "early" : delta > tol ? "late" : "on_time", delta };
  }

  _deltaText(delta) {
    const a = Math.abs(Math.round(delta));
    return `${delta < 0 ? "−" : "+"}${a >= 60 ? formatDuration(a) : `${a} s`}`;
  }

  _verdictHtml(v) {
    if (!v) return nothing;
    const text = v.delta == null ? this._t("verdict.none") : this._t(`verdict.${v.kind}`, { delta: this._deltaText(v.delta) });
    return html`<span class="v ${v.kind}">${text}</span>`;
  }

  // the last suggestions, while what they were made from is unchanged
  _freshSuggest(key) {
    const s = this._suggest?.key === key ? this._suggest : null;
    if (!s?.data || !s.sig) return null;
    const d = this._drafts[`station:${this._route.entry}:${this._route.station}`];
    return !d || this._suggestSig(key, d) === s.sig ? s.data : null;
  }

  _isOutlier(key, runId) {
    const s = this._freshSuggest(key);
    return !!s?.outliers?.includes(runId) && !this._kept?.has(`${key}:${runId}`);
  }

  // a mark set, moved or removed: shown at once, saved after a short pause (nudges, drags)
  // zoomed in: a mark moved out of view (buttons, keys) takes the view along
  _followMark(run, done) {
    const z = this._zoom;
    if (!z?.view || !z.key.endsWith(`:${run.id}:${run.start}`)) return;
    const { from, to } = z.view;
    if (done >= from && done <= to) return;
    const span = to - from;
    const shift = done < from ? done - from - span * 0.1 : done - to + span * 0.1;
    this._zoom = { key: z.key, view: { from: from + shift, to: to + shift } }; // (clamped to the run when drawn)
  }

  _setMark(area, station, run, value, save = true) {
    const key = this._runsKey(area.entry_id, station.id);
    const done = value == null ? null : Math.max(0, Math.round(value * 10) / 10);
    const marks = done == null ? {} : { done };
    if (this._run?.key === key && this._run.id === run.id) this._run = { ...this._run, data: { ...this._run.data, marks } };
    if (save && !this._drag && done != null) this._followMark(run, done);
    const list = this._runs[key];
    if (list) this._runs = { ...this._runs, [key]: { ...list, runs: list.runs.map((r) => (r.id === run.id ? { ...r, marks } : r)) } };
    if (!save) return;
    this._markSaves = this._markSaves || new Map();
    const pending = this._markSaves.get(run.id);
    if (pending) clearTimeout(pending.timer);
    const send = async () => {
      this._markSaves.delete(run.id);
      try {
        await this.hass.callWS({ type: `${WS}/runs/mark`, entry_id: area.entry_id, station_id: station.id, run_id: run.id, done });
      } catch (err) {
        this._notice = { kind: "warn", text: err?.message || String(err) };
      }
    };
    this._markSaves.set(run.id, { timer: setTimeout(send, 400), send });
  }

  // marks still waiting to be saved go out now (before a reload would show the old ones)
  async _flushMarks() {
    const saves = [...(this._markSaves?.values() || [])];
    for (const x of saves) clearTimeout(x.timer);
    await Promise.all(saves.map((x) => x.send()));
  }

  async _setSkip(area, station, run, skip) {
    const key = this._runsKey(area.entry_id, station.id);
    try {
      await this.hass.callWS({ type: `${WS}/runs/skip`, entry_id: area.entry_id, station_id: station.id, run_id: run.id, skip });
    } catch (err) {
      this._notice = { kind: "warn", text: err?.message || String(err) };
      return;
    }
    if (this._run?.key === key && this._run.id === run.id) this._run = { ...this._run, data: { ...this._run.data, skip } };
    const list = this._runs[key];
    if (list) this._runs = { ...this._runs, [key]: { ...list, runs: list.runs.map((r) => (r.id === run.id ? { ...r, skip } : r)) } };
  }

  async _doneNow(area, station) {
    try {
      await this.hass.callWS({ type: `${WS}/runs/mark`, entry_id: area.entry_id, station_id: station.id, run_id: "current" });
      await this._loadRun(area.entry_id, station.id, "current");
    } catch (err) {
      this._notice = { kind: "warn", text: err?.message || String(err) };
    }
  }

  // clicking (or tapping) the curve sets the mark there; dragging moves it
  // a mouse sets the mark at once and drags it; a finger only on a tap (a swipe scrolls the page)
  _dragStart(e, m, area, station, run) {
    const svgEl = e.currentTarget.querySelector("svg");
    const at = secondsAt(m, svgEl, e.clientX);
    if (at == null) return;
    const touch = e.pointerType === "touch";
    this._drag = { m, svgEl, area, station, run, touch, x: e.clientX, y: e.clientY, at, prev: run.marks?.done ?? null };
    if (touch) return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    this._setMark(area, station, run, this._clampMark(run, at), false);
  }

  _dragEnd(e, cancelled = false) {
    const drag = this._drag;
    if (!drag) return;
    this._drag = null;
    if (cancelled) {
      this._setMark(drag.area, drag.station, drag.run, drag.prev, false); // back to before (it was a scroll)
      return;
    }
    if (drag.touch) {
      if (e && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 10) this._setMark(drag.area, drag.station, drag.run, this._clampMark(drag.run, drag.at));
      return;
    }
    const mark = this._run?.data?.marks?.done;
    if (mark != null) this._setMark(drag.area, drag.station, drag.run, mark);
  }

  // a mark stays on the curve: from where it is known to its last reading
  _clampMark(run, t) {
    const last = Math.max(run.duration || 0, (run.t || [0]).slice(-1)[0] || 0);
    return Math.min(Math.max(run.from || 0, t), last);
  }

  // Ctrl (or ⌘) + wheel, or pinching a touchpad: zoom around the pointer; zoomed in, a sideways wheel moves along.
  // Wheel events come fast (a touchpad): they add up and are drawn once a frame.
  _wheel(e, m, zoom, pan) {
    if (this._drag) return; // the mark is being dragged on the scale of its start
    const svgEl = e.currentTarget.querySelector("svg.chart");
    if (!svgEl) return;
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? m.plotW : 1; // lines, pages → pixels
    const w = this._wheelSum || (this._wheelSum = { zoom: 0, pan: 0 });
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      w.zoom += Math.max(-1, Math.min(1, (e.deltaY * unit) / 100)) * 0.35;
      w.at = secondsAt(m, svgEl, e.clientX, true);
    } else if (m.zoomed && Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
      e.preventDefault();
      w.pan += (e.deltaX * unit) / m.plotW;
    } else {
      return;
    }
    if (this._wheelFrame) return;
    this._wheelFrame = requestAnimationFrame(() => {
      const sum = this._wheelSum;
      this._wheelFrame = null;
      this._wheelSum = null;
      if (sum.zoom) zoom(Math.exp(sum.zoom), sum.at);
      else if (sum.pan) pan(sum.pan);
      this._hover = null; // the second under the pointer changed
    });
  }

  // zoomed in: the whole run below the chart; drag it (or tap) to move the part shown – grabbing the window keeps
  // where it was grabbed, a press beside it centres the window there
  _renderOverview(run, m, extra, zkey) {
    const o = overviewModel(run, this._chartWidth || 600, extra);
    const at = (e) => {
      const el = e.currentTarget.querySelector("svg.overview");
      return el ? secondsAt(o, el, e.clientX, true) : null;
    };
    const place = (centre) => (this._zoom = { key: zkey, view: zoomView({ from: centre - m.span / 2, to: centre + m.span / 2 }, m.end, 1, centre) });
    const down = (e) => {
      if (e.button !== 0) return;
      const s = at(e);
      if (s == null) return;
      e.currentTarget.setPointerCapture?.(e.pointerId);
      const inside = s >= m.from && s <= m.to;
      this._ovDrag = { offset: inside ? s - (m.from + m.to) / 2 : 0 };
      if (!inside) place(s);
    };
    const move = (e) => {
      if (!this._ovDrag || !(e.buttons & 1)) return (this._ovDrag = null);
      const s = at(e);
      if (s != null) place(s - this._ovDrag.offset);
    };
    const end = () => (this._ovDrag = null);
    return html`<div class="ovbox" title=${this._t("zoom.overview")}
      @pointerdown=${down} @pointermove=${move} @pointerup=${end} @pointercancel=${end} @lostpointercapture=${end}>
      ${renderOverview(o, { from: m.from, to: m.to }, this._t("zoom.overview"))}
    </div>`;
  }

  _markKey(e, area, station, run, mark) {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[e.key];
    if (!step) return;
    e.preventDefault();
    this._setMark(area, station, run, this._clampMark(run, mark + step * (e.shiftKey ? 10 : 1)));
  }

  _renderMarkBar(area, station, run, pv, outlier) {
    const t = this._t;
    const mark = run.marks?.done;
    if (run.running) {
      return html`<div class="mbar">${mark != null
          ? html`<b>✓ ${t("learn.marked_now", { time: formatDuration(mark) })}</b><span class="chs">${t("learn.now_hint")}</span>`
          : html`<span>${t("learn.ready")}</span><span class="sp"></span><button class="btn filled ok" @click=${() => this._doneNow(area, station)}><ha-icon icon="mdi:check"></ha-icon>${t("learn.done_now")}</button>`}</div>`;
    }
    if (mark == null) {
      const guess = this._lastHover ?? (pv?.saved?.rang != null ? pv.saved.start + pv.saved.rang : (run.duration || 0) / 2);
      return html`<div class="mbar ask"><span>${t("learn.ask")} <span class="chs">${t("learn.ask_hint")}</span></span><span class="sp"></span>
        <button class="btn ok" @click=${() => this._setMark(area, station, run, guess)}><ha-icon icon="mdi:check"></ha-icon>${t("learn.mark_here")}</button></div>`;
    }
    const nudge = (s) => this._setMark(area, station, run, this._clampMark(run, mark + s));
    return html`<div class="mbar ${outlier ? "bad" : ""}"><b>✓ ${t("learn.done_at", { time: formatDuration(mark) })}</b><span class="chs">– ${outlier ? t("runs.tag.outlier") : t("learn.drag_hint")}</span><span class="sp"></span>
      ${[-10, -1, 1, 10].map((x) => html`<button class="btn sm ok" @click=${() => nudge(x)}>${x > 0 ? "+" : "−"}${Math.abs(x)} s</button>`)}
      <button class="btn sm" @click=${() => this._setMark(area, station, run, null)}>${t("learn.remove")}</button></div>`;
  }

  // "Suggest from my runs": the tolerance, the button, the suggestions
  _renderSuggest(key, d, set, station) {
    const t = this._t;
    if (!this._learning(d)) return nothing;
    if (!station || station.mode !== "power") return nothing;
    const area = this._area(this._route.entry);
    const rkey = this._runsKey(area.entry_id, station.id);
    const data = this._runs[rkey];
    if (!data || data.error) return nothing;
    const need = data.min_marked ?? 3;
    const marked = (data.runs || []).filter((r) => r.marks?.done != null && !r.skip).length;
    const spec = this._config.specs.power;
    const tolField = this._inline(key, "power.tolerance", d.power.tolerance ?? 60, spec.tolerance, "s", (v) => set((x) => (x.power.tolerance = v)));
    const parts = t("learn.tolerance", { count: marked }).split(/(\{field\})/);
    const sug = this._suggest?.key === rkey ? this._suggest : null;
    if (!sug && this._searchChecked !== rkey) {
      this._searchChecked = rkey; // a search may be going on (or over) from before the page was opened
      this._attachSearch(area, station, rkey);
    }
    return html`<div class="suggest">
      <div class="sugrow">
        <button class="btn filled" ?disabled=${marked < need || sug?.loading} @click=${() => this._runSuggest(area, station, d)}><ha-icon icon="mdi:auto-fix"></ha-icon>${t("learn.suggest")}</button>
        <span class="chs">${marked < need ? t("learn.suggest_need", { min: need, count: marked }) : parts.map((x) => (x === "{field}" ? tolField : x))}</span>
      </div>
      ${sug?.loading ? this._renderProgress(area, station, sug) : nothing}
      ${sug?.cancelled ? html`<div class="note">${t("learn.cancelled")}</div>` : nothing}
      ${sug?.error ? html`<div class="err">${sug.error}</div>` : nothing}
      ${sug?.data && sug.sig !== this._suggestSig(rkey, d) ? html`<div class="note">${t("learn.stale")}</div>` : nothing}
      ${sug?.data && !sug.data.suggestions.length ? html`<div class="note">${t(sug.data.marked < need ? "learn.not_usable" : "learn.none")}</div>` : nothing}
      ${(sug?.data?.suggestions || []).map((x, i) => html`<div class="sug ${i === 0 ? "best" : ""}" data-type=${x.rule.steps ? "steps" : x.rule.type}>
          <div class="sugh"><span>${this._ruleText(x.rule)}</span>
            <button class="btn sm" @click=${() => this._useSuggestion(key, set, x.rule)}>${t("learn.use")}</button></div>
          <div class="chs"><span class=${x.on_time === x.runs ? "v on_time" : "v late"}>${t("learn.fits", { on: x.on_time, n: x.runs })}</span>
            ${x.median != null ? ` · ${t("learn.median", { s: Math.round(x.median) })}` : ""}${x.max != null ? ` · ${t("learn.max", { s: Math.round(x.max) })}` : ""}
            ${x.early ? html` · <span class="v early">${t("learn.early", { count: x.early })}</span>` : nothing}${x.late ? html` · <span class="v late">${t("learn.late", { count: x.late })}</span>` : nothing}</div>
        </div>`)}
    </div>`;
  }

  // a rule as its sentence, with the values written in; steps numbered, joined by "then"
  _ruleText(rule) {
    const t = this._t;
    if (rule?.steps) return rule.steps.map((x, i) => `${i + 1}. ${this._ruleText(x)}`).join(` ${t("steps.then_inline")} `);
    const fields = Object.fromEntries((this._config.specs.rules[rule.type]?.fields || []).map((f) => [f.key, f]));
    return t(`rule_sentence.${rule.type}`).replace(/\{(\w+)\}/g, (m, k) => (k === "type" ? t(`rule_verb.${rule.type}`) : fields[k] ? `${rule[k]} ${fields[k].unit}` : m));
  }

  // what the suggestions were made from: marks, runs left out, tolerance, on and off
  _suggestSig(key, d) {
    const runs = (this._runs[key]?.runs || []).map((r) => [r.id, r.marks?.done ?? null, !!r.skip]);
    return JSON.stringify([runs, d.power.tolerance, d.power.on_above, d.power.off_after]);
  }

  // the search's progress: a bar, what it does, how far, and Cancel
  _renderProgress(area, station, sug) {
    const t = this._t;
    const pct = Math.round((sug.progress || 0) * 100);
    return html`<div class="sprog">
      <div class="sbar" role="progressbar" aria-label=${t("learn.searching")} aria-valuemin="0" aria-valuemax="100" aria-valuenow=${pct}><i style="width:${pct}%"></i></div>
      <span class="chs">${t(`learn.stage.${sug.stage || "rules"}`)} · ${pct} %</span>
      <button class="btn sm" ?disabled=${sug.cancelling} @click=${() => this._cancelSearch(area, station, sug.key)}>${t("learn.cancel")}</button>
    </div>`;
  }

  async _runSuggest(area, station, d) {
    const key = this._runsKey(area.entry_id, station.id);
    await this._flushMarks();
    const sig = this._suggestSig(key, d);
    const old = this._suggest?.key === key ? this._suggest : null;
    const before = old?.data || null;
    this._suggest = { key, loading: true, progress: 0, stage: "rules", data: before, sig: old?.sig };
    this._pollSearch(area, station, key, false);
    try {
      const data = await this.hass.callWS({ type: `${WS}/runs/suggest`, entry_id: area.entry_id, station_id: station.id, power: clone(d.power), sig });
      if (this._suggest?.key === key) this._suggest = { key, data, sig };
    } catch (err) {
      if (this._suggest?.key !== key) return;
      if (err?.code === "busy") {
        // a search started elsewhere (another tab) is going on: show it and take its result
        this._suggest = { key, loading: true, progress: 0, stage: "rules", data: before, sig: old?.sig };
        this._pollSearch(area, station, key, true);
        return;
      }
      this._suggest = err?.code === "cancelled" ? { key, cancelled: true, data: before, sig: old?.sig } : { key, error: err?.message || String(err) };
    } finally {
      if (this._searchPollKey === key && !this._searchPollAttached) this._stopPoll();
    }
  }

  // asks how far the search is (twice a second); "attached": a search this page did not start – its result comes from here
  _pollSearch(area, station, key, attached) {
    this._stopPoll();
    this._searchPollKey = key;
    this._searchPollAttached = attached;
    this._searchCtx = { area, station, key };
    const ask = async () => {
      let status;
      try {
        status = await this.hass.callWS({ type: `${WS}/runs/suggest_status`, entry_id: area.entry_id, station_id: station.id });
      } catch (_err) {
        return; // the next one may get through
      }
      const sug = this._suggest;
      if (sug?.key !== key || !sug.loading) return this._searchPollKey === key && this._stopPoll();
      if (status.busy) {
        this._suggest = { ...sug, progress: status.progress, stage: status.stage };
      } else if (attached) {
        this._stopPoll();
        this._suggest = this._searchOutcome(key, status);
      }
    };
    this._searchPoll = setInterval(ask, 500);
  }

  _stopPoll() {
    clearInterval(this._searchPoll);
    this._searchPoll = null;
    this._searchPollKey = null;
    this._searchPollAttached = false;
  }

  _searchOutcome(key, status) {
    if (status.result) return { key, data: status.result, sig: status.sig };
    if (status.cancelled) return { key, cancelled: true };
    if (status.failed) return { key, error: this._t("learn.failed") };
    return { key, error: this._t("learn.lost") }; // Home Assistant restarted meanwhile: the search is gone
  }

  // on opening a station: a search going on shows its bar, one that is over its result
  async _attachSearch(area, station, key) {
    let status;
    try {
      status = await this.hass.callWS({ type: `${WS}/runs/suggest_status`, entry_id: area.entry_id, station_id: station.id });
    } catch (_err) {
      return;
    }
    if (this._suggest?.key === key) return; // started here meanwhile
    if (status.busy) {
      this._suggest = { key, loading: true, progress: status.progress, stage: status.stage, sig: status.sig };
      this._pollSearch(area, station, key, true);
    } else if (status.result || status.cancelled) {
      this._suggest = this._searchOutcome(key, status);
    }
  }

  async _cancelSearch(area, station, key) {
    if (this._suggest?.key === key) this._suggest = { ...this._suggest, cancelling: true };
    try {
      await this.hass.callWS({ type: `${WS}/runs/suggest_cancel`, entry_id: area.entry_id, station_id: station.id });
    } catch (err) {
      if (this._suggest?.key === key) this._suggest = { ...this._suggest, cancelling: false };
    }
  }

  _useSuggestion(key, set, rule) {
    this._errors = { ...this._errors, [key]: Object.fromEntries(Object.entries(this._errors[key] || {}).filter(([k]) => !k.startsWith("power.done."))) };
    set((x) => {
      x.power.kind = "until_off";
      x.power.done = clone(rule);
    });
    this._used = { key, rule: clone(rule) };
  }

  // runs the best suggestion misses while it fits most others: their marks may be off
  _renderOutliers(area, station, data) {
    const t = this._t;
    const key = this._runsKey(area.entry_id, station.id);
    const ids = this._freshSuggest(key)?.outliers || [];
    const runs = ids.map((id) => data.runs.find((r) => r.id === id)).filter((r) => r && !r.skip && !this._kept?.has(`${key}:${r.id}`));
    const draft = this._drafts[`station:${area.entry_id}:${station.id}`];
    const tol = Number((draft || station).power.tolerance ?? 60);
    return runs.map((r) => html`<div class="outl"><b>${t("learn.outlier", { when: this._runWhen(r) })}</b> ${t("learn.outlier_hint", { time: formatDuration(r.marks?.done), tol })}
      <div class="outla">
        <button class="btn sm" @click=${() => (this._loadRun(area.entry_id, station.id, r.id), this.shadowRoot?.querySelector(".pane")?.scrollTo?.({ top: 0, behavior: "smooth" }))}>${t("learn.check")}</button>
        <button class="btn sm" @click=${() => this._setSkip(area, station, r, true)}>${t("learn.leave")}</button>
        <button class="btn sm" @click=${() => (this._kept = new Set([...(this._kept || []), `${key}:${r.id}`]), this.requestUpdate())}>${t("learn.keep")}</button>
      </div></div>`);
  }

  // "21 s earlier" between saved and draft ring, when both ring by the same way
  _delta(pv) {
    const a = pv?.saved;
    const b = pv?.draft;
    if (!a || !b || a.rang == null || b.rang == null || a.blip || b.blip) return "";
    const diff = Math.round(b.start + b.rang - (a.start + a.rang));
    if (Math.abs(diff) < 1) return this._t("delta.same");
    return this._t(diff < 0 ? "delta.earlier" : "delta.later", { s: Math.abs(diff) });
  }

  _name(entityId) {
    return this.hass.states[entityId]?.attributes?.friendly_name || entityId;
  }

  _otherSensor(run, station) {
    return run.sensor && station.power.sensor && run.sensor !== station.power.sensor;
  }

  // what should the timer do: one question, three answers; each answer's own setting under it
  _renderTodo(key, d, set, station, preview) {
    const t = this._t;
    const p = d.power;
    const spec = this._config.specs;
    const setP = (k, v) => set((x) => (x.power[k] = v));
    const base = station ? station.power : null;
    const changed = base && ["kind", "preset", "cancel_on_off", "warn_after", "done"].some((k) => !same(p[k], base[k]));
    const sub = (k) => {
      if (k === "until_off") return this._ruleBody(key, d, set, station, preview);
      if (k === "start_preset") {
        return html`<div class="pair">
            ${this._selector(key, "power.preset", t("power.preset"), { select: { mode: "dropdown", options: d.presets.filter((x) => x.name).map((x) => ({ value: x.id, label: `${x.name} · ${x.text}` })) } }, p.preset, (v) => setP("preset", v || null), d.presets.some((x) => x.name) ? t("power.preset_below") : t("power.preset_none"))}
          </div>
          ${this._toggle(t("power.cancel_on_off"), t("power.cancel_on_off_hint"), p.cancel_on_off, (v) => setP("cancel_on_off", v))}
          ${this._previewSummary(station, d, preview)}`;
      }
      return html`<div class="pair">${this._number(key, "power.warn_after", t("power.warn_after"), p.warn_after, spec.power.warn_after, "min", (v) => setP("warn_after", v))}</div>
        ${this._previewSummary(station, d, preview)}`;
    };
    return html`<div class="card"><div class="ch">${t("power.kind")} ${changed ? html`<span class="changed">${t("changed")}</span>` : nothing}</div><div class="cb">
      <div class="opts" role="group" aria-label=${t("power.kind")}>
        ${spec.kinds.map((k) => html`<div class="opt ${p.kind === k ? "on" : ""}">
            <button class="radio" aria-pressed=${p.kind === k ? "true" : "false"} @click=${() => setP("kind", k)}>
              <span class="rb ${p.kind === k ? "on" : ""}"></span><span class="lbl"><b>${t(`kind.${k}`)}</b><span>${t(`kind.${k}_long`)}</span></span></button>
            ${p.kind === k ? html`<div class="optsub">${sub(k)}</div>` : nothing}
          </div>`)}
      </div>
    </div></div>`;
  }

  // "it is done when …": the done rule as a sentence with its values in it – or steps, one after another – and the preview
  _ruleBody(key, d, set, station, preview) {
    const t = this._t;
    const spec = this._config.specs.rules;
    const steps = doneSteps(d.power.done);
    const many = steps.length > 1;
    const clearRule = () => (this._errors = { ...this._errors, [key]: Object.fromEntries(Object.entries(this._errors[key] || {}).filter(([k]) => !k.startsWith("power.done."))) });
    // all changes go through the list of steps; one step is stored as the plain rule
    const setSteps = (fn) => set((x) => {
      const list = doneSteps(x.power.done).map(clone);
      fn(list);
      x.power.done = list.length === 1 && !STEP_KEYS.some((k) => k in list[0]) ? list[0] : { steps: list };
    });
    const move = (from, to) => {
      if (from === to || to < 0 || to >= steps.length) return;
      clearRule();
      setSteps((list) => list.splice(to, 0, ...list.splice(from, 1)));
    };
    const max = this._config.specs.max_steps || 8;
    const last = steps[steps.length - 1];
    const body = steps.map((step, i) => html`
      ${i ? html`<div class="then" aria-hidden="true">${t("steps.then")}</div>` : nothing}
      ${this._stepRow(key, step, i, steps.length, !!d.power.done?.steps, spec, setSteps, move, clearRule)}`);
    return html`<div class="ql">${t(many ? "power.done_steps" : "power.done")}</div>
      <div class="steps ${many ? "many" : "one"}" role="list">${body}</div>
      ${steps.length < max && !(steps.length === 1 && steps[0].type === "off")
        ? html`<div class="addstep"><button class="link" @click=${() => (clearRule(), setSteps((list) => list.push({ type: "trend", ...spec.trend.defaults })))}>+ ${t("steps.add")}</button>
          <span class="chs">– ${t("steps.add_hint")}</span></div>`
        : nothing}
      ${this._used?.key === key && same(this._used.rule, d.power.done) && station && !same(station.power.done, d.power.done) ? html`<div class="note">${t("learn.used")}</div>` : nothing}
      ${!many && last.type === "off" && !this._markedCount(station) ? nothing : this._previewSummary(station, d, preview)}
      ${this._renderSuggest(key, d, set, station)}
      ${this._help(`${many ? t("steps.hint") : t(`rule.${last.type}_hint`)} ${t("power.done_hint")}`, t("rule.all_hint"), "rules")}`;
  }

  // one step: its number, the rule as a sentence, move up / down / drag, remove
  _stepRow(key, step, i, count, stored, spec, setSteps, move, clearRule) {
    const t = this._t;
    const many = count > 1;
    const type = spec[step.type] ? step.type : "off";
    const fields = Object.fromEntries(spec[type].fields.map((f) => [f.key, f]));
    const prefix = stored ? `power.done.steps.${i}` : "power.done"; // the error keys follow how it is stored
    const types = Object.keys(spec).filter((r) => !many || r !== "off"); // "it switches off" ends the run: no step of a sequence
    // .value: the select follows the draft when the rule changes elsewhere (Use, Discard) – the browser ignores
    // ?selected on an option the user once picked. ?selected stays: on a new select .value comes before its options.
    const typeSelect = html`<select class="inlsel" aria-label=${many ? t("steps.rule_n", { n: i + 1 }) : t("power.rule")} .value=${type}
        @change=${(e) => (clearRule(), setSteps((list) => (list[i] = { ...keepKeys(list[i]), type: e.target.value, ...spec[e.target.value].defaults })))}>
        ${types.map((r) => html`<option value=${r} ?selected=${type === r}>${t(`rule_verb.${r}`)}</option>`)}
      </select>`;
    const parts = t(`rule_sentence.${type}`).split(/(\{\w+\})/);
    const errors = Object.entries(this._errors[key] || {}).filter(([k]) => k.startsWith(`${prefix}.`) && k.split(".").length === prefix.split(".").length + 1);
    const drop = (e) => {
      e.preventDefault();
      if (this._stepDrag != null) move(this._stepDrag, i);
      this._stepDrag = null;
    };
    return html`<div class="step" role="listitem" data-step=${i + 1} @dragover=${(e) => this._stepDrag != null && e.preventDefault()} @drop=${drop}>
      ${many ? html`<span class="handle" draggable="true" title=${t("steps.drag")}
          @dragstart=${(e) => ((this._stepDrag = i), e.dataTransfer.setData("text/plain", String(i)), e.dataTransfer.setDragImage?.(e.target.closest(".step"), 16, 16))}
          @dragend=${() => (this._stepDrag = null)}><ha-icon icon="mdi:drag"></ha-icon></span>` : nothing}
      ${many ? html`<span class="num" aria-hidden="true">${i + 1}</span>` : nothing}
      <div class="sentence">${parts.map((part) => {
        const m = part.match(/^\{(\w+)\}$/);
        if (!m) return part;
        if (m[1] === "type") return typeSelect;
        const f = fields[m[1]];
        const label = many ? t("steps.field_n", { n: i + 1, field: t(`rule_field.${f?.key}`) }) : null;
        return f ? this._inline(key, `${prefix}.${f.key}`, step[f.key], f, f.unit, (v) => setSteps((list) => (list[i][f.key] = v)), f.step, label) : part;
      })}</div>
      ${many ? html`<span class="tools">
          <button class="icon" aria-label=${t("steps.up", { n: i + 1 })} ?disabled=${i === 0} @click=${() => move(i, i - 1)}>▲</button>
          <button class="icon" aria-label=${t("steps.down", { n: i + 1 })} ?disabled=${i === count - 1} @click=${() => move(i, i + 1)}>▼</button>
          <button class="icon" aria-label=${t("steps.remove", { n: i + 1 })} @click=${() => (clearRule(), setSteps((list) => list.splice(i, 1)))}>×</button>
        </span>` : nothing}
      ${errors.map(([k, code]) => {
        const f = fields[k.split(".").pop()];
        return html`<div class="err" role="alert">${t(`error.${code}`)} ${f ? t("allowed", { min: f.min, max: f.max, unit: f.unit }) : ""}</div>`;
      })}
    </div>`;
  }

  // the draft on all stored runs, counted
  _markedCount(station) {
    if (!station) return 0;
    const area = this._area(this._route.entry);
    const data = area ? this._runs[this._runsKey(area.entry_id, station.id)] : null;
    return (data?.runs || []).filter((r) => r.marks?.done != null && !r.skip).length;
  }

  // the saved settings and the draft judged against the user's marks
  _markedSummary(station, d, preview) {
    const t = this._t;
    const area = this._area(this._route.entry);
    const data = this._runs[this._runsKey(area.entry_id, station.id)];
    const tol = Number(d.power.tolerance ?? 60);
    const byId = Object.fromEntries((data?.runs || []).map((r) => [r.id, r]));
    const judged = preview.runs.map((r) => ({ r, run: byId[r.id] })).filter((x) => x.run && x.run.marks?.done != null && !x.run.skip);
    if (!judged.length) return null;
    const differs = this._draftDiffers(station, d);
    const count = (which) => {
      const c = { on_time: [], early: 0, late: 0 };
      for (const { r, run } of judged) {
        const v = this._verdict(r[which], run.marks.done, tol);
        if (v.kind === "on_time") c.on_time.push(v.delta);
        else c[v.kind]++;
      }
      return c;
    };
    const med = (a) => {
      const x = [...a].sort((p, q) => p - q);
      const k = Math.floor(x.length / 2);
      return Math.round(x.length % 2 ? x[k] : (x[k - 1] + x[k]) / 2);
    };
    const c = count(differs ? "draft" : "saved");
    const unmarked = preview.runs.length - judged.length;
    return html`<div class="preview"><b>${t(differs ? "preview.marked_title" : "preview.saved_title", { count: judged.length })}</b>
      <span class="v on_time">${c.on_time.length ? t("preview.on_time", { count: c.on_time.length, s: med(c.on_time) }) : t("preview.on_time_none")}</span>
      · <span class=${c.early ? "v early" : ""}>${t("preview.early_n", { count: c.early })}</span>
      · <span class=${c.late ? "v late" : ""}>${t("preview.late_n", { count: c.late })}</span>
      ${differs ? html` <span class="chs">${t("preview.saved_on_time", { count: count("saved").on_time.length })}</span>` : nothing}
      ${unmarked > 0 ? html`<div class="chs">${t("preview.unmarked", { count: unmarked })}</div>` : nothing}</div>`;
  }

  _previewSummary(station, d, preview) {
    const t = this._t;
    if (station && station.mode === "power" && preview && this._learning(d)) {
      const marked = this._markedSummary(station, d, preview);
      if (marked) return marked;
    }
    if (!station || station.mode !== "power" || !preview || !this._draftDiffers(station, d)) return nothing;
    const runs = preview.runs.filter((r) => r.saved && r.draft);
    if (!runs.length) return nothing;
    const kind = d.power.kind;
    const parts = [];
    if (kind === "until_off" && (d.power.done?.type || "off") !== "off") {
      const earlier = [];
      const later = [];
      let same = 0;
      let never = 0;
      let blips = 0;
      for (const r of runs) {
        if (r.draft.blip) blips++;
        else if (r.draft.trigger === "never") never++;
        else if (r.saved.rang != null && r.draft.rang != null) {
          const diff = r.draft.start + r.draft.rang - (r.saved.start + r.saved.rang);
          if (diff < -0.5) earlier.push(-diff);
          else if (diff > 0.5) later.push(diff);
          else same++;
        }
      }
      const med = (a) => Math.round([...a].sort((x, y) => x - y)[Math.floor(a.length / 2)]);
      if (earlier.length) parts.push(t("preview.earlier", { count: earlier.length, s: med(earlier) }));
      if (later.length) parts.push(t("preview.later", { count: later.length, s: med(later) }));
      if (same) parts.push(t("preview.same", { count: same }));
      if (never) parts.push(html`<span class="warn">${t("preview.never", { count: never })}</span>`);
      if (blips) parts.push(t("preview.blips", { count: blips }));
    } else {
      const count = (which, k) => {
        const c = {};
        for (const r of runs) {
          const res = r[which];
          const key = res.blip ? "blip" : res.trigger === "countdown" ? (k === "warn" ? "warns" : "rings") : res.trigger === "none" ? (k === "warn" ? "no_warning" : "no_ring") : res.trigger;
          c[key] = (c[key] || 0) + 1;
        }
        return Object.entries(c).map(([k, n]) => t(`preview.n.${k}`, { count: n })).join(", ");
      };
      parts.push(count("draft", kind));
      parts.push(t("preview.saved_was", { list: count("saved", station.power.kind) }));
    }
    return html`<div class="preview"><b>${t("preview.title", { count: runs.length })}</b> ${parts.map((x, i) => html`${i ? " · " : ""}${x}`)}
      <div class="chs">${t("preview.marks_later")}</div></div>`;
  }

  // on and off: the sensor, and when the appliance runs and counts as off (a sentence)
  _renderOnOff(key, d, set, foldable, station) {
    const t = this._t;
    const p = d.power;
    const spec = this._config.specs.power;
    const setP = (k, v) => set((x) => (x.power[k] = v));
    const errs = Object.entries(this._errors[key] || {}).filter(([k]) => ["power.sensor", "power.on_above", "power.off_after"].includes(k));
    const open = !foldable || !this.narrow || this._fold.onoff === true || errs.length > 0;
    const unit = station ? this._unit(station) : "W";
    const parts = t("onoff.sentence").split(/(\{\w+\})/);
    const inl = { on_above: () => this._inline(key, "power.on_above", p.on_above, spec.on_above, "W", (v) => setP("on_above", v)), off_after: () => this._inline(key, "power.off_after", p.off_after, spec.off_after, "s", (v) => setP("off_after", v)) };
    const summary = `${p.sensor ? this._name(p.sensor) : t("onoff.no_sensor")} · ${t("onoff.short", { watts: p.on_above, unit })}`;
    return html`<div class="card">
      ${foldable && this.narrow
        ? html`<button class="ch fold" aria-expanded=${open ? "true" : "false"} @click=${() => this._setFold("onoff", !open)}>
            <span>${t("onoff.title")} ${open ? nothing : html`<span class="chs">– ${summary}</span>`}</span><ha-icon icon=${open ? "mdi:chevron-up" : "mdi:chevron-down"}></ha-icon></button>`
        : html`<div class="ch">${t("onoff.title")}</div>`}
      ${open
        ? html`<div class="cb">
            ${this._selector(key, "power.sensor", t("power.sensor"), { entity: { filter: { domain: "sensor", device_class: "power" } } }, p.sensor, (v) => setP("sensor", v || ""), t("power.sensor_hint"))}
            <div class="sentence">${parts.map((part) => {
              const m = part.match(/^\{(\w+)\}$/);
              return m && inl[m[1]] ? inl[m[1]]() : part;
            })}</div>
            ${errs.filter(([k]) => k !== "power.sensor").map(([k, code]) => html`<div class="err" role="alert">${t(`error.${code}`)} ${t("allowed", { ...spec[k.split(".").pop()], unit: k.endsWith("on_above") ? "W" : "s" })}</div>`)}
            ${this._help(t("power.thresholds_short"), t("power.thresholds_hint"), "thresholds")}
          </div>`
        : nothing}
    </div>`;
  }

  // ------------------------------------------------------------------ runs of a power station

  _runsKey(entryId, stationId) {
    return `${entryId}:${stationId}`;
  }

  // select: the run to show (default: the one shown, else the newest)
  async _loadRuns(entryId, stationId, select) {
    const key = this._runsKey(entryId, stationId);
    await this._flushMarks();
    try {
      const res = await this.hass.callWS({ type: `${WS}/runs/list`, entry_id: entryId, station_id: stationId });
      this._runs = { ...this._runs, [key]: res };
      if (!this._showing(key)) return; // the user went elsewhere meanwhile
      const all = [...(res.current ? [res.current] : []), ...res.runs];
      const shown = this._run?.key === key && all.some((r) => r.id === this._run.id) ? this._run.id : null;
      const id = select && all.some((r) => r.id === select) ? select : shown ?? all[0]?.id;
      if (id && (id !== this._run?.id || this._run?.key !== key || id === "current")) await this._loadRun(entryId, stationId, id);
      else if (!id) this._run = null;
    } catch (err) {
      this._runs = { ...this._runs, [key]: { error: err?.message || String(err), runs: [] } };
    }
  }

  async _loadRun(entryId, stationId, id) {
    if (this._reread && !this._reread.loading && this._reread.id !== id) this._reread = null; // its note belongs to its run
    const key = this._runsKey(entryId, stationId);
    if (this._run?.id !== id) (this._confirm = null), (this._hover = null), (this._lastHover = null);
    this._fetching = true;
    try {
      const data = await this.hass.callWS({ type: `${WS}/runs/get`, entry_id: entryId, station_id: stationId, run_id: id });
      if (this._showing(key)) this._run = { key, id, data, at: Date.now() };
    } catch (err) {
      if (!this._showing(key)) return;
      if (id === "current") await this._loadRuns(entryId, stationId); // it just ended: show it stored
      else this._notice = { kind: "warn", text: err?.message || String(err) };
    } finally {
      this._fetching = false;
    }
  }

  // this station's page is the page shown
  _showing(key) {
    const r = this._route;
    return r.page === "station" && this._runsKey(r.entry, r.station) === key;
  }

  async _deleteRun(entryId, stationId, id) {
    if (this._deleting) return;
    this._deleting = true;
    try {
      await this.hass.callWS({ type: `${WS}/runs/delete`, entry_id: entryId, station_id: stationId, run_id: id });
    } catch (err) {
      this._notice = { kind: "warn", text: err?.message || String(err) };
    }
    this._confirm = null;
    this._deleting = false;
    await this._loadRuns(entryId, stationId);
  }

  // the run going on is fetched again every few seconds while it is shown
  _tickRuns() {
    const r = this._route;
    if (r.page !== "station" || this._run?.id !== "current" || this._fetching) return;
    if (this._run.key !== this._runsKey(r.entry, r.station)) return;
    if (Date.now() - this._run.at >= 5000) this._loadRun(r.entry, r.station, "current").then(() => this._refreshCurrentPreview(r.entry, r.station));
  }

  // the appliance switched on or off: the list changes (a run starts, or is stored)
  _watchRuns() {
    const r = this._route;
    if (r.page !== "station") return;
    const station = this._area(r.entry)?.stations.find((s) => s.id === r.station);
    const id = station?.entities?.running;
    if (!id) return;
    const key = this._runsKey(r.entry, r.station);
    const on = this.hass.states[id]?.state === "on";
    const before = this._running[key];
    this._running[key] = on;
    if (before !== undefined && before !== on && this._runs[key]) this._loadRuns(r.entry, r.station, on ? "current" : undefined);
  }

  _observeChart() {
    const box = this.shadowRoot?.querySelector(".chartbox");
    if (!box || box === this._observed) return;
    this._resize?.disconnect();
    this._resize = new ResizeObserver(([entry]) => {
      const width = Math.floor(entry.contentRect.width);
      if (width && Math.abs(width - this._chartWidth) > 1) this._chartWidth = width;
    });
    this._resize.observe(box);
    this._observed = box;
  }

  _setFold(name, open) {
    this._fold = { ...this._fold, [name]: open };
    try {
      localStorage.setItem(FOLD, JSON.stringify(this._fold));
    } catch {
      // private mode and the like: it is only not remembered
    }
  }

  _runWhen(run, short = false) {
    const lang = this.hass.locale?.language || this.hass.language;
    // Home Assistant's time zone setting: the browser's, or the server's
    const timeZone = this.hass.locale?.time_zone === "server" ? this.hass.config?.time_zone : undefined;
    const d = new Date(run.start);
    const time = d.toLocaleTimeString(lang, { hour: "numeric", minute: "2-digit", timeZone });
    const day = (x) => x.toLocaleDateString("en-CA", { timeZone });
    if (day(new Date()) === day(d)) return this._t("runs.today", { time });
    return `${d.toLocaleDateString(lang, { ...(short ? {} : { weekday: "short" }), day: "numeric", month: "short", timeZone })}, ${time}`;
  }

  // the value under the pointer, at most once a frame
  _pointer(m, e) {
    const svgEl = e.currentTarget.querySelector("svg");
    const x = e.clientX;
    const drag = this._drag;
    if (drag && !drag.touch) {
      const at = secondsAt(drag.m, drag.svgEl, x); // the scale from when the drag began: it does not shift under the pointer
      if (at != null) this._setMark(drag.area, drag.station, drag.run, this._clampMark(drag.run, at), false);
    }
    cancelAnimationFrame(this._frame);
    this._frame = requestAnimationFrame(() => (this._hover = secondsAt(m, svgEl, x)));
    if (this._hover != null) this._lastHover = this._hover;
  }

  // "read again from the history": the recorder has every reading – a run missing its start gets it back
  _renderReread(area, station, run) {
    const t = this._t;
    const busy = this._reread?.id === run.id && this._reread.loading;
    return html`<button class="icon sm" aria-label=${t("runs.reread")} title=${t("runs.reread_hint")} ?disabled=${!!this._reread?.loading}
      @click=${() => this._rereadRun(area.entry_id, station.id, run.id)}><ha-icon icon=${busy ? "mdi:timer-sand" : "mdi:history"}></ha-icon></button>`;
  }

  async _rereadRun(entryId, stationId, id) {
    if (this._reread?.loading) return;
    this._reread = { id, key: this._runsKey(entryId, stationId), loading: true };
    await this._flushMarks(); // a mark just moved is saved first (it is moved along with the run)
    try {
      const res = await this.hass.callWS({ type: `${WS}/runs/reread`, entry_id: entryId, station_id: stationId, run_id: id });
      this._reread = { id, key: this._runsKey(entryId, stationId), result: res.run ? "done" : "nothing" };
      if (res.run) {
        await this._loadRuns(entryId, stationId);
        await this._loadRun(entryId, stationId, id);
      }
    } catch (err) {
      this._reread = { id, key: this._runsKey(entryId, stationId), result: "error", code: err?.code, text: err?.message || String(err) };
    }
  }

  _renderRereadNote(run, key) {
    const r = this._reread;
    if (!r || r.id !== run.id || r.key !== key || r.loading) return nothing;
    const t = this._t;
    if (r.result === "done") return html`<div class="note">${t("runs.reread_done", { time: formatDuration(run.duration) })}</div>`;
    if (r.result === "nothing") return html`<div class="note">${t("runs.reread_nothing")}</div>`;
    const known = ["no_recorder", "other_sensor", "not_found"].includes(r.code);
    return html`<div class="err" role="alert">${known ? t(`runs.reread_${r.code}`) : r.text}</div>`;
  }

  _renderDeleteRun(area, station, run) {
    const t = this._t;
    if (this._confirm === run.id) {
      return html`<span class="confirm">${t("runs.delete_confirm")}
        <button class="btn sm danger filled" ?disabled=${this._deleting} @click=${() => this._deleteRun(area.entry_id, station.id, run.id)}>${t("delete")}</button>
        <button class="btn sm" @click=${() => (this._confirm = null)}>${t("cancel")}</button></span>`;
    }
    return html`<button class="icon sm" aria-label=${t("runs.delete")} title=${t("runs.delete")} @click=${() => (this._confirm = run.id)}><ha-icon icon="mdi:delete-outline"></ha-icon></button>`;
  }

  // a small bar per run: its length, the saved and the draft ring
  _strip(run, pv, differs, mark = null) {
    const end = Math.max(run.duration || 0, mark ?? 0, ...(pv ? [pv.saved, pv.draft].filter((x) => x && x.rang != null).map((x) => x.start + x.rang) : []), 1);
    const x = (s) => Math.min(100, (s / end) * 100);
    const line = (res, dash) => res && res.rang != null && !res.blip
      ? svg`<line x1="${x(res.start + res.rang)}%" x2="${x(res.start + res.rang)}%" y1="1" y2="13" class="${res.trigger === "cancelled" ? "cx" : ""} ${dash ? "dash" : ""}"></line>` : nothing;
    return html`<svg class="strip" width="100%" height="14" aria-hidden="true"><rect x="0" y="5" width="${x(run.duration || 0)}%" height="4" rx="2"></rect>
      ${mark != null ? svg`<line x1="${x(mark)}%" x2="${x(mark)}%" y1="0" y2="14" class="mk"></line>` : nothing}
      ${pv ? line(pv.saved, false) : nothing}${pv && differs ? line(pv.draft, true) : nothing}</svg>`;
  }

  _renderRunList(area, station, d, data, preview) {
    const t = this._t;
    const all = [...(data.current ? [data.current] : []), ...(data.runs || [])];
    if (!all.length) return nothing;
    const key = this._runsKey(area.entry_id, station.id);
    const selected = this._run?.key === key ? this._run.id : null;
    const differs = this._draftDiffers(station, d);
    const open = !this.narrow || this._fold.runs !== false;
    const stored = all.filter((r) => !r.running).length;
    const usual = this._usual(station);
    const closed = `${t("runs.summary", { count: stored })}${usual ? ` · ${t("now.usual", { time: usual })}` : ""}`;
    const pvOf = (r) => (r.running ? preview?.current : preview?.runs.find((x) => x.id === r.id));
    const learning = this._learning(d);
    const tol = Number(d.power.tolerance ?? 60);
    const anyMarked = learning && all.some((r) => r.marks?.done != null);
    const markOf = (r) => (learning ? r.marks?.done ?? null : null);
    const text = (r, pv) => {
      if (!pv) return "";
      const one = (res) => (res.blip ? "–" : res.rang != null ? formatDuration(res.start + res.rang) : "–");
      const rings = differs ? `${one(pv.saved)} → ${one(pv.draft)}` : one(pv.saved);
      return markOf(r) != null ? `${formatDuration(markOf(r))} · ${rings}` : rings;
    };
    const tagOf = (r, pv) => {
      if (r.running) return html`<span class="tag live">${t("runs.tag.running")}</span>`;
      if (learning && r.skip) return html`<span class="tag">${t("runs.tag.left_out")}</span>`;
      if (learning && this._isOutlier(key, r.id)) return html`<span class="tag bad">${t("runs.tag.outlier")}</span>`;
      if (pv && markOf(r) != null) {
        const v = this._verdict(differs ? pv.draft : pv.saved, markOf(r), tol);
        return html`<span class="tag ${v.kind}">${t(`runs.tag.${v.kind}`)}</span>`;
      }
      if (pv && differs) {
        if (pv.draft.blip) return html`<span class="tag warn">${t("runs.tag.blip")}</span>`;
        if (pv.draft.trigger === "never") return html`<span class="tag warn">${t("runs.tag.never")}</span>`;
        if (pv.draft.trigger === "cancelled") return html`<span class="tag">${t("runs.tag.cancelled")}</span>`;
        const delta = this._delta(pv);
        if (delta && delta !== t("delta.same")) return html`<span class="dl">${delta}</span>`;
      }
      if (this._otherSensor(r, station)) return html`<span class="tag">${t("runs.tag.other_sensor")}</span>`;
      if (r.source === "history") return html`<span class="tag">${t("runs.tag.history")}</span>`;
      if (anyMarked) return html`<span class="tag">${t("runs.tag.mark_it")}</span>`;
      return nothing;
    };
    return html`<div class="card">
      <button class="ch fold" ?disabled=${!this.narrow} aria-expanded=${open ? "true" : "false"} @click=${() => this._setFold("runs", !open)}>
        <span>${t("runs.title")} <span class="chs">– ${open ? (anyMarked ? t(differs ? "runs.mark_saved_draft" : "runs.mark_saved") : differs ? t("runs.saved_draft") : t("runs.kept", { count: data.keep ?? 30 })) : closed}</span></span>
        ${this.narrow ? html`<ha-icon icon=${open ? "mdi:chevron-up" : "mdi:chevron-down"}></ha-icon>` : nothing}
      </button>
      ${open
        ? html`<div class="runs" role="listbox" aria-label=${t("runs.title")}>
            ${all.map((r) => {
              const pv = pvOf(r);
              return html`<button class="runrow ${r.id === selected ? "sel" : ""}" role="option" aria-selected=${r.id === selected ? "true" : "false"} @click=${() => this._loadRun(area.entry_id, station.id, r.id)}>
                <span class="rw">${r.running ? t("runs.now") : this._runWhen(r, !this.narrow)}</span>
                <span class="rd">${formatDuration(r.duration)}</span>
                <span class="rs">${this._strip(r, pv, differs, markOf(r))}</span>
                <span class="rr">${text(r, pv)}</span>
                <span class="rt">${tagOf(r, pv)}</span>
              </button>`;
            })}
          </div>
          ${learning ? html`<div class="outls">${this._renderOutliers(area, station, data)}</div>` : nothing}`
        : nothing}
    </div>`;
  }

  _unit(station) {
    return this.hass.states[station?.power?.sensor]?.attributes?.unit_of_measurement || "W";
  }

  _renderSensor(data, station) {
    const t = this._t;
    const s = data.sensor || {};
    const parts = [];
    if (s.interval != null) parts.push(t("runs.sensor.interval", { time: s.interval < 60 ? `${s.interval} s` : formatDuration(s.interval) }));
    if (s.noise != null) parts.push(t("runs.sensor.noise", { watts: s.noise, unit: this._unit(station) }));
    return html`<div class="card"><div class="ch">${t("runs.sensor")}</div><div class="cb">
      <div>${parts.length ? parts.join(" · ") : t("runs.sensor.unknown")}</div>
      ${this._help(t("runs.sensor.hint"), t("runs.sensor.more"), "sensor")}
    </div></div>`;
  }

  // ------------------------------------------------------------------ bars and dialogs

  _renderSaveBar() {
    const t = this._t;
    return html`<div class="savebar">
      <span>${t("unsaved")}</span><span class="sp"></span>
      <button class="btn" ?disabled=${this._saving} @click=${() => this._discard()}>${t("discard")}</button>
      <button class="btn filled" ?disabled=${this._saving} @click=${() => this._save()}>${this._saving ? t("saving") : t("save")}</button>
    </div>`;
  }

  // a dialog takes the focus, so Escape and the keyboard work in it
  _focusDialog() {
    requestAnimationFrame(() => this.shadowRoot?.querySelector(".dialog")?.focus());
  }

  _renderDialog() {
    const t = this._t;
    const d = this._dialog;
    const close = () => (this._dialog = null);
    let body = nothing;
    let actions = html`<button class="btn" @click=${close}>${t("cancel")}</button>`;
    if (d.type === "add_area") {
      const free = this._config.free_areas;
      body = html`<h2 id="ht-dialog-title">${t("area.add")}</h2>
        ${free.length
          ? html`<div class="sub">${t("area.add_hint")}</div>
              <div class="choices">${free.map((a) => html`<button class="ai" @click=${() => this._createArea(a.area_id)}><span class="ic"><ha-icon icon=${a.icon || "mdi:texture-box"}></ha-icon></span><span class="txt"><b>${a.name}</b></span></button>`)}</div>`
          : html`<div class="sub">${t("area.none_free")}</div>`}`;
    } else if (d.type === "delete_area") {
      body = html`<h2 id="ht-dialog-title">${t("area.delete")}</h2><div class="sub">${t("area.delete_confirm", { area: d.area.name })}</div>`;
      actions = html`${actions}<button class="btn danger filled" @click=${() => this._deleteArea(d.area.entry_id)}>${t("delete")}</button>`;
    } else if (d.type === "delete_station") {
      body = html`<h2 id="ht-dialog-title">${t("station.delete")}</h2><div class="sub">${t("station.delete_confirm", { station: d.station.name })}</div>`;
      actions = html`${actions}<button class="btn danger filled" @click=${() => this._deleteStation(d.area.entry_id, d.station.id)}>${t("delete")}</button>`;
    } else if (d.type === "discard") {
      body = html`<h2 id="ht-dialog-title">${t("discard_title")}</h2><div class="sub">${t("discard_confirm")}</div>`;
      actions = html`${actions}<button class="btn filled" @click=${() => ((this._dialog = null), this._go(d.path, { replace: d.replace, force: true }))}>${t("discard")}</button>`;
    } else if (d.type === "settings") {
      body = html`<h2 id="ht-dialog-title">${t("settings.title")}</h2><div class="sub">${t("settings.hint")}</div>
        ${this._toggle(t("settings.sidebar"), t("settings.sidebar_hint"), this._config.settings.sidebar, (v) => this._saveSettings({ sidebar: v }))}`;
      actions = html`<button class="btn filled" @click=${close}>${t("close")}</button>`;
    }
    return html`<div class="scrim" @click=${(e) => e.target === e.currentTarget && close()} @keydown=${(e) => e.key === "Escape" && close()}>
      <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="ht-dialog-title" tabindex="-1">${body}<div class="actions">${actions}</div></div></div>`;
  }

  static styles = css`
    :host { display: flex; flex-direction: column; height: 100vh; height: 100dvh; background: var(--primary-background-color, #fafafa); color: var(--primary-text-color, #212121);
      font-family: var(--ha-font-family-body, var(--primary-font-family, Roboto, sans-serif)); --ht-p: var(--primary-color, #03a9f4); --ht-d: var(--divider-color, #e0e0e0);
      --ht-s: var(--secondary-text-color, #727272); --ht-card: var(--card-background-color, #fff); --ht-err: var(--error-color, #db4437);
      --ht-tint: color-mix(in srgb, var(--ht-p) 13%, transparent); --ht-line: color-mix(in srgb, var(--ht-p) 45%, transparent);
      --ht-ok: var(--success-color, #43a047); --ht-warn: var(--warning-color, #ffa600); --ht-run: var(--orange-color, #ff9800);
      --ht-fill: var(--input-fill-color, color-mix(in srgb, var(--primary-text-color, #000) 5%, transparent));
      --ht-under: var(--input-idle-line-color, color-mix(in srgb, var(--primary-text-color, #000) 42%, transparent)); }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; background: none; border: none; cursor: pointer; text-align: left; }
    input, select { font: inherit; color: inherit; }
    a { color: var(--ht-p); text-decoration: none; }
    .toolbar { height: var(--header-height, 56px); flex: none; display: flex; align-items: center; gap: 8px; padding: 0 8px 0 4px; background: var(--app-header-background-color, var(--ht-card));
      color: var(--app-header-text-color, inherit); border-bottom: 1px solid var(--app-header-border-bottom, var(--ht-d)); }
    .toolbar .title { flex: 1; font-size: 20px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding-left: 8px; }
    .crumb { color: var(--ht-s); font-size: inherit; padding: 0; }
    .crumb:hover { text-decoration: underline; }
    .icon { width: 40px; height: 40px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; color: inherit; flex: none; }
    .icon:hover { background: rgba(127, 127, 127, 0.12); }
    .icon.sm { width: 34px; height: 34px; --mdc-icon-size: 20px; }
    .icon[disabled] { opacity: 0.3; cursor: default; }
    .menuwrap { position: relative; }
    .menuscrim { position: fixed; inset: 0; z-index: 8; }
    .menu { position: absolute; right: 4px; top: 44px; z-index: 9; min-width: 240px; background: var(--ht-card); border-radius: 8px; padding: 6px 0;
      box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2); }
    .menu button { display: flex; align-items: center; gap: 14px; width: 100%; padding: 10px 16px; font-size: 14.5px; }
    .menu button:hover { background: rgba(127, 127, 127, 0.1); }
    .menu ha-icon { color: var(--ht-s); --mdc-icon-size: 20px; }
    .menu .danger, .menu .danger ha-icon { color: var(--ht-err); }
    .menusep { border-top: 1px solid var(--ht-d); margin: 4px 0; }
    .body { flex: 1; min-height: 0; display: flex; }
    .areas { width: 260px; flex: none; border-right: 1px solid var(--ht-d); background: var(--ht-card); padding: 12px 8px; overflow: auto; }
    :host([narrow]) .areas { width: 100%; border: none; background: none; }
    .h { font-size: 12px; font-weight: 600; letter-spacing: 0.06em; color: var(--ht-s); text-transform: uppercase; margin: 6px 10px 8px; }
    .ai { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-radius: 10px; width: 100%; margin-bottom: 2px; }
    :host([narrow]) .areas .ai { background: var(--ht-card); border: 1px solid var(--ht-d); margin-bottom: 8px; }
    .ai.sel { background: var(--ht-tint); }
    .ai:hover { background: rgba(127, 127, 127, 0.08); }
    .txt { display: flex; flex-direction: column; min-width: 0; flex: 1; }
    .txt b { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .txt span { font-size: 12.5px; color: var(--ht-s); }
    .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--ht-err); flex: none; }
    .ic { width: 38px; height: 38px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex: none; background: var(--ht-tint); color: var(--ht-p); --mdc-icon-size: 20px; }
    .ic.run { background: color-mix(in srgb, var(--ht-run) 15%, transparent); color: var(--ht-run); }
    .ic.ring { background: color-mix(in srgb, var(--ht-err) 15%, transparent); color: var(--ht-err); }
    .add { color: var(--ht-p); font-weight: 500; padding: 10px 12px; }
    .pane { flex: 1; overflow: auto; padding: 20px 28px 100px; min-width: 0; container-type: inline-size; }
    :host([narrow]) .pane { padding: 12px 12px 100px; }
    h1 { font-size: 24px; font-weight: 400; margin: 0 0 10px; }
    .sub { color: var(--ht-s); margin-bottom: 12px; line-height: 1.45; }
    .chips { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 4px; }
    .chip { background: rgba(127, 127, 127, 0.12); border-radius: 8px; padding: 3px 10px; font-size: 12.5px; }
    .btn { color: var(--ht-p); font-weight: 500; padding: 8px 16px; border-radius: 18px; border: 1px solid var(--ht-line); font-size: 14px; white-space: nowrap; }
    .btn:hover { background: var(--ht-tint); }
    .btn.push { margin-left: auto; }
    .btn.filled { background: var(--ht-p); color: var(--text-primary-color, #fff); border-color: var(--ht-p); }
    .btn.danger { color: var(--ht-err); border-color: color-mix(in srgb, var(--ht-err) 40%, transparent); }
    .btn.danger.filled { background: var(--ht-err); color: #fff; }
    .btn[disabled] { opacity: 0.5; }
    .link { color: var(--ht-p); font-weight: 500; padding: 6px 8px; border-radius: 6px; display: inline-flex; align-items: center; gap: 6px; --mdc-icon-size: 18px; }
    .link:hover { background: var(--ht-tint); }
    .link.danger { color: var(--ht-err); }
    .sec { font-size: 12px; font-weight: 600; letter-spacing: 0.06em; color: var(--ht-s); text-transform: uppercase; margin: 22px 0 10px; }
    .sechead { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 14px 0 10px; }
    .sechead .sec { margin: 0; }
    .lc { text-transform: none; font-weight: 400; letter-spacing: 0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
    .card { background: var(--ht-card); border: 1px solid var(--ht-d); border-radius: var(--ha-card-border-radius, 12px); margin-bottom: 14px; display: block; width: 100%; overflow: hidden; }
    .card.station { padding: 14px 16px; margin: 0; }
    .card.ring { border: 2px solid var(--ht-err); }
    .card.run { border: 2px solid color-mix(in srgb, var(--ht-run) 60%, transparent); }
    .ch { padding: 14px 16px 2px; font-size: 16px; font-weight: 500; }
    .chs { font-size: 12.5px; color: var(--ht-s); font-weight: 400; }
    .cb { padding: 10px 16px 14px; }
    .cf { border-top: 1px solid var(--ht-d); padding: 6px 8px; display: flex; flex-wrap: wrap; gap: 2px; }
    .cf a { padding: 6px 10px; border-radius: 6px; font-weight: 500; font-size: 13.5px; }
    .cf a:hover { background: var(--ht-tint); }
    .st { display: flex; align-items: center; gap: 12px; }
    .go { color: var(--ht-s); font-size: 20px; }
    .line { display: flex; justify-content: space-between; align-items: baseline; margin-top: 10px; gap: 8px; font-size: 13.5px; }
    .live { font-variant-numeric: tabular-nums; }
    .live.ring { color: var(--ht-err); }
    .live.quiet { color: var(--ht-s); }
    .watts { font-size: 18px; font-weight: 500; color: color-mix(in srgb, var(--ht-run) 80%, var(--primary-text-color, #000)); font-variant-numeric: tabular-nums; white-space: nowrap; }
    .emptycard { border: 2px dashed var(--ht-d); border-radius: 14px; padding: 26px; display: flex; flex-direction: column; align-items: center; gap: 6px; text-align: center; color: var(--ht-s); }
    .emptycard b { color: var(--primary-text-color); font-size: 16px; font-weight: 500; }
    .emptycard .btn { margin-top: 8px; }
    .qt { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .q { background: var(--ht-card); border: 1px solid var(--ht-d); border-radius: 20px; padding: 7px 14px; }
    .q:not(.static):hover { border-color: var(--ht-line); }
    .q i { color: var(--ht-s); font-style: normal; margin-left: 6px; }
    .column { max-width: 760px; }
    .cols { display: flex; gap: 20px; align-items: flex-start; }
    .col { flex: 1; min-width: 0; max-width: 760px; }
    .colr { width: 320px; flex: none; position: sticky; top: 0; }
    :host([narrow]) .cols { flex-direction: column; gap: 0; align-items: stretch; }
    :host([narrow]) .colr { width: 100%; position: static; order: -1; }
    .fieldwrap + .pair { margin-top: 12px; }
    .pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px 14px; margin: 4px 0; }
    .pair.off { opacity: 0.6; }
    .fieldwrap { display: block; min-width: 0; }
    .ff { position: relative; display: flex; flex-direction: column; background: var(--ht-fill); border-radius: 4px 4px 0 0; border-bottom: 1px solid var(--ht-under); padding: 6px 12px 6px; min-height: 56px; justify-content: center; }
    .ff:focus-within { border-bottom: 2px solid var(--ht-p); }
    .ff:focus-within .fl { color: var(--ht-p); }
    .ff.bad { border-bottom: 2px solid var(--ht-err); }
    .ff.small { min-height: 48px; padding: 4px 10px; }
    .ff input { border: none; background: none; outline: none; padding: 2px 0; width: 100%; font-size: 15px; }
    .ff .unit { position: absolute; right: 12px; bottom: 9px; color: var(--ht-s); font-size: 14px; }
    .fl { font-size: 12px; color: var(--ht-s); }
    .help { display: block; font-size: 12.5px; color: var(--ht-s); margin: 6px 2px 0; line-height: 1.45; }
    .more { color: var(--ht-p); font-weight: 500; font-size: 12.5px; display: inline-flex; align-items: center; gap: 3px; --mdc-icon-size: 15px; padding: 0 2px; }
    .moretext { margin-top: 6px; padding: 8px 10px; border-radius: 8px; background: var(--ht-fill); }
    .err { color: var(--ht-err); font-size: 12px; margin: 4px 2px 0; }
    .starts { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-top: 14px; }
    .seg { display: inline-flex; border: 1px solid var(--ht-line); border-radius: 18px; overflow: hidden; }
    .seg button { padding: 8px 18px; color: var(--ht-p); display: inline-flex; align-items: center; gap: 4px; --mdc-icon-size: 16px; }
    .seg .on { background: var(--ht-tint); font-weight: 600; }
    .radios { display: flex; flex-direction: column; }
    .radio { display: flex; gap: 14px; align-items: flex-start; padding: 10px 4px; border-bottom: 1px solid var(--ht-d); width: 100%; }
    .radio:last-child { border: none; }
    .rb { width: 20px; height: 20px; border-radius: 50%; border: 2px solid var(--ht-s); flex: none; margin-top: 1px; }
    .rb.on { border-color: var(--ht-p); background: radial-gradient(var(--ht-p) 42%, transparent 48%); }
    .lbl { display: flex; flex-direction: column; }
    .lbl b { font-weight: 500; }
    .lbl span { font-size: 12.5px; color: var(--ht-s); }
    .sub { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--ht-d); }
    .sentence { font-size: 15px; line-height: 2.7; }
    .steps.many { display: flex; flex-direction: column; }
    .steps.many .step { display: grid; grid-template-columns: auto auto 1fr auto; gap: 0 8px; align-items: start; border: 1px solid var(--divider-color); border-radius: 10px; padding: 4px 10px; background: var(--card-background-color); }
    .steps.many .step .err { grid-column: 3 / 5; }
    .step .handle { cursor: grab; color: var(--secondary-text-color); padding-top: 10px; }
    .step .num { display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; margin-top: 10px; border-radius: 50%; background: var(--secondary-text-color); color: #fff; font-size: 12px; font-weight: 600; }
    .step:last-child .num { background: var(--ht-step, #00897b); }
    .step .tools { display: flex; gap: 2px; padding-top: 8px; }
    .step .tools .icon { border: 0; background: none; color: var(--secondary-text-color); cursor: pointer; font-size: 13px; padding: 4px 6px; border-radius: 6px; }
    .step .tools .icon:disabled { opacity: 0.3; cursor: default; }
    .step .tools .icon:not(:disabled):hover { background: var(--secondary-background-color); }
    .then { margin: 2px 0 2px 18px; padding: 4px 0 4px 14px; border-left: 2px dashed var(--divider-color); font-size: 12px; font-weight: 700; letter-spacing: 0.06em; color: var(--secondary-text-color); text-transform: uppercase; }
    .addstep { margin: 8px 0 4px; }
    .addstep .link { border: 0; background: none; color: var(--primary-color); cursor: pointer; font: inherit; padding: 0; }
    @container (max-width: 560px) { .steps.many .step { grid-template-columns: auto 1fr auto; } .steps.many .step .handle { display: none; } .steps.many .step .err { grid-column: 2 / 4; } }
    .inl { display: inline-flex; align-items: baseline; gap: 3px; background: var(--ht-fill); border-bottom: 1px solid var(--ht-under); border-radius: 4px 4px 0 0; padding: 0 8px; margin: 0 2px; line-height: 1.9; }
    .inl:focus-within { border-bottom: 2px solid var(--ht-p); }
    .inl.bad { border-bottom: 2px solid var(--ht-err); }
    .inl input { border: none; background: none; outline: none; font-weight: 500; font-size: 15px; padding: 0; min-width: 3ch; text-align: right; -moz-appearance: textfield; }
    .inl input::-webkit-inner-spin-button, .inl input::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
    .inl i { font-style: normal; color: var(--ht-s); font-size: 13px; }
    .inlsel { color-scheme: light dark; background: var(--ht-fill); border: none; border-bottom: 1px solid var(--ht-under); border-radius: 4px 4px 0 0; padding: 4px 6px; font-weight: 500; font-size: 15px; margin: 0 2px; }
    .row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 0; }
    .row.off { opacity: 0.5; }
    .switch { width: 36px; height: 14px; appearance: none; border-radius: 7px; background: #bdbdbd; position: relative; cursor: pointer; flex: none; margin: 0 4px; }
    .switch:checked { background: color-mix(in srgb, var(--ht-p) 45%, transparent); }
    .switch::after { content: ""; position: absolute; top: -3px; left: -4px; width: 20px; height: 20px; border-radius: 50%; background: #fafafa; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35); transition: left 0.15s; }
    .switch:checked::after { left: 20px; background: var(--ht-p); }
    .presets { display: flex; flex-direction: column; }
    .preset { display: grid; grid-template-columns: 24px 1.3fr 0.7fr 1.6fr auto; gap: 10px; align-items: center; padding: 6px 0; border-bottom: 1px solid var(--ht-d); }
    :host([narrow]) .preset { grid-template-columns: 1fr 92px auto; }
    :host([narrow]) .preset.open label:nth-of-type(3) { grid-column: 1 / 3; }
    .preset .rowerr { grid-column: 1 / -1; }
    .preset .rowerr:empty { display: none; }
    .handle { color: var(--ht-s); cursor: grab; display: flex; justify-content: center; }
    .acts { display: flex; }
    .presetfoot { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 12px; }
    .presetfoot .help { margin: 0; }
    .now .nowh { font-size: 12px; color: var(--ht-s); letter-spacing: 0.06em; text-transform: uppercase; font-weight: 600; display: flex; align-items: center; gap: 6px; }
    .livedot { width: 8px; height: 8px; border-radius: 50%; background: var(--ht-ok); }
    .now .big { font-size: 20px; font-variant-numeric: tabular-nums; }
    .savebar { position: fixed; left: 0; right: 0; bottom: 0; min-height: 60px; background: var(--ht-card); border-top: 1px solid var(--ht-d); box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.12);
      display: flex; align-items: center; gap: 10px; padding: 8px 20px calc(8px + env(safe-area-inset-bottom, 0px)); z-index: 5; }
    :host(:not([narrow])) .savebar { left: var(--ha-sidebar-width, 0px); }
    .savebar .sp { flex: 1; }
    .notice { border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; }
    .notice.ok { background: color-mix(in srgb, var(--ht-ok) 14%, transparent); }
    .notice.warn { background: color-mix(in srgb, var(--ht-warn) 18%, transparent); }
    .notice.error { background: color-mix(in srgb, var(--ht-err) 12%, transparent); color: var(--ht-err); }
    .msg { padding: 40px; color: var(--ht-s); }
    .empty { text-align: center; padding: 60px 20px; color: var(--ht-s); --mdc-icon-size: 48px; }
    .scrim { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.4); display: flex; align-items: center; justify-content: center; z-index: 10; padding: 16px; }
    .dialog { background: var(--ht-card); border-radius: 20px; padding: 20px 22px; width: 440px; max-width: 100%; max-height: 90vh; overflow: auto; outline: none; }
    .dialog h2 { margin: 0 0 8px; font-size: 20px; font-weight: 400; }
    .choices { display: flex; flex-direction: column; gap: 4px; max-height: 50vh; overflow: auto; }
    .actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
    /* last, so it wins over the column rules above: at medium widths form and Now stack */
    .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--ht-d); margin: -4px 0 14px; }
    .tabs button { padding: 10px 16px; color: var(--ht-s); font-weight: 500; border-bottom: 2px solid transparent; margin-bottom: -1px; }
    .tabs button.on { color: var(--ht-p); border-bottom-color: var(--ht-p); }
    .tabs button:hover { background: var(--ht-tint); }
    .runh { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 44px; padding-top: 8px; }
    .confirm { display: inline-flex; align-items: center; gap: 6px; font-size: 13.5px; font-weight: 400; }
    .btn.sm { padding: 4px 12px; font-size: 13px; }
    .tag { font-size: 11.5px; font-weight: 500; padding: 2px 8px; border-radius: 10px; background: var(--ht-fill); color: var(--ht-s); white-space: nowrap; }
    .tag.live { background: color-mix(in srgb, var(--ht-run) 16%, transparent); color: var(--ht-run); }
    .chartbox { width: 100%; touch-action: pan-y; }
    .zoombar { display: flex; align-items: center; justify-content: flex-end; gap: 2px; margin: 0 0 -4px; --mdc-icon-size: 18px; }
    .zoombar .chs { margin-right: 6px; }
    .zoombar .icon[disabled] { opacity: 0.35; cursor: default; }
    .zoombar .icon.on { color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 12%, transparent); }
    .ovbox { width: 100%; touch-action: none; cursor: grab; margin-top: 2px; }
    .ovbox svg { display: block; }
    .overview .ovbg { fill: var(--secondary-background-color); }
    .overview .ovcurve { fill: none; stroke: var(--ht-p); stroke-width: 1; opacity: 0.7; }
    .overview .ovwin { fill: color-mix(in srgb, var(--primary-color) 14%, transparent); stroke: var(--primary-color); stroke-width: 1.5; }
    .chart { display: block; overflow: visible; }
    .chart .grid { stroke: var(--ht-d); stroke-width: 1; }
    .chart .axis { stroke: var(--ht-under); stroke-width: 1; }
    .chart .ax { fill: var(--ht-s); font-size: 11px; }
    .chart .curve { fill: none; stroke: var(--ht-p); stroke-width: 2; stroke-linejoin: round; }
    .chart .area { fill: var(--ht-tint); stroke: none; }
    .chart .thr { stroke: var(--ht-s); stroke-width: 1.2; stroke-dasharray: 5 4; }
    .chart .ignored { fill: color-mix(in srgb, var(--ht-s) 12%, transparent); }
    .chart .unknown { fill: color-mix(in srgb, var(--ht-s) 7%, transparent); }
    .chart .zone { fill: var(--ht-s); font-size: 11px; }
    .chart .stop { stroke: var(--ht-s); stroke-width: 1; stroke-dasharray: 2 3; }
    .chart .rang { stroke: var(--ht-err); stroke-width: 2; }
    .chart .mk { fill: var(--ht-s); font-size: 11.5px; }
    .chart .mk.ring { fill: var(--ht-err); font-weight: 600; }
    .chart .hover { stroke: var(--primary-text-color, #212121); stroke-width: 1; opacity: 0.35; }
    .chart .dot { fill: var(--ht-card); stroke: var(--ht-p); stroke-width: 2; }
    .readout { font-size: 12.5px; color: var(--ht-s); text-align: right; min-height: 18px; font-variant-numeric: tabular-nums; }
    .legend { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 12.5px; color: var(--ht-s); margin: 2px 0 10px; }
    .legend span { display: inline-flex; align-items: center; gap: 6px; }
    .sw { display: inline-block; width: 18px; height: 0; border-top: 2px solid var(--ht-p); }
    .sw.thr { border-top: 1.5px dashed var(--ht-s); }
    .sw.ign { height: 10px; border: none; background: color-mix(in srgb, var(--ht-s) 18%, transparent); }
    .sw.rang { width: 0; height: 14px; border-top: none; border-left: 2px solid var(--ht-err); }
    .runinfo { line-height: 1.5; }
    .ch.fold { display: flex; align-items: center; justify-content: space-between; width: 100%; padding-bottom: 10px; cursor: default; }
    :host([narrow]) .ch.fold { cursor: pointer; }
    .runs { border-top: 1px solid var(--ht-d); max-height: 420px; overflow: auto; }
    .runrow { display: grid; grid-template-columns: minmax(118px, 1fr) 44px minmax(40px, 80px) 86px 92px; align-items: center; gap: 8px; width: 100%; padding: 10px 16px;
      border-bottom: 1px solid var(--ht-d); font-variant-numeric: tabular-nums; }
    .runrow:last-child { border-bottom: none; }
    .runrow:hover { background: rgba(127, 127, 127, 0.06); }
    .runrow.sel { background: var(--ht-tint); }
    .runrow .rw { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .runrow .rs { min-width: 0; }
    .runrow .rd, .runrow .rr { color: var(--ht-s); text-align: right; display: inline-flex; align-items: center; justify-content: flex-end; gap: 3px; --mdc-icon-size: 15px; }
    .runrow .rt { text-align: right; }
    :host([narrow]) .runrow { grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: "w d" "s r" "t t"; row-gap: 4px; }
    :host([narrow]) .runrow .rw { grid-area: w; } :host([narrow]) .runrow .rd { grid-area: d; }
    :host([narrow]) .runrow .rs { grid-area: s; } :host([narrow]) .runrow .rr { grid-area: r; }
    :host([narrow]) .runrow .rt { grid-area: t; text-align: left; }
    :host([narrow]) .runrow .rt:empty { display: none; }
    .cols.wide .col { max-width: 1120px; }
    .ident { display: grid; grid-template-columns: minmax(180px, 1.2fr) minmax(170px, 1fr) auto; gap: 10px 16px; align-items: start; }
    .ident .starts { margin-top: 6px; }
    .identhelp { grid-column: 1 / -1; margin-top: -6px; }
    :host([narrow]) .ident { grid-template-columns: 1fr; gap: 4px; }
    :host([narrow]) .ident .starts { margin-top: 2px; }
    .curveh { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding-bottom: 6px; }
    .curvet { display: flex; flex-direction: column; gap: 2px; }
    .curvet b { font-weight: 500; }
    .curveh .links { display: flex; gap: 4px; font-size: 13.5px; font-weight: 500; }
    .curveh .links a { padding: 2px 6px; border-radius: 6px; }
    .curveh .links a:hover { background: var(--ht-tint); }
    .card.curve.run { border: 2px solid color-mix(in srgb, var(--ht-run) 60%, transparent); }
    .card.curve.ring { border: 2px solid var(--ht-err); }
    .runh2 { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 36px; font-weight: 500; }
    .runh2 .tag { margin-left: 6px; }
    .note { background: color-mix(in srgb, var(--ht-warn) 14%, transparent); border-radius: 8px; padding: 9px 12px; margin-bottom: 10px; font-size: 13px; line-height: 1.45; }
    .changed { font-size: 12px; font-weight: 500; color: color-mix(in srgb, var(--ht-warn) 80%, #000); background: color-mix(in srgb, var(--ht-warn) 15%, transparent); border-radius: 10px; padding: 2px 8px; margin-left: 6px; }
    .opts { display: flex; flex-direction: column; gap: 8px; }
    .opt { border: 1px solid var(--ht-d); border-radius: 10px; padding: 0 12px; }
    .opt.on { border: 2px solid var(--ht-p); background: color-mix(in srgb, var(--ht-p) 4%, transparent); }
    .opt .radio { border: none; }
    .optsub { margin: 0 0 12px 34px; padding-top: 10px; border-top: 1px dashed var(--ht-d); }
    :host([narrow]) .optsub { margin-left: 4px; }
    .ql { font-size: 12px; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: var(--ht-s); margin-bottom: 2px; }
    .preview { background: var(--ht-fill); border-radius: 8px; padding: 10px 12px; margin: 8px 0 4px; line-height: 1.6; }
    .preview .warn { color: color-mix(in srgb, var(--ht-run) 85%, #000); }
    .duo { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.35fr); gap: 0 16px; align-items: start; }
    :host([narrow]) .duo { grid-template-columns: 1fr; }
    :host([narrow]) .duor { order: -1; }
    .chart .win { fill: color-mix(in srgb, var(--ht-err) 8%, transparent); }
    .chart .zone.win, .chart .zone.lvl { fill: var(--ht-err); }
    .chart .level { stroke: var(--ht-err); stroke-width: 1.2; stroke-dasharray: 2 3; }
    .chart .mark { stroke: var(--ht-err); stroke-width: 2; }
    .chart .mark.dash { stroke-dasharray: 4 3; }
    .chart .mk.it { font-style: italic; }
    .sw.rang.dash { border-left-style: dashed; }
    .sw.win { height: 10px; border: none; background: color-mix(in srgb, var(--ht-err) 14%, transparent); }
    .strip { display: block; overflow: visible; }
    .strip rect { fill: var(--ht-d); }
    .strip line { stroke: var(--ht-err); stroke-width: 2; }
    .strip line.dash { stroke-dasharray: 3 2; }
    .strip line.cx { stroke: var(--ht-run); }
    .dl { font-size: 12.5px; font-weight: 500; color: var(--ht-ok); white-space: nowrap; }
    .tag.warn { background: color-mix(in srgb, var(--ht-run) 16%, transparent); color: color-mix(in srgb, var(--ht-run) 85%, #000); }
    .runacts { display: inline-flex; align-items: center; gap: 6px; }
    .chartbox.marking { cursor: crosshair; }
    .chartbox:focus-visible { outline: 2px solid var(--ht-ok); outline-offset: 2px; border-radius: 4px; }
    .chart .done { stroke: var(--ht-ok); stroke-width: 2.5; }
    .sw.pinsw { width: 0; height: 14px; border-top: none; border-left: 2px dashed var(--secondary-text-color); }
    .chart .pinl { stroke: var(--secondary-text-color); stroke-width: 1.2; stroke-dasharray: 3 3; }
    .chart .pinc { fill: var(--secondary-text-color); }
    .chart .pin.last .pinc { fill: var(--ht-step, #00897b); }
    .chart .pint { fill: #fff; font-size: 11px; font-weight: 600; pointer-events: none; }
    .chart .grip { fill: var(--ht-ok); cursor: ew-resize; }
    .chart .gripl { stroke: #fff; stroke-width: 1.6; }
    .chart .tol { fill: color-mix(in srgb, var(--ht-ok) 11%, transparent); }
    .chart .zone.tolt, .chart .mk.donet { fill: var(--ht-ok); }
    .chart .mk.donet { font-weight: 600; }
    .sw.done { width: 0; height: 14px; border-top: none; border-left: 2.5px solid var(--ht-ok); }
    .sw.tol { height: 10px; border: none; background: color-mix(in srgb, var(--ht-ok) 18%, transparent); }
    .mbar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; background: color-mix(in srgb, var(--ht-ok) 10%, transparent); border-radius: 8px; padding: 8px 10px; margin: 4px 0 8px; font-size: 13.5px; }
    .mbar b { color: color-mix(in srgb, var(--ht-ok) 85%, #000); font-weight: 600; }
    .mbar.ask { background: var(--ht-fill); }
    .mbar.bad { background: color-mix(in srgb, var(--ht-err) 10%, transparent); }
    .mbar.bad b { color: var(--ht-err); }
    .mbar .sp { flex: 1; }
    .btn.ok { color: color-mix(in srgb, var(--ht-ok) 85%, #000); border-color: color-mix(in srgb, var(--ht-ok) 45%, transparent); display: inline-flex; align-items: center; gap: 4px; --mdc-icon-size: 16px; }
    .btn.filled.ok { background: var(--ht-ok); color: #fff; border-color: var(--ht-ok); }
    .v.on_time { color: color-mix(in srgb, var(--ht-ok) 85%, #000); }
    .v.early { color: var(--ht-err); }
    .v.late { color: color-mix(in srgb, var(--ht-run) 85%, #000); }
    .tag.on_time { background: color-mix(in srgb, var(--ht-ok) 14%, transparent); color: color-mix(in srgb, var(--ht-ok) 85%, #000); }
    .tag.early, .tag.bad { background: color-mix(in srgb, var(--ht-err) 12%, transparent); color: var(--ht-err); }
    .tag.late { background: color-mix(in srgb, var(--ht-run) 16%, transparent); color: color-mix(in srgb, var(--ht-run) 85%, #000); }
    .strip line.mk { stroke: var(--ht-ok); stroke-width: 2; }
    .suggest { margin-top: 10px; }
    .sprog { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 8px 0; }
    .sbar { flex: 1 1 200px; height: 8px; border-radius: 4px; background: var(--secondary-background-color); overflow: hidden; }
    .sbar i { display: block; height: 100%; background: var(--primary-color); transition: width 0.4s ease; }
    .sugrow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .sugrow .btn { display: inline-flex; align-items: center; gap: 6px; --mdc-icon-size: 18px; }
    .sugrow .btn[disabled] { opacity: 0.5; cursor: default; }
    .sug { border: 1px solid var(--ht-d); border-radius: 10px; padding: 10px 12px; margin-top: 8px; background: var(--ht-card); }
    .sug.best { border: 2px solid var(--ht-ok); }
    .sugh { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 4px; }
    .outls { padding: 0 16px; }
    .outl { border: 1px solid color-mix(in srgb, var(--ht-err) 30%, transparent); background: color-mix(in srgb, var(--ht-err) 5%, transparent); border-radius: 10px; padding: 10px 12px; margin: 10px 0; line-height: 1.45; }
    .outla { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
    @container (max-width: 920px) {
      .duo { grid-template-columns: 1fr; }
      .duor { order: -1; }
    }
    @container (max-width: 780px) {
      .cols { flex-direction: column; gap: 0; align-items: stretch; }
      .colr { width: 100%; position: static; order: -1; }
      .col { max-width: none; }
    }
  `;
}

// a newer version loaded into an open tab must not define the element twice
if (!customElements.get("home-timer-panel")) customElements.define("home-timer-panel", HomeTimerPanel);
