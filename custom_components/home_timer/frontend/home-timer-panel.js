// Home Timer: the configuration panel.
// Areas on the left, the chosen area or station on the right (on a phone one at a
// time). Editors work on a draft until saved: one bar saves or discards it, because
// saving reloads the area. Live state comes from the entities, not from the panel's
// own data. The checks are the integration's (panel/edit.py); errors show at the field.

import { LitElement, html, css, nothing } from "./vendor/lit.js";
import { clone, formatDuration, looseTimers, parseDuration, parseRoute, same, stationLive, tempId, translator } from "./helpers.js";

const PANEL = "home-timer";
const WS = "home_timer";

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
    this._errors = {}; // key → {field: error}
    this._t = (k) => k;
    this._lang = null;
    this._dragIndex = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._timer = setInterval(() => (this._now = Date.now()), 1000);
    this._beforeUnload = (e) => {
      if (this._dirtyKey()) e.preventDefault();
    };
    window.addEventListener("beforeunload", this._beforeUnload);
    if (this.hass && !this._config) this._load();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    clearInterval(this._timer);
    window.removeEventListener("beforeunload", this._beforeUnload);
  }

  updated(changed) {
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

  _draft(key, make) {
    if (!this._drafts[key]) {
      const base = make();
      if (!base) return null;
      this._base[key] = base;
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
    this._drafts = drafts;
    this._errors = { ...this._errors, [key]: {} };
  }

  static presetDraft(p) {
    return { id: p.id, name: p.name, duration: p.duration, text: formatDuration(p.duration), note: p.note || "" };
  }

  static presetPayload(p) {
    return { id: p.id, name: p.name, duration: p.duration, ...(p.note ? { note: p.note } : {}) };
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
          version: area.version,
          options: draft.options,
          quick_timers: draft.quick_timers.map(HomeTimerPanel.presetPayload),
        });
      } else {
        const station = area.stations.find((s) => s.id === r.station);
        result = await this.hass.callWS({
          type: `${WS}/station/save`,
          entry_id: area.entry_id,
          station_id: station ? station.id : null,
          version: station ? station.version : null,
          station: {
            name: draft.name,
            icon: draft.icon,
            mode: draft.mode,
            presets: draft.presets.map(HomeTimerPanel.presetPayload),
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
      this._notice = { kind: "ok", text: this._t("notice.saved") };
      if (r.page === "station" && r.station === "new") this._go(`station/${area.entry_id}/${result.station_id}`, { replace: true, force: true });
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
    return code ? html`<div class="err">${this._t(`error.${code}`)}</div>` : nothing;
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
    await this.hass.callWS({ type: `${WS}/area/delete`, entry_id: entryId });
    this._drop(`options:${entryId}`);
    await this._load();
    this._go("", { force: true });
  }

  async _deleteStation(entryId, stationId) {
    this._dialog = null;
    await this.hass.callWS({ type: `${WS}/station/delete`, entry_id: entryId, station_id: stationId });
    this._drop(`station:${entryId}:${stationId}`);
    await this._load();
    this._go(`area/${entryId}`, { force: true });
  }

  async _saveSettings(settings) {
    const result = await this.hass.callWS({ type: `${WS}/settings/save`, ...settings });
    this._config = { ...this._config, settings: result.settings };
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
        <div class="title">${crumb ? html`<span class="crumb" @click=${() => this._go(`area/${area.entry_id}`)}>${crumb}</span> › ` : nothing}${title}</div>
        <button class="icon" aria-label=${t("settings.title")} @click=${() => (this._dialog = { type: "settings" })}><ha-icon icon="mdi:cog-outline"></ha-icon></button>
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
        return html`<button class="ai ${selected?.entry_id === a.entry_id ? "sel" : ""}" @click=${() => this._go(`area/${a.entry_id}`)}>
          <span class="ic"><ha-icon icon=${a.icon || "mdi:texture-box"}></ha-icon></span>
          <span class="txt"><b>${a.name}</b><span>${t("area.stations", { count: a.stations.length })}${!a.loaded ? ` · ${t(a.disabled ? "area.disabled" : "area.not_loaded")}` : ""}</span></span>
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
    return html`
      ${this.narrow ? nothing : html`<h1>${area.name}</h1>`}
      <div class="chips">
        <span class="chip">${o.ring_until_acknowledged ? t("chip.ring_until_ack") : t("chip.ring_once")}</span>
        ${o.ring_until_acknowledged && o.reminder_minutes ? html`<span class="chip">${t("chip.reminder", { min: o.reminder_minutes })}</span>` : nothing}
        ${o.ring_until_acknowledged && o.auto_dismiss_minutes ? html`<span class="chip">${t("chip.dismiss", { min: o.auto_dismiss_minutes })}</span>` : nothing}
        <button class="btn" @click=${() => this._go(`area/${area.entry_id}/options`)}>${t("options.open")}</button>
      </div>
      <div class="sec">${t("stations")}</div>
      <div class="grid">
        ${area.stations.map((s) => this._renderStationCard(area, s))}
        <button class="addcard" @click=${() => this._go(`station/${area.entry_id}/new`)}>+ ${t("station.add")}</button>
      </div>
      <div class="sec">${t("quick_timers")} <span class="lc">– ${t("quick_timers.hint")}</span></div>
      <div class="qt">
        ${area.quick_timers.map((p) => html`<span class="q">${p.name}<i>${formatDuration(p.duration)}</i></span>`)}
        <button class="q addq" @click=${() => this._go(`area/${area.entry_id}/options`)}>${area.quick_timers.length ? t("quick_timers.edit") : `+ ${t("quick_timers.add")}`}</button>
      </div>
      ${loose.length
        ? html`<div class="sec">${t("loose")}</div>
            <div class="qt">${loose.map((x) => html`<span class="q">${x.name}<i>${t(`status.${x.status}`)}${x.ends_at ? ` · ${formatDuration((Date.parse(x.ends_at) - this._now) / 1000)}` : ""}</i></span>`)}</div>`
        : nothing}
    `;
  }

  _liveText(live) {
    const t = this._t;
    if (live.status === "unavailable") return t("live.unavailable");
    if (live.status === "idle") return live.running ? t("live.running_no_timer") : t("status.idle");
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

  _renderStationCard(area, s) {
    const live = stationLive(this.hass, s, this._now);
    const cls = live.status === "ringing" ? "ring" : live.running || live.status === "active" ? "run" : "";
    const icon = s.icon || (s.mode === "power" ? "mdi:power-plug-outline" : "mdi:timer-outline");
    return html`<button class="card station ${cls}" @click=${() => this._go(`station/${area.entry_id}/${s.id}`)}>
      <div class="st"><span class="ic ${cls}"><ha-icon icon=${icon}></ha-icon></span>
        <span class="txt"><b>${s.name}</b><span>${this._stationSummary(s)}</span></span><span class="go">›</span></div>
      <div class="line"><span class="live ${cls}">${this._liveText(live)}</span>
        ${live.watts != null ? html`<span class="watts ${live.running ? "on" : ""}">${Math.round(live.watts)} ${live.unit}</span>` : nothing}</div>
    </button>`;
  }

  // ------------------------------------------------------------------ area options

  _renderOptions(area) {
    const t = this._t;
    const key = `options:${area.entry_id}`;
    const d = this._draft(key, () => this._optionsDraft(area));
    const spec = this._config.specs.options;
    const set = (k, v) => this._change(key, (x) => (x.options[k] = v));
    return html`
      ${this.narrow ? nothing : html`<h1>${t("options.title", { area: area.name })}</h1>`}
      <div class="sub">${t("options.hint")}</div>
      <div class="card form">
        ${this._toggle(t("options.ring_until_acknowledged"), t("options.ring_until_acknowledged_hint"), d.options.ring_until_acknowledged, (v) => set("ring_until_acknowledged", v))}
        ${this._numberRow(key, "reminder_minutes", t("options.reminder_minutes"), t("options.reminder_minutes_hint"), d.options.reminder_minutes, spec.reminder_minutes, "min", (v) => set("reminder_minutes", v), !d.options.ring_until_acknowledged)}
        ${this._numberRow(key, "auto_dismiss_minutes", t("options.auto_dismiss_minutes"), t("options.auto_dismiss_minutes_hint"), d.options.auto_dismiss_minutes, spec.auto_dismiss_minutes, "min", (v) => set("auto_dismiss_minutes", v), !d.options.ring_until_acknowledged)}
        ${this._toggle(t("options.report_missed"), t("options.report_missed_hint"), d.options.report_missed, (v) => set("report_missed", v))}
      </div>
      <div class="sec">${t("quick_timers")}</div>
      <div class="sub">${t("quick_timers.long_hint")}</div>
      ${this._presetTable(key, d.quick_timers, "quick_timers", (fn) => this._change(key, (x) => fn(x.quick_timers)))}
      <div class="sec">${t("danger")}</div>
      <button class="btn danger" @click=${() => (this._dialog = { type: "delete_area", area })}>${t("area.delete")}</button>
    `;
  }

  _toggle(label, hint, value, onChange, disabled = false) {
    return html`<label class="row ${disabled ? "off" : ""}">
      <span class="lbl"><b>${label}</b>${hint ? html`<span>${hint}</span>` : nothing}</span>
      <input type="checkbox" class="switch" .checked=${!!value} ?disabled=${disabled} @change=${(e) => onChange(e.target.checked)} />
    </label>`;
  }

  _numberRow(key, field, label, hint, value, range, unit, onChange, disabled = false) {
    return html`<label class="row ${disabled ? "off" : ""}">
      <span class="lbl"><b>${label}</b>${hint ? html`<span>${hint}</span>` : nothing}${this._err(key, field)}</span>
      <span class="num"><input type="number" inputmode="decimal" .value=${String(value ?? "")} min=${range?.min ?? ""} max=${range?.max ?? ""} ?disabled=${disabled}
        @input=${(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} /><span class="unit">${unit}</span></span>
    </label>`;
  }

  // a sortable list of presets (a station's or the area's quick timers)
  _presetTable(key, presets, prefix, change) {
    const t = this._t;
    const move = (from, to) =>
      change((list) => {
        if (to < 0 || to >= list.length) return;
        const [p] = list.splice(from, 1);
        list.splice(to, 0, p);
      });
    return html`<div class="presets">
      ${presets.map(
        (p, i) => html`<div class="preset" draggable="true"
            @dragstart=${(e) => ((this._dragIndex = i), e.dataTransfer.setData("text/plain", String(i)))}
            @dragover=${(e) => e.preventDefault()}
            @drop=${(e) => (e.preventDefault(), this._dragIndex != null && move(this._dragIndex, i), (this._dragIndex = null))}>
          <span class="handle" title=${t("preset.drag")}>⠿</span>
          <span class="f name"><input .value=${p.name} placeholder=${t("preset.name")} aria-label=${t("preset.name")}
            @input=${(e) => change((l) => (l[i].name = e.target.value))} />${this._err(key, `${prefix}.${i}.name`)}</span>
          <span class="f dur"><input .value=${p.text} placeholder="10:00" aria-label=${t("preset.duration")} inputmode="numeric"
            @input=${(e) => change((l) => ((l[i].text = e.target.value), (l[i].duration = parseDuration(e.target.value))))}
            @blur=${() => change((l) => l[i].duration != null && (l[i].text = formatDuration(l[i].duration)))} />
            ${this._err(key, `${prefix}.${i}.duration`) !== nothing ? this._err(key, `${prefix}.${i}.duration`) : p.text && p.duration == null ? html`<div class="err">${t("error.invalid_duration")}</div>` : nothing}</span>
          <span class="f note"><input .value=${p.note} placeholder=${t("preset.note")} aria-label=${t("preset.note")}
            @input=${(e) => change((l) => (l[i].note = e.target.value))} /></span>
          <span class="acts">
            <button class="icon sm" aria-label=${t("preset.up")} ?disabled=${i === 0} @click=${() => move(i, i - 1)}><ha-icon icon="mdi:chevron-up"></ha-icon></button>
            <button class="icon sm" aria-label=${t("preset.down")} ?disabled=${i === presets.length - 1} @click=${() => move(i, i + 1)}><ha-icon icon="mdi:chevron-down"></ha-icon></button>
            <button class="icon sm" aria-label=${t("preset.delete")} @click=${() => change((l) => l.splice(i, 1))}><ha-icon icon="mdi:delete-outline"></ha-icon></button>
          </span>
        </div>`
      )}
      <button class="add" @click=${() => change((l) => l.push({ id: tempId(), name: "", duration: null, text: "", note: "" }))}>+ ${t("preset.add")}</button>
      <div class="hint">${t("preset.duration_hint")}</div>
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
    const manualControls = d.mode === "manual" || d.power.kind === "start_preset";
    return html`
      <div class="cols">
        <div class="col">
          <div class="pair">
            <label class="f"><span class="fl">${t("station.name")}</span><input .value=${d.name} placeholder=${t("station.name_placeholder")} @input=${(e) => set((x) => (x.name = e.target.value))} />${this._err(key, "name")}</label>
            <label class="f"><span class="fl">${t("station.icon")}</span><span class="withicon"><ha-icon icon=${d.icon || (d.mode === "power" ? "mdi:power-plug-outline" : "mdi:timer-outline")}></ha-icon>
              <input .value=${d.icon} placeholder="mdi:stove" @input=${(e) => set((x) => (x.icon = e.target.value.trim()))} /></span></label>
          </div>
          <div class="sec">${t("station.starts")}</div>
          <div class="seg" role="radiogroup">
            ${["manual", "power"].map((m) => html`<button role="radio" aria-checked=${d.mode === m} class=${d.mode === m ? "on" : ""} @click=${() => set((x) => (x.mode = m))}>${d.mode === m ? "✓ " : ""}${t(`mode.${m}`)}</button>`)}
          </div>
          <div class="hint">${t(`mode.${d.mode}_hint`)}</div>
          ${d.mode === "power" ? this._renderPower(key, d, set) : nothing}
          ${manualControls
            ? html`<div class="sec">${t("presets")}</div>
                ${this._presetTable(key, d.presets, "presets", (fn) => set((x) => fn(x.presets)))}
                <div class="hint">${t("presets.hint")}</div>`
            : nothing}
          ${station
            ? html`<div class="sec">${t("danger")}</div>
                <button class="btn danger" @click=${() => (this._dialog = { type: "delete_station", area, station })}>${t("station.delete")}</button>`
            : nothing}
        </div>
        ${station ? html`<div class="colr">${this._renderNow(area, station)}</div>` : nothing}
      </div>
    `;
  }

  _renderNow(area, station) {
    const t = this._t;
    const live = stationLive(this.hass, station, this._now);
    const cls = live.status === "ringing" ? "ring" : live.running || live.status === "active" ? "run" : "";
    return html`
      <div class="card now ${cls}">
        <div class="sec top">${t("now")}</div>
        <div class="big">${this._liveText(live)}</div>
        ${station.mode === "power"
          ? html`<div class="hint">${live.running ? t("live.appliance_on") : t("live.appliance_off")}${live.watts != null ? ` · ${Math.round(live.watts)} ${live.unit}` : ""}</div>`
          : nothing}
        <div class="hint">${t("now.hint")}</div>
      </div>
      <div class="card links">
        <div class="sec top">${t("in_ha")}</div>
        ${station.device_id ? html`<a href="/config/devices/device/${station.device_id}">${t("link.device")} ↗</a>` : nothing}
        ${station.entities.timer ? html`<a href="/config/entities?search=${encodeURIComponent(station.name)}">${t("link.entities")} ↗</a>` : nothing}
      </div>
    `;
  }

  _powerSensors() {
    return Object.entries(this.hass.states)
      .filter(([id, s]) => id.startsWith("sensor.") && (s.attributes?.device_class === "power" || ["W", "kW"].includes(s.attributes?.unit_of_measurement)))
      .map(([id, s]) => ({ id, name: s.attributes.friendly_name || id }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  _renderPower(key, d, set) {
    const t = this._t;
    const p = d.power;
    const spec = this._config.specs;
    const sensor = p.sensor ? this.hass.states[p.sensor] : null;
    const setP = (k, v) => set((x) => (x.power[k] = v));
    const rule = p.done?.type || "off";
    const ruleSpec = spec.rules[rule];
    return html`
      <div class="sec">${t("power")}</div>
      <label class="f"><span class="fl">${t("power.sensor")}</span>
        <input list="ht-sensors" .value=${p.sensor} placeholder="sensor.kitchen_plug_power" @change=${(e) => setP("sensor", e.target.value.trim())} />
        <datalist id="ht-sensors">${this._powerSensors().map((s) => html`<option value=${s.id}>${s.name}</option>`)}</datalist>
        ${sensor ? html`<span class="liveval">● ${sensor.state} ${sensor.attributes.unit_of_measurement || ""}</span>` : nothing}
        ${this._err(key, "power.sensor")}</label>
      <div class="pair">
        ${this._numField(key, "power.on_above", t("power.on_above"), p.on_above, spec.power.on_above, "W", (v) => setP("on_above", v))}
        ${this._numField(key, "power.off_after", t("power.off_after"), p.off_after, spec.power.off_after, "s", (v) => setP("off_after", v))}
      </div>
      <div class="hint">${t("power.thresholds_hint")}</div>
      <div class="sec">${t("power.kind")}</div>
      <div class="kinds">
        ${spec.kinds.map((k) => html`<button class="kind ${p.kind === k ? "on" : ""}" @click=${() => setP("kind", k)}><b>${p.kind === k ? "✓ " : ""}${t(`kind.${k}`)}</b><span>${t(`kind.${k}_hint`)}</span></button>`)}
      </div>
      ${p.kind === "start_preset"
        ? html`<div class="pair">
              <label class="f"><span class="fl">${t("power.preset")}</span>
                <select @change=${(e) => setP("preset", e.target.value || null)}>
                  <option value="" ?selected=${!p.preset}>–</option>
                  ${d.presets.filter((x) => x.name).map((x) => html`<option value=${x.id} ?selected=${p.preset === x.id}>${x.name}</option>`)}
                </select>${this._err(key, "power.preset")}</label>
            </div>
            ${this._toggle(t("power.cancel_on_off"), t("power.cancel_on_off_hint"), p.cancel_on_off, (v) => setP("cancel_on_off", v))}`
        : nothing}
      ${p.kind === "warn" ? html`<div class="pair">${this._numField(key, "power.warn_after", t("power.warn_after"), p.warn_after, spec.power.warn_after, "min", (v) => setP("warn_after", v))}</div>` : nothing}
      ${p.kind === "until_off"
        ? html`<div class="sec">${t("power.done")}</div>
            <div class="pair">
              <label class="f"><span class="fl">${t("power.rule")}</span>
                <select @change=${(e) => set((x) => (x.power.done = { type: e.target.value, ...spec.rules[e.target.value].defaults }))}>
                  ${Object.keys(spec.rules).map((r) => html`<option value=${r} ?selected=${rule === r}>${t(`rule.${r}`)}</option>`)}
                </select></label>
              ${ruleSpec.fields.map((f) => this._numField(key, `power.done.${f.key}`, t(`rule_field.${f.key}`), p.done[f.key], f, f.unit, (v) => set((x) => (x.power.done[f.key] = v)), f.step))}
            </div>
            <div class="hint">${t(`rule.${rule}_hint`)}</div>`
        : nothing}
    `;
  }

  _numField(key, field, label, value, range, unit, onChange, step = 1) {
    return html`<label class="f"><span class="fl">${label}</span>
      <span class="num"><input type="number" inputmode="decimal" step=${step} .value=${String(value ?? "")} min=${range?.min ?? ""} max=${range?.max ?? ""}
        @input=${(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} /><span class="unit">${unit}</span></span>${this._err(key, field)}</label>`;
  }

  // ------------------------------------------------------------------ bars and dialogs

  _renderSaveBar() {
    const t = this._t;
    return html`<div class="savebar">
      <span>${t("unsaved")}</span><span class="sp"></span>
      <button class="btn light" ?disabled=${this._saving} @click=${() => this._discard()}>${t("discard")}</button>
      <button class="btn filled" ?disabled=${this._saving} @click=${() => this._save()}>${this._saving ? t("saving") : t("save")}</button>
    </div>`;
  }

  _renderDialog() {
    const t = this._t;
    const d = this._dialog;
    const close = () => (this._dialog = null);
    let body = nothing;
    let actions = html`<button class="btn" @click=${close}>${t("cancel")}</button>`;
    if (d.type === "add_area") {
      const free = this._config.free_areas;
      body = html`<h2>${t("area.add")}</h2>
        ${free.length
          ? html`<div class="sub">${t("area.add_hint")}</div>
              <div class="choices">${free.map((a) => html`<button class="ai" @click=${() => this._createArea(a.area_id)}><span class="ic"><ha-icon icon=${a.icon || "mdi:texture-box"}></ha-icon></span><span class="txt"><b>${a.name}</b></span></button>`)}</div>`
          : html`<div class="sub">${t("area.none_free")}</div>`}`;
    } else if (d.type === "delete_area") {
      body = html`<h2>${t("area.delete")}</h2><div class="sub">${t("area.delete_confirm", { area: d.area.name })}</div>`;
      actions = html`${actions}<button class="btn danger filled" @click=${() => this._deleteArea(d.area.entry_id)}>${t("delete")}</button>`;
    } else if (d.type === "delete_station") {
      body = html`<h2>${t("station.delete")}</h2><div class="sub">${t("station.delete_confirm", { station: d.station.name })}</div>`;
      actions = html`${actions}<button class="btn danger filled" @click=${() => this._deleteStation(d.area.entry_id, d.station.id)}>${t("delete")}</button>`;
    } else if (d.type === "discard") {
      body = html`<h2>${t("discard_title")}</h2><div class="sub">${t("discard_confirm")}</div>`;
      actions = html`${actions}<button class="btn filled" @click=${() => ((this._dialog = null), this._go(d.path, { replace: d.replace, force: true }))}>${t("discard")}</button>`;
    } else if (d.type === "settings") {
      body = html`<h2>${t("settings.title")}</h2><div class="sub">${t("settings.hint")}</div>
        ${this._toggle(t("settings.sidebar"), t("settings.sidebar_hint"), this._config.settings.sidebar, (v) => this._saveSettings({ sidebar: v }))}`;
      actions = html`<button class="btn filled" @click=${close}>${t("close")}</button>`;
    }
    return html`<div class="scrim" @click=${(e) => e.target === e.currentTarget && close()}>
      <div class="dialog" role="dialog" aria-modal="true">${body}<div class="actions">${actions}</div></div></div>`;
  }

  static styles = css`
    :host { display: block; height: 100%; background: var(--primary-background-color, #fafafa); color: var(--primary-text-color, #212121);
      font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); --ht-p: var(--primary-color, #03a9f4); --ht-d: var(--divider-color, #e0e0e0);
      --ht-s: var(--secondary-text-color, #727272); --ht-card: var(--card-background-color, #fff); --ht-err: var(--error-color, #db4437); }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; background: none; border: none; cursor: pointer; text-align: left; }
    input, select { font: inherit; color: inherit; }
    .toolbar { height: var(--header-height, 56px); display: flex; align-items: center; gap: 8px; padding: 0 8px 0 4px; background: var(--app-header-background-color, var(--ht-card));
      color: var(--app-header-text-color, inherit); border-bottom: 1px solid var(--app-header-border-bottom, var(--ht-d)); }
    .toolbar .title { flex: 1; font-size: 20px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding-left: 8px; }
    .crumb { color: var(--ht-s); cursor: pointer; }
    .icon { width: 40px; height: 40px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; color: inherit; }
    .icon:hover { background: rgba(127, 127, 127, 0.12); }
    .icon.sm { width: 32px; height: 32px; }
    .icon[disabled] { opacity: 0.3; cursor: default; }
    .body { display: flex; height: calc(100% - var(--header-height, 56px)); }
    .areas { width: 270px; flex: none; border-right: 1px solid var(--ht-d); background: var(--ht-card); padding: 12px 8px; overflow: auto; }
    :host([narrow]) .areas { width: 100%; border: none; background: none; }
    .h { font-size: 12px; font-weight: 600; letter-spacing: 0.06em; color: var(--ht-s); text-transform: uppercase; margin: 6px 10px 8px; }
    .ai { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-radius: 10px; width: 100%; margin-bottom: 2px; }
    :host([narrow]) .areas .ai { background: var(--ht-card); border: 1px solid var(--ht-d); margin-bottom: 8px; }
    .ai.sel { background: rgba(3, 169, 244, 0.12); }
    .ai:hover { background: rgba(127, 127, 127, 0.08); }
    .txt { display: flex; flex-direction: column; min-width: 0; flex: 1; }
    .txt b { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .txt span { font-size: 12.5px; color: var(--ht-s); }
    .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--ht-err); flex: none; }
    .ic { width: 38px; height: 38px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex: none; background: rgba(3, 169, 244, 0.12); color: var(--ht-p); --mdc-icon-size: 20px; }
    .ic.run { background: rgba(255, 152, 0, 0.15); color: #ef6c00; }
    .ic.ring { background: rgba(219, 68, 55, 0.15); color: var(--ht-err); }
    .add { color: var(--ht-p); font-weight: 500; padding: 10px 12px; }
    .pane { flex: 1; overflow: auto; padding: 20px 28px 96px; min-width: 0; }
    :host([narrow]) .pane { padding: 14px 14px 96px; }
    h1 { font-size: 24px; font-weight: 400; margin: 0 0 8px; }
    .sub { color: var(--ht-s); margin-bottom: 12px; line-height: 1.45; }
    .chips { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
    .chip { background: rgba(127, 127, 127, 0.12); border-radius: 8px; padding: 3px 10px; font-size: 12.5px; }
    .btn { color: var(--ht-p); font-weight: 500; padding: 8px 16px; border-radius: 18px; border: 1px solid rgba(3, 169, 244, 0.4); font-size: 14px; }
    .chips .btn { margin-left: auto; }
    .btn.filled { background: var(--ht-p); color: #fff; border-color: var(--ht-p); }
    .btn.danger { color: var(--ht-err); border-color: rgba(219, 68, 55, 0.4); }
    .btn.danger.filled { background: var(--ht-err); color: #fff; }
    .btn[disabled] { opacity: 0.5; }
    .sec { font-size: 12px; font-weight: 600; letter-spacing: 0.06em; color: var(--ht-s); text-transform: uppercase; margin: 22px 0 10px; }
    .sec.top { margin-top: 0; }
    .lc { text-transform: none; font-weight: 400; letter-spacing: 0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
    .card { background: var(--ht-card); border: 1px solid var(--ht-d); border-radius: 14px; padding: 14px 16px; display: block; width: 100%; }
    .card.ring { border: 2px solid var(--ht-err); }
    .card.run { border: 2px solid #ffb74d; }
    .st { display: flex; align-items: center; gap: 12px; }
    .go { color: var(--ht-s); font-size: 20px; }
    .line { display: flex; justify-content: space-between; align-items: baseline; margin-top: 10px; gap: 8px; font-size: 13.5px; }
    .live { font-variant-numeric: tabular-nums; }
    .live.ring { color: var(--ht-err); }
    .watts { font-size: 15px; color: var(--ht-s); font-variant-numeric: tabular-nums; }
    .watts.on { font-size: 18px; font-weight: 500; color: #ef6c00; }
    .addcard { border: 2px dashed var(--ht-d); border-radius: 14px; display: flex; align-items: center; justify-content: center; color: var(--ht-p); font-weight: 500; min-height: 92px; }
    .qt { display: flex; gap: 8px; flex-wrap: wrap; }
    .q { background: var(--ht-card); border: 1px solid var(--ht-d); border-radius: 20px; padding: 7px 14px; }
    .q i { color: var(--ht-s); font-style: normal; margin-left: 6px; }
    .addq { color: var(--ht-p); border-style: dashed; }
    .form { padding: 4px 16px; }
    .row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--ht-d); }
    .row:last-child { border: none; }
    .row.off { opacity: 0.5; }
    .lbl { display: flex; flex-direction: column; }
    .lbl b { font-weight: 500; }
    .lbl span { font-size: 12.5px; color: var(--ht-s); }
    .switch { width: 40px; height: 22px; appearance: none; border-radius: 11px; background: #bdbdbd; position: relative; cursor: pointer; flex: none; transition: background 0.15s; }
    .switch:checked { background: var(--ht-p); }
    .switch::after { content: ""; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 50%; background: #fff; transition: left 0.15s; }
    .switch:checked::after { left: 21px; }
    .num { display: inline-flex; align-items: center; gap: 6px; }
    .num input { width: 96px; }
    .unit { color: var(--ht-s); font-size: 13px; min-width: 22px; }
    input, select { border: 1px solid var(--ht-d); border-radius: 6px; padding: 9px 10px; background: var(--ht-card); width: 100%; min-width: 0; }
    input:focus, select:focus { outline: 2px solid rgba(3, 169, 244, 0.5); border-color: var(--ht-p); }
    .f { display: flex; flex-direction: column; gap: 4px; position: relative; }
    .fl { font-size: 12px; color: var(--ht-s); }
    .liveval { font-size: 12.5px; color: #2e7d32; }
    .withicon { display: flex; align-items: center; gap: 8px; }
    .pair { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px 16px; margin: 8px 0; }
    .err { color: var(--ht-err); font-size: 12px; }
    .hint { font-size: 12.5px; color: var(--ht-s); margin: 6px 2px 0; line-height: 1.45; }
    .presets { background: var(--ht-card); border: 1px solid var(--ht-d); border-radius: 12px; padding: 6px 10px; }
    .preset { display: grid; grid-template-columns: 20px 1.4fr 0.8fr 1.6fr auto; gap: 8px; align-items: start; padding: 6px 0; border-bottom: 1px solid var(--ht-d); }
    :host([narrow]) .preset { grid-template-columns: 1fr 96px auto; }
    :host([narrow]) .preset .handle { display: none; }
    :host([narrow]) .preset .note { grid-column: 1 / 3; grid-row: 2; }
    .handle { color: var(--ht-s); cursor: grab; padding-top: 9px; }
    .acts { display: flex; }
    .seg { display: inline-flex; border: 1px solid rgba(3, 169, 244, 0.5); border-radius: 18px; overflow: hidden; }
    .seg button { padding: 8px 18px; color: var(--ht-p); }
    .seg .on { background: rgba(3, 169, 244, 0.14); font-weight: 600; }
    .kinds { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; margin-bottom: 8px; }
    .kind { background: var(--ht-card); border: 1px solid var(--ht-d); border-radius: 12px; padding: 10px 12px; display: flex; flex-direction: column; gap: 3px; }
    .kind b { font-weight: 500; }
    .kind span { font-size: 12.5px; color: var(--ht-s); }
    .kind.on { border: 2px solid var(--ht-p); }
    .cols { display: flex; gap: 24px; align-items: flex-start; }
    .col { flex: 1; min-width: 0; max-width: 760px; }
    .colr { width: 300px; flex: none; display: flex; flex-direction: column; gap: 12px; position: sticky; top: 0; }
    :host([narrow]) .cols { flex-direction: column; }
    :host([narrow]) .colr { width: 100%; position: static; }
    .now .big { font-size: 18px; font-variant-numeric: tabular-nums; margin-bottom: 4px; }
    .links { display: flex; flex-direction: column; gap: 6px; font-size: 14px; }
    .links a { color: var(--ht-p); text-decoration: none; }
    .savebar { position: fixed; left: 0; right: 0; bottom: 0; height: 60px; background: #263238; color: #fff; display: flex; align-items: center; gap: 10px; padding: 0 20px; z-index: 5; }
    :host(:not([narrow])) .savebar { left: var(--mdc-drawer-width, 0); }
    .savebar .sp { flex: 1; }
    .btn.light { color: #b3e5fc; border-color: #546e7a; }
    .notice { border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; }
    .notice.ok { background: rgba(46, 125, 50, 0.12); color: #1b5e20; }
    .notice.warn { background: rgba(255, 193, 7, 0.18); color: #6d4c00; }
    .notice.error { background: rgba(219, 68, 55, 0.12); color: var(--ht-err); }
    .msg { padding: 40px; color: var(--ht-s); }
    .empty { text-align: center; padding: 60px 20px; color: var(--ht-s); --mdc-icon-size: 48px; }
    .scrim { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.4); display: flex; align-items: center; justify-content: center; z-index: 10; padding: 16px; }
    .dialog { background: var(--ht-card); border-radius: 20px; padding: 20px 22px; width: 440px; max-width: 100%; max-height: 90vh; overflow: auto; }
    .dialog h2 { margin: 0 0 8px; font-size: 20px; font-weight: 400; }
    .choices { display: flex; flex-direction: column; gap: 4px; max-height: 50vh; overflow: auto; }
    .actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
  `;
}

customElements.define("home-timer-panel", HomeTimerPanel);
