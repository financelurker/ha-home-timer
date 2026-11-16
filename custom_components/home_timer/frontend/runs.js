// Home Timer panel: the power curve of a stored run (an SVG drawn here, no chart library).
// The curve is a step line: each reading holds until the next. Marked in it: the "runs
// above" threshold, the start the rule ignores, where the run stopped, and marks – when
// the saved settings and the draft ring (the preview) – with the rule's window or level.

import { svg, nothing } from "./vendor/lit.js";
import { formatDuration } from "./helpers.js";

const PAD = { left: 52, right: 14, top: 22, bottom: 26 };
const TIME_STEPS = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400];

// a round step for about `count` ticks up to `max`
export function niceStep(max, count = 4) {
  if (!(max > 0)) return 1;
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const f = raw / mag;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag;
}

export function timeStep(span, width) {
  const most = Math.max(2, Math.floor(width / 70));
  return TIME_STEPS.find((s) => span / s <= most) ?? TIME_STEPS[TIME_STEPS.length - 1];
}

// the sizes and scales of a run's chart
// extra: further seconds the time axis must reach (marks); view: {from, to} – the seconds shown (zoomed in)
// fitY: zoomed in, the power axis fits what is in view (a few watts at the end of a 4 kW run)
export function chartModel(run, { width = 600, height = 240, onAbove = 0, ignore = 0, extra = [], view = null, pad = PAD, fitY = true, levels = [] } = {}) {
  const t = run.t || [];
  const w = run.w || [];
  const end = Math.max(run.duration || 0, t.length ? t[t.length - 1] : 0, ...extra, 1);
  const v = clampView(view, end);
  const from = v ? v.from : 0;
  const span = v ? v.to - v.from : end;
  // (levels: watt lines drawn on the chart – a rule's level – stay within the axis)
  const top = Math.max(v && fitY ? maxIn(t, w, from, from + span) : Math.max(...w, 0), onAbove, ...levels, v && fitY ? 0.1 : 1) * 1.08;
  const ystep = niceStep(top, 4);
  const ymax = Math.ceil(top / ystep - 1e-9) * ystep;
  const plotW = Math.max(10, width - pad.left - pad.right);
  const plotH = Math.max(10, height - pad.top - pad.bottom);
  // zoomed in, what lies outside the view is clipped (the chart draws within its plot)
  const x = (s) => pad.left + ((Math.min(Math.max(s, 0), end) - from) / span) * plotW;
  const y = (val) => pad.top + plotH - (Math.min(Math.max(val, 0), ymax) / ymax) * plotH;
  return { t, w, end, from, to: from + span, span, zoomed: !!v, ymax, ystep, xstep: timeStep(span, plotW), plotW, plotH, width, height, x, y, onAbove, ignore, pad };
}

// the highest reading within [a, b] – including the one in force at a (the curve is a step curve)
function maxIn(t, w, a, b) {
  let top = 0;
  let before = null;
  for (let i = 0; i < t.length; i++) {
    if (t[i] <= a) before = w[i];
    else if (t[i] <= b) top = Math.max(top, w[i]);
    else break;
  }
  return Math.max(top, before ?? 0);
}

export const MIN_VIEW = 30; // seconds: the narrowest view

// a view within the run (at least MIN_VIEW wide), or null for the whole run
export function clampView(view, end) {
  if (!view || !(end > MIN_VIEW)) return null;
  const span = Math.min(end, Math.max(MIN_VIEW, view.to - view.from));
  if (span >= end - 1e-6) return null;
  const from = Math.min(Math.max(0, view.from), end - span);
  return { from, to: from + span };
}

// the view zoomed by `factor` (< 1 in, > 1 out) around second `at`, which stays where it is
export function zoomView(view, end, factor, at) {
  const v = clampView(view, end) || { from: 0, to: end };
  const span = v.to - v.from;
  const next = Math.min(end, Math.max(MIN_VIEW, span * factor));
  const share = span > 0 ? (at - v.from) / span : 0.5;
  return clampView({ from: at - share * next, to: at - share * next + next }, end);
}

// the reading at second `s` (the step curve)
export function valueAt(run, s) {
  const t = run.t || [];
  let lo = 0;
  let hi = t.length - 1;
  if (hi < 0 || s < t[0]) return null;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (t[mid] <= s) lo = mid;
    else hi = mid - 1;
  }
  return run.w[lo];
}

function path(m) {
  if (!m.t.length) return "";
  let d = `M${m.x(m.t[0]).toFixed(1)},${m.y(m.w[0]).toFixed(1)}`;
  for (let i = 1; i < m.t.length; i++) d += `H${m.x(m.t[i]).toFixed(1)}V${m.y(m.w[i]).toFixed(1)}`;
  return `${d}H${m.x(m.end).toFixed(1)}`;
}

// labels: {aria, stopped, unknown, ignored}; hover: seconds or null; unit: "W"
// marks: [{t, label, dash, color}] (the first label above the plot, the next inside);
// window: {from, to, label} shaded; level: {w, label} a dotted line
// done: {t, label, tol, tolLabel} – the user's mark (learning) with the "on time" band after it
// pins: [{t, n}] – where each step of a sequence was met (numbered); the last one is the ring
export function renderChart(run, m, { labels, hover, unit = "W", marks = [], window = null, level = null, done = null, pins = [] }) {
  const left = m.pad.left;
  const right = m.width - m.pad.right;
  const top = m.pad.top;
  const bottom = top + m.plotH;
  const yt = [];
  for (let v = 0; v <= m.ymax + 1e-9; v += m.ystep) yt.push(v);
  const xt = [];
  for (let s = Math.ceil(m.from / m.xstep - 1e-9) * m.xstep; s <= m.to + 1e-9; s += m.xstep) xt.push(s);
  const from = run.from || 0;
  const decimals = Math.max(0, -Math.floor(Math.log10(m.ystep) + 1e-9));
  const hv = hover != null ? valueAt(run, hover) : null;
  const line = path(m);
  const seen = (s) => s >= m.from - 1e-6 && s <= m.to + 1e-6; // within the view (all of it, when not zoomed)
  const crowded = marks.some((k) => Math.abs(m.x(k.t) - m.x(run.duration || 0)) < 100);
  const flip = (k) => m.x(k.t) > m.width - 130; // a label near the right edge goes to the left of its line
  const zoneX = (s) => Math.max(m.x(s), left) + 4; // a zone's label stays in view
  // a ring's label where your mark's label is (both end there, e.g. done at switch-off) goes to the second row
  const row2Free = !marks.some((k) => k.dash) && !crowded; // (the draft's label and "stopped" use the second row)
  const nearDone = (k) => row2Free && done && seen(done.t) && (flip(k) || m.x(k.t) - m.x(done.t) < 8) && Math.abs(m.x(k.t) - m.x(done.t)) < 170;
  // zoomed in, the data is clipped to the plot; labels are drawn only for what is in view (never cut)
  const data = svg`
      ${from > 0 && m.from < from ? svg`<rect class="unknown" x=${left} y=${top} width=${Math.max(0, m.x(from) - left)} height=${m.plotH}></rect>` : nothing}
      ${m.ignore > 0 && m.from < Math.max(m.ignore, from) ? svg`<rect class="ignored" x=${Math.max(left, m.x(from))} y=${top} width=${Math.max(0, m.x(Math.max(m.ignore, from)) - Math.max(left, m.x(from)))} height=${m.plotH}></rect>` : nothing}
      ${m.onAbove > 0 ? svg`<line class="thr" x1=${left} x2=${right} y1=${m.y(m.onAbove)} y2=${m.y(m.onAbove)}></line>` : nothing}
      ${line ? svg`<path class="area" d="${line}V${bottom}H${m.x(m.t[0])}Z"></path><path class="curve" d=${line}></path>` : nothing}
      ${run.duration && !run.running && run.duration < m.end - 0.5 ? svg`<line class="stop" x1=${m.x(run.duration)} x2=${m.x(run.duration)} y1=${top} y2=${bottom}></line>` : nothing}
      ${window ? svg`<rect class="win" x=${m.x(window.from)} y=${top} width=${Math.max(1, m.x(window.to) - m.x(window.from))} height=${m.plotH}></rect>` : nothing}
      ${level ? svg`<line class="level" x1=${left} x2=${right} y1=${m.y(level.w)} y2=${m.y(level.w)}></line>` : nothing}
      ${marks.map((k) => svg`<line class="mark ${k.dash ? "dash" : ""}" style=${k.color ? `stroke:${k.color}` : ""} x1=${m.x(k.t)} x2=${m.x(k.t)} y1=${top} y2=${bottom}></line>`)}
      ${done?.tol ? svg`<rect class="tol" x=${m.x(done.t)} y=${top} width=${Math.max(1, m.x(done.t + done.tol) - m.x(done.t))} height=${m.plotH}></rect>` : nothing}
      ${done ? svg`<line class="done" x1=${m.x(done.t)} x2=${m.x(done.t)} y1=${top} y2=${bottom}></line>` : nothing}`;
  return svg`
    <svg class="chart" width=${m.width} height=${m.height} viewBox="0 0 ${m.width} ${m.height}" role="img" aria-label=${labels.aria}>
      ${m.zoomed ? svg`<defs><clipPath id="htplot"><rect x=${left} y="0" width=${right - left} height=${m.height}></rect></clipPath></defs>` : nothing}
      ${yt.map((v) => svg`<line class="grid" x1=${left} x2=${right} y1=${m.y(v)} y2=${m.y(v)}></line>
          <text class="ax y" x=${left - 6} y=${m.y(v) + 4} text-anchor="end">${v.toFixed(decimals)} ${unit}</text>`)}
      ${m.zoomed ? svg`<g clip-path="url(#htplot)">${data}</g>` : data}
      ${from > 0 && m.from < from ? svg`<text class="zone" x=${left + 4} y=${top + 12}>${labels.unknown}</text>` : nothing}
      ${m.ignore > 0 && m.from < Math.max(m.ignore, from) ? svg`<text class="zone" x=${zoneX(from)} y=${bottom - 6}>${labels.ignored}</text>` : nothing}
      ${xt.map((s) => svg`<text class="ax" x=${m.x(s)} y=${bottom + 18} text-anchor=${m.zoomed && m.x(s) - left < 20 ? "start" : m.zoomed && right - m.x(s) < 20 ? "end" : "middle"}>${formatDuration(s)}</text>`)}
      ${run.duration && !run.running && run.duration < m.end - 0.5 && seen(run.duration) ? svg`<text class="mk" x=${m.x(run.duration) - 4} y=${crowded ? top + 12 : top - 6} text-anchor="end">${labels.stopped}</text>` : nothing}
      ${window && seen((window.from + window.to) / 2) ? svg`<text class="zone win" x=${(m.x(window.from) + m.x(window.to)) / 2} y=${bottom - 22} text-anchor="middle">${window.label}</text>` : nothing}
      ${level ? svg`<text class="zone lvl" x=${right - 4} y=${m.y(level.w) - 4} text-anchor="end">${level.label}</text>` : nothing}
      ${marks.map((k, i) => (seen(k.t) ? svg`<text class="mk ring ${k.dash ? "it" : ""}" style=${k.color ? `fill:${k.color}` : ""} x=${flip(k) ? m.x(k.t) - 4 : m.x(k.t) + 4} y=${i || nearDone(k) ? top + 12 : top - 6} text-anchor=${flip(k) ? "end" : "start"}>${k.label}</text>` : nothing))}
      ${done?.tol && seen(done.t + done.tol / 2) ? svg`<text class="zone tolt" x=${(m.x(done.t) + m.x(done.t + done.tol)) / 2} y=${bottom - 6} text-anchor="middle">${done.tolLabel || ""}</text>` : nothing}
      ${done && seen(done.t) ? svg`<circle class="grip" cx=${m.x(done.t)} cy=${top + m.plotH / 2} r="9"></circle>
          <path class="gripl" d="M${m.x(done.t) - 3},${top + m.plotH / 2 - 4}v8M${m.x(done.t) + 3},${top + m.plotH / 2 - 4}v8"></path>
          <text class="mk donet" x=${m.x(done.t) - 6} y=${top - 6} text-anchor="end">${done.label}</text>` : nothing}
      ${pins.filter((k) => seen(k.t)).map((k) => {
        const x = m.x(k.t);
        const y = top + m.plotH * 0.72;
        return svg`<g class="pin ${k.last ? "last" : ""}" data-step=${k.n}><line class="pinl" x1=${x} x2=${x} y1=${y + 9} y2=${bottom}></line>
          <circle class="pinc" cx=${x} cy=${y} r="9"></circle><text class="pint" x=${x} y=${y + 4} text-anchor="middle">${k.n}</text>
          <title>${k.label || ""}</title></g>`;
      })}
      ${hover != null && hv != null && seen(hover) ? svg`<line class="hover" x1=${m.x(hover)} x2=${m.x(hover)} y1=${top} y2=${bottom}></line>
          <circle class="dot" cx=${m.x(hover)} cy=${m.y(hv)} r="4"></circle>` : nothing}
      <line class="axis" x1=${left} x2=${right} y1=${bottom} y2=${bottom}></line>
    </svg>`;
}

// the second under a pointer at clientX over the chart element
export function secondsAt(m, el, clientX, clamp = false) {
  const box = el.getBoundingClientRect();
  const px = ((clientX - box.left) / box.width) * m.width;
  const s = m.from + ((px - m.pad.left) / m.plotW) * m.span;
  if (clamp) return Math.min(Math.max(s, m.from), m.to);
  return s < m.from || s > m.to ? null : s;
}

const OVERVIEW_PAD = { left: PAD.left, right: PAD.right, top: 3, bottom: 3 };

// zoomed in: the whole run, small, with the part shown – drag it to move along the run
export function overviewModel(run, width, extra = []) {
  return chartModel(run, { width, height: 34, extra, pad: OVERVIEW_PAD });
}

export function renderOverview(o, view, label) {
  const line = path(o);
  const a = o.x(view.from);
  const b = o.x(view.to);
  return svg`<svg class="overview" width=${o.width} height=${o.height} viewBox="0 0 ${o.width} ${o.height}" aria-hidden="true">
      <rect class="ovbg" x=${o.pad.left} y=${o.pad.top} width=${o.plotW} height=${o.plotH}></rect>
      ${line ? svg`<path class="ovcurve" d=${line}></path>` : nothing}
      <rect class="ovwin" x=${a} y="1" width=${Math.max(4, b - a)} height=${o.height - 2} rx="3"><title>${label}</title></rect>
      ${[a, b].map((x) => svg`<rect class="ovgrip" x=${x - 3} y=${o.height / 2 - 9} width="6" height="18" rx="2"></rect>`)}
    </svg>`;
}
