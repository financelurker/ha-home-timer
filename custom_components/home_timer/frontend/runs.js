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
// extra: further seconds the time axis must reach (marks)
export function chartModel(run, { width = 600, height = 240, onAbove = 0, ignore = 0, extra = [] } = {}) {
  const t = run.t || [];
  const w = run.w || [];
  const end = Math.max(run.duration || 0, t.length ? t[t.length - 1] : 0, ...extra, 1);
  const top = Math.max(...w, onAbove, 1) * 1.08;
  const ystep = niceStep(top, 4);
  const ymax = Math.ceil(top / ystep) * ystep;
  const plotW = Math.max(10, width - PAD.left - PAD.right);
  const plotH = Math.max(10, height - PAD.top - PAD.bottom);
  const x = (s) => PAD.left + (Math.min(Math.max(s, 0), end) / end) * plotW;
  const y = (v) => PAD.top + plotH - (Math.min(Math.max(v, 0), ymax) / ymax) * plotH;
  return { t, w, end, ymax, ystep, xstep: timeStep(end, plotW), plotW, plotH, width, height, x, y, onAbove, ignore };
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
export function renderChart(run, m, { labels, hover, unit = "W", marks = [], window = null, level = null, done = null }) {
  const left = PAD.left;
  const right = m.width - PAD.right;
  const bottom = PAD.top + m.plotH;
  const yt = [];
  for (let v = 0; v <= m.ymax + 1e-9; v += m.ystep) yt.push(v);
  const xt = [];
  for (let s = 0; s <= m.end + 1e-9; s += m.xstep) xt.push(s);
  const from = run.from || 0;
  const decimals = Math.max(0, -Math.floor(Math.log10(m.ystep) + 1e-9));
  const hv = hover != null ? valueAt(run, hover) : null;
  const line = path(m);
  const crowded = marks.some((k) => Math.abs(m.x(k.t) - m.x(run.duration || 0)) < 100);
  const flip = (k) => m.x(k.t) > m.width - 130; // a label near the right edge goes to the left of its line
  return svg`
    <svg class="chart" width=${m.width} height=${m.height} viewBox="0 0 ${m.width} ${m.height}" role="img" aria-label=${labels.aria}>
      ${from > 0 ? svg`<rect class="unknown" x=${left} y=${PAD.top} width=${m.x(from) - left} height=${m.plotH}></rect>
          <text class="zone" x=${left + 4} y=${PAD.top + 12}>${labels.unknown}</text>` : nothing}
      ${m.ignore > 0 ? svg`<rect class="ignored" x=${m.x(from)} y=${PAD.top} width=${Math.max(0, m.x(Math.max(m.ignore, from)) - m.x(from))} height=${m.plotH}></rect>
          <text class="zone" x=${m.x(from) + 4} y=${bottom - 6}>${labels.ignored}</text>` : nothing}
      ${yt.map((v) => svg`<line class="grid" x1=${left} x2=${right} y1=${m.y(v)} y2=${m.y(v)}></line>
          <text class="ax" x=${left - 6} y=${m.y(v) + 4} text-anchor="end">${v.toFixed(decimals)} ${unit}</text>`)}
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
      ${hover != null && hv != null ? svg`<line class="hover" x1=${m.x(hover)} x2=${m.x(hover)} y1=${PAD.top} y2=${bottom}></line>
          <circle class="dot" cx=${m.x(hover)} cy=${m.y(hv)} r="4"></circle>` : nothing}
      <line class="axis" x1=${left} x2=${right} y1=${bottom} y2=${bottom}></line>
    </svg>`;
}

// the second under a pointer at clientX over the chart element
export function secondsAt(m, el, clientX) {
  const box = el.getBoundingClientRect();
  const px = ((clientX - box.left) / box.width) * m.width;
  const s = ((px - PAD.left) / m.plotW) * m.end;
  return s < 0 || s > m.end ? null : s;
}
