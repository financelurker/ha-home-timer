// Home Timer: the configuration panel.
// Areas on the left, the chosen area or station on the right (on a phone one at a
// time). Editors work on a draft until saved: one bar saves or discards it, because
// saving reloads the area. Live state comes from the entities, not from the panel's
// own data. The checks are the integration's (panel/edit.py); errors show at the field.

import { LitElement, html, css, nothing } from "./vendor/lit.js";
import { clone, formatDuration, looseTimers, parseDuration, parseRoute, same, stationLive, tempId, translator } from "./helpers.js";

const PANEL = "home-timer";
const WS = "home_timer";
const DOCS = "https://github.com/financelurker/ha-home-timer/blob/main/docs/panel.md";

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
    if (changed.has("_dialog") && this._dialog) this._focusDialog();
    if (changed.has("_menu") && this._menu) this.shadowRoot?.querySelector(".menu button")?.focus();
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
          <select ?disabled=${disabled} @change=${(e) => onChange(e.target.value || null)}>
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
  _inline(key, field, value, range, unit, onChange, step = 1) {
    const bad = this._errors[key]?.[field];
    return html`<span class="inl ${bad ? "bad" : ""}"><input type="number" inputmode="decimal" aria-label=${this._t(`rule_field.${field.split(".").pop()}`)}
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
    return html`
      <div class="cols">
        <div class="col">
          <div class="card"><div class="ch">${t("station.general")}</div><div class="cb">
            <div class="pair">
              ${this._selector(key, "name", t("station.name"), { text: {} }, d.name, (v) => set((x) => (x.name = v ?? "")), t("station.name_placeholder"), true)}
              ${this._selector(key, "icon", t("station.icon"), { icon: { placeholder: icon } }, d.icon, (v) => set((x) => (x.icon = v || "")))}
            </div>
            <div class="starts"><span class="fl">${t("station.starts")}</span>
              <div class="seg" role="radiogroup" aria-label=${t("station.starts")}>
                ${["manual", "power"].map((m) => html`<button role="radio" aria-checked=${d.mode === m} class=${d.mode === m ? "on" : ""} @click=${() => set((x) => (x.mode = m))}>${d.mode === m ? html`<ha-icon icon="mdi:check"></ha-icon>` : nothing}${t(`mode.${m}`)}</button>`)}
              </div>
            </div>
            ${this._help(t(`mode.${d.mode}_hint`))}
          </div></div>
          ${d.mode === "power" ? this._renderPower(key, d, set) : nothing}
          ${showPresets
            ? html`<div class="card"><div class="ch">${t("presets")} <span class="chs">– ${t("presets.hint")}</span></div><div class="cb">
                ${this._presetTable(key, d.presets, "presets", (fn) => set((x) => fn(x.presets)))}
              </div></div>`
            : nothing}
        </div>
        ${station ? html`<div class="colr">${this._renderNow(area, station)}</div>` : nothing}
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

  _renderPower(key, d, set) {
    const t = this._t;
    const p = d.power;
    const spec = this._config.specs;
    const setP = (k, v) => set((x) => (x.power[k] = v));
    return html`
      <div class="card"><div class="ch">${t("power")}</div><div class="cb">
        ${this._selector(key, "power.sensor", t("power.sensor"), { entity: { filter: { domain: "sensor", device_class: "power" } } }, p.sensor, (v) => setP("sensor", v || ""), t("power.sensor_hint"))}
        <div class="pair">
          ${this._number(key, "power.on_above", t("power.on_above"), p.on_above, spec.power.on_above, "W", (v) => setP("on_above", v))}
          ${this._number(key, "power.off_after", t("power.off_after"), p.off_after, spec.power.off_after, "s", (v) => setP("off_after", v))}
        </div>
        ${this._help(t("power.thresholds_short"), t("power.thresholds_hint"), "thresholds")}
      </div></div>
      <div class="card"><div class="ch">${t("power.kind")}</div><div class="cb">
        <div class="radios" role="radiogroup" aria-label=${t("power.kind")}>
          ${spec.kinds.map((k) => html`<button class="radio" role="radio" aria-checked=${p.kind === k} @click=${() => setP("kind", k)}>
              <span class="rb ${p.kind === k ? "on" : ""}"></span><span class="lbl"><b>${t(`kind.${k}`)}</b><span>${t(`kind.${k}_long`)}</span></span></button>`)}
        </div>
        ${p.kind === "start_preset"
          ? html`<div class="sub">
              ${this._selector(key, "power.preset", t("power.preset"), { select: { mode: "dropdown", options: d.presets.filter((x) => x.name).map((x) => ({ value: x.id, label: x.name })) } }, p.preset, (v) => setP("preset", v || null), d.presets.some((x) => x.name) ? "" : t("power.preset_none"))}
              ${this._toggle(t("power.cancel_on_off"), t("power.cancel_on_off_hint"), p.cancel_on_off, (v) => setP("cancel_on_off", v))}
            </div>`
          : nothing}
        ${p.kind === "warn" ? html`<div class="sub pair">${this._number(key, "power.warn_after", t("power.warn_after"), p.warn_after, spec.power.warn_after, "min", (v) => setP("warn_after", v))}</div>` : nothing}
      </div></div>
      ${p.kind === "until_off" ? this._renderRule(key, d, set) : nothing}
    `;
  }

  // the done rule as a sentence with its values in it
  _renderRule(key, d, set) {
    const t = this._t;
    const spec = this._config.specs.rules;
    const rule = d.power.done?.type || "off";
    const fields = Object.fromEntries(spec[rule].fields.map((f) => [f.key, f]));
    const clearRule = () => (this._errors = { ...this._errors, [key]: Object.fromEntries(Object.entries(this._errors[key] || {}).filter(([k]) => !k.startsWith("power.done."))) });
    const typeSelect = html`<select class="inlsel" aria-label=${t("power.rule")} @change=${(e) => (clearRule(), set((x) => (x.power.done = { type: e.target.value, ...spec[e.target.value].defaults })))}>
        ${Object.keys(spec).map((r) => html`<option value=${r} ?selected=${rule === r}>${t(`rule_verb.${r}`)}</option>`)}
      </select>`;
    const parts = t(`rule_sentence.${rule}`).split(/(\{\w+\})/);
    const errors = Object.entries(this._errors[key] || {}).filter(([k]) => k.startsWith("power.done.") && fields[k.split(".").pop()]);
    return html`<div class="card"><div class="ch">${t("power.done")}</div><div class="cb">
      <div class="sentence">${parts.map((part) => {
        const m = part.match(/^\{(\w+)\}$/);
        if (!m) return part;
        if (m[1] === "type") return typeSelect;
        const f = fields[m[1]];
        return f ? this._inline(key, `power.done.${f.key}`, d.power.done[f.key], f, f.unit, (v) => set((x) => (x.power.done[f.key] = v)), f.step) : part;
      })}</div>
      ${errors.map(([k, code]) => {
        const f = fields[k.split(".").pop()];
        return html`<div class="err" role="alert">${t(`error.${code}`)} ${t("allowed", { min: f.min, max: f.max, unit: f.unit })}</div>`;
      })}
      ${this._help(t(`rule.${rule}_hint`), t("rule.all_hint"), "rules")}
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
    :host([narrow]) .cols { flex-direction: column; gap: 0; }
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
    @container (max-width: 780px) {
      .cols { flex-direction: column; gap: 0; }
      .colr { width: 100%; position: static; order: -1; }
      .col { max-width: none; }
    }
  `;
}

// a newer version loaded into an open tab must not define the element twice
if (!customElements.get("home-timer-panel")) customElements.define("home-timer-panel", HomeTimerPanel);
