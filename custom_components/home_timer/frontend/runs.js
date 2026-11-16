// Home Timer panel: the power curve of a stored run (an SVG drawn here, no chart library).
// The curve is a step line: each reading holds until the next. Marked in it: the "runs
// above" threshold, the start the rule ignores, where the run stopped, and marks – when
// the saved settings and the draft ring (the preview) – with the rule's window or level.

import { svg, nothing } from "./vendor/lit.js";
import { formatDuration } from "./helpers.js";

const PAD = { left: 52, right: 14, top: 22, bottom: 26 };
let CLIPS = 0; // ids of the charts' clip paths
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
export function chartModel(run, { width = 600, height = 240, onAbove = 0, ignore = 0, extra = [], view = null, pad = PAD } = {}) {
  const t = run.t || [];
  const w = run.w || [];
  const end = Math.max(run.duration || 0, t.length ? t[t.length - 1] : 0, ...extra, 1);
  const top = Math.max(...w, onAbove, 1) * 1.08;
  const ystep = niceStep(top, 4);
  const ymax = Math.ceil(top / ystep) * ystep;
  const plotW = Math.max(10, width - pad.left - pad.right);
  const plotH = Math.max(10, height - pad.top - pad.bottom);
  const v = clampView(view, end);
  const from = v ? v.from : 0;
  const span = v ? v.to - v.from : end;
  // zoomed in, what lies outside the view is clipped (the chart draws within its plot)
  const x = (s) => pad.left + ((Math.min(Math.max(s, 0), end) - from) / span) * plotW;
  const y = (val) => pad.top + plotH - (Math.min(Math.max(val, 0), ymax) / ymax) * plotH;
  return { t, w, end, from, to: from + span, span, zoomed: !!v, ymax, ystep, xstep: timeStep(span, plotW), plotW, plotH, width, height, x, y, onAbove, ignore, pad };
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
  const left = PAD.left;
  const right = m.width - PAD.right;
  const bottom = PAD.top + m.plotH;
  const yt = [];
  for (let v = 0; v <= m.ymax + 1e-9; v += m.ystep) yt.push(v);
  const xt = [];
  for (let s = Math.ceil(m.from / m.xstep - 1e-9) * m.xstep; s <= m.to + 1e-9; s += m.xstep) xt.push(s);
  const clip = `htclip${++CLIPS}`;
  const from = run.from || 0;
  const decimals = Math.max(0, -Math.floor(Math.log10(m.ystep) + 1e-9));
  const hv = hover != null ? valueAt(run, hover) : null;
  const line = path(m);
  const crowded = marks.some((k) => Math.abs(m.x(k.t) - m.x(run.duration || 0)) < 100);
  const flip = (k) => m.x(k.t) > m.width - 130; // a label near the right edge goes to the left of its line
  return svg`
    <svg class="chart" width=${m.width} height=${m.height} viewBox="0 0 ${m.width} ${m.height}" role="img" aria-label=${labels.aria}>
      <g clip-path="url(#${clip})">
      ${from > 0 ? svg`<rect class="unknown" x=${left} y=${PAD.top} width=${m.x(from) - left} height=${m.plotH}></rect>
          <text class="zone" x=${left + 4} y=${PAD.top + 12}>${labels.unknown}</text>` : nothing}
      ${m.ignore > 0 ? svg`<rect class="ignored" x=${m.x(from)} y=${PAD.top} width=${Math.max(0, m.x(Math.max(m.ignore, from)) - m.x(from))} height=${m.plotH}></rect>
          <text class="zone" x=${m.x(from) + 4} y=${bottom - 6}>${labels.ignored}</text>` : nothing}
      </g>
      <defs><clipPath id=${clip}><rect x=${left} y="0" width=${right - left} height=${m.height}></rect></clipPath></defs>
      ${yt.map((v) => svg`<line class="grid" x1=${left} x2=${right} y1=${m.y(v)} y2=${m.y(v)}></line>
          <text class="ax" x=${left - 6} y=${m.y(v) + 4} text-anchor="end">${v.toFixed(decimals)} ${unit}</text>`)}
      <g clip-path="url(#${clip})">
      ${xt.map((s) => svg`<text class="ax" x=${m.x(s)} y=${bottom + 18} text-anchor="middle">${formatDuration(s)}</text>`)}
      ${m.onAbove > 0 ? svg`<line class="thr" x1=${left} x2=${right} y1=${m.y(m.onAbove)} y2=${m.y(m.onAbove)}></line>` : nothing}
      ${line ? svg`<path class="area" d="${line}V${bottom}H${m.x(m.t[0])}Z"></path><path class="curve" d=${line}></path>` : nothing}
      ${run.duration && !run.running && run.duration < m.end - 0.5 ? svg`<line class="stop" x1=${m.x(run.duration)} x2=${m.x(run.duration)} y1=${PAD.top} y2=${bottom}></line>
          <text class="mk" x=${m.x(run.duration) - 4} y=${crowded ? PAD.top + 12 : PAD.top - 6} text-anchor="end">${labels.stopped}</text>` : nothing}
      ${window ? svg`<rect class="win" x=${m.x(window.from)} y=${PAD.top} width=${Math.max(1, m.x(window.to) - m.x(window.from))} height=${m.plotH}></rect>
          <text class="zone win" x=${(m.x(window.from) + m.x(window.to)) / 2} y=${bottom - 22} text-anchor="middle">${window.label}</text>` : nothing}
      ${level ? svg`<line class="level" x1=${left} x2=${right} y1=${m.y(level.w)} y2=${m.y(level.w)}></line>
          <text class="zone lvl" x=${right - 4} y=${m.y(level.w) - 4} text-anchor="end">${level.label}</text>` : nothing}
      ${marks.map((k, i) => svg`<line class="mark ${k.dash ? "dash" : ""}" style=${k.color ? `stroke:${k.color}` : ""} x1=${m.x(k.t)} x2=${m.x(k.t)} y1=${PAD.top} y2=${bottom}></line>
          <text class="mk ring ${k.dash ? "it" : ""}" style=${k.color ? `fill:${k.color}` : ""} x=${flip(k) ? m.x(k.t) - 4 : m.x(k.t) + 4} y=${i ? PAD.top + 12 : PAD.top - 6} text-anchor=${flip(k) ? "end" : "start"}>${k.label}</text>`)}
      ${done ? svg`${done.tol ? svg`<rect class="tol" x=${m.x(done.t)} y=${PAD.top} width=${Math.max(1, m.x(done.t + done.tol) - m.x(done.t))} height=${m.plotH}></rect>
            <text class="zone tolt" x=${(m.x(done.t) + m.x(done.t + done.tol)) / 2} y=${bottom - 6} text-anchor="middle">${done.tolLabel || ""}</text>` : nothing}
          <line class="done" x1=${m.x(done.t)} x2=${m.x(done.t)} y1=${PAD.top} y2=${bottom}></line>
          <circle class="grip" cx=${m.x(done.t)} cy=${PAD.top + m.plotH / 2} r="9"></circle>
          <path class="gripl" d="M${m.x(done.t) - 3},${PAD.top + m.plotH / 2 - 4}v8M${m.x(done.t) + 3},${PAD.top + m.plotH / 2 - 4}v8"></path>
          <text class="mk donet" x=${m.x(done.t) - 6} y=${PAD.top - 6} text-anchor="end">${done.label}</text>` : nothing}
      ${pins.map((k) => {
        const x = m.x(k.t);
        const y = PAD.top + m.plotH * 0.72;
        return svg`<g class="pin ${k.last ? "last" : ""}" data-step=${k.n}><line class="pinl" x1=${x} x2=${x} y1=${y + 9} y2=${bottom}></line>
          <circle class="pinc" cx=${x} cy=${y} r="9"></circle><text class="pint" x=${x} y=${y + 4} text-anchor="middle">${k.n}</text>
          <title>${k.label || ""}</title></g>`;
      })}
      ${hover != null && hv != null ? svg`<line class="hover" x1=${m.x(hover)} x2=${m.x(hover)} y1=${PAD.top} y2=${bottom}></line>
          <circle class="dot" cx=${m.x(hover)} cy=${m.y(hv)} r="4"></circle>` : nothing}
      </g>
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
    </svg>`;
}
