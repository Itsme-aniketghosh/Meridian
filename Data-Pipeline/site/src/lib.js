/* Shared pieces: the API, formatting, icons, and the renderers for views.py's sections (table, texts, diffs). */
import React, { useState, useEffect, useRef } from "react";
import htm from "htm";

export const html = htm.bind(React.createElement);

/* ---------- the API: GET-only, cached per path; a failure is retried on the next visit ---------- */
const cache = new Map();
export function get(path) {
  if (!cache.has(path)) {
    cache.set(path, fetch(path).then(async (r) => {
      if (!r.ok) throw new Error(r.status === 404 ? "Not found on this block" : `The views service answered ${r.status}`);
      return (r.headers.get("content-type") || "").includes("json") ? r.json() : r.text();
    }).catch((e) => { cache.delete(path); throw e; }));
  }
  return cache.get(path);
}
export function useApi(path) {
  const [s, setS] = useState({ loading: true });
  useEffect(() => {
    if (!path) return;
    let live = true;
    setS({ loading: true });
    get(path).then((data) => live && setS({ data }), (e) => live && setS({ error: e.message }));
    return () => { live = false; };
  }, [path]);
  return s;
}
export const qs = (o) => { const s = new URLSearchParams(Object.entries(o).filter(([, v]) => v != null && v !== "")).toString(); return s ? "?" + s : ""; };
export const sec = (v, title) => v && v.sections.find((s) => s.title === title);
/* a section's rows as objects keyed by its columns */
export const recs = (s) => (s ? s.rows.map((r) => Object.fromEntries(s.columns.map((c, i) => [c, r[i]]))) : []);
export const RISK = import.meta.env.VITE_RISK_URL || "/risk";

/* ---------- formatting ---------- */
export const n = (x) => (x == null ? "–" : Number(x).toLocaleString("en"));
export const pctOf = (x) => Math.round(x * 100) + "%";
export const fmt = (iso, year = true) => (iso ? new Date(iso.length === 10 ? iso + "T00:00:00Z" : iso)
  .toLocaleDateString("en-GB", { day: "numeric", month: "short", ...(year ? { year: "numeric" } : {}), timeZone: "UTC" }) : "");
export const daysSince = (iso) => Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 864e5));
export const short = (t) => (t && t.includes("/") ? t.replace(/\/$/, "").split("/").pop() : t);
export const isNum = (v) => typeof v === "number";
export const isPath = (v) => typeof v === "string" && /[/\\]|\.py(:\d+)?$/.test(v) && !/\s/.test(v);

/* ---------- icons and marks ---------- */
export const Icon = ({ d, size = 16 }) => html`<svg width=${size} height=${size} viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d.map((p, i) => html`<path key=${i} d=${p} />`)}</svg>`;
export const IC = {
  home: ["M2.5 7.5L8 3l5.5 4.5", "M4 6.5V13h8V6.5"], portfolio: ["M2.5 3.5h11", "M2.5 8h11", "M2.5 12.5h7"], migration: ["M3 13V8", "M8 13V3", "M13 13v-6"],
  team: ["M8 7.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z", "M3 13.5c.6-2.3 2.6-3.5 5-3.5s4.4 1.2 5 3.5"], push: ["M8 13.5v-11", "M4 6.5l4-4 4 4"],
  tickets: ["M3 3.5h10v9H3z", "M5.5 6.5h5", "M5.5 9.5h3"], search: ["M7 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z", "M14 14l-3.5-3.5"], arrow: ["M3 8h10", "M9 4l4 4-4 4"],
  copy: ["M5.5 5.5h8v8h-8z", "M3 10.5V2.5h8"], check: ["M3.5 8.5l3 3 6-7"], chev: ["M6 4l4 4-4 4"], down: ["M8 3v10", "M4 9l4 4 4-4"],
  ext: ["M9 3h4v4", "M13 3L7.5 8.5", "M11 9.5V13H3V5h3.5"],
};
export const Logo = ({ h = 18 }) => html`<img class="logo" src="/logo.png" alt="Meridian" height=${h} width=${Math.round(h * 687 / 96)} />`;

/* views.py's team status words, as a dot and a word (status is never colour alone) */
const STATUS = { STALLED: ["held", "Stalled"], blocked: ["wait", "Blocked"], moving: ["ok", "Moving"], done: ["done", "Done"],
  flag: ["held", "Flagged"], review: ["wait", "Review"], clear: ["ok", "Clear"] };
export function Status({ s, since }) {
  const [cls, word] = STATUS[s] || ["", s];
  return html`<span class="status"><span class=${"dot " + cls} aria-hidden="true"></span><span class="word">${word}</span>${since ? html`<span class="since">${since}</span>` : null}</span>`;
}

/* ---------- copy ---------- */
export function useCopy() {
  const [copied, setCopied] = useState(null);
  const copy = (s, k) => { const ok = () => { setCopied(k); setTimeout(() => setCopied(null), 1400); }; try { navigator.clipboard.writeText(s).then(ok, ok); } catch (e) { ok(); } };
  return [copied, copy];
}
export const CopyBtn = ({ text, id, label = "Copy", copied, copy }) => html`<button class="btn ghost" onClick=${() => copy(text, id)}><${Icon} d=${copied === id ? IC.check : IC.copy} size=${14} />${copied === id ? "Copied" : label}</button>`;

/* ---------- layout helpers ---------- */
export function useWidth(min = 280, start = 760) {   // draw at the real width, so chart text renders at its true size
  const ref = useRef(null);
  const [w, setW] = useState(start);
  useEffect(() => {
    const el = ref.current; if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(min, Math.round(e.contentRect.width))));
    ro.observe(el); return () => ro.disconnect();
  }, []);
  return [ref, w];
}
export function useListKeys(count, setIndex, onOpen, index) {
  useEffect(() => {
    const f = (e) => {
      if (e.target.closest && e.target.closest("input, textarea, select, [contenteditable], dialog")) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); setIndex((i) => Math.min(count - 1, i + 1)); }
      else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); setIndex((i) => Math.max(0, i - 1)); }
      else if (e.key === "Enter" && onOpen && !(e.target.closest && e.target.closest("button, a"))) onOpen(index);
    };
    addEventListener("keydown", f); return () => removeEventListener("keydown", f);
  }, [count, onOpen, index]);
}

/* ---------- loading, errors, and what every screen says about its data ---------- */
export const Loading = ({ what = "Reading the block" }) => html`<div class="state" role="status"><span class="spin" aria-hidden="true"></span>${what}…</div>`;
export const Failed = ({ error }) => html`<div class="state err" role="alert"><b>Couldn't load this view.</b><span>${error}. The views service may be starting; reload in a few seconds.</span></div>`;

/* rule 2 and 3: run, snapshot, coverage and real-or-fixture, on every screen */
const COV = { call_sites_owned: "call sites with an owning team", call_sites_resolved: "call sites resolved", files_owned: "files with an owning team", tickets_mapped: "tickets mapped to code" };
export function DataLine({ meta }) {
  if (!meta) return null;
  const fx = meta.data === "fixture";
  const cov = meta.coverage.filter((c) => c.denominator);
  const key = cov.find((c) => c.metric === "files_owned") || cov[0];
  return html`<div class=${"dataline" + (fx ? " fx" : "")}>
    ${fx ? html`<span class="fx-tag">Example data</span><span>${meta.note.split(";").pop().trim() || "shown from the fixture"}: the fixture <code>acme/shop</code>, a made-up repo.</span>`
      : html`<span class="real-tag">Real block</span>`}
    <span class="muted">run <code>${meta.run_id}</code> · snapshot ${fmt(meta.snapshot_at)}${fx ? "" : `, ${n(daysSince(meta.snapshot_at))} days ago`}</span>
    ${key ? html`<details class="cov"><summary>coverage: ${pctOf(key.numerator / key.denominator)} ${COV[key.metric] || key.metric.replace(/_/g, " ")}</summary>
      <dl>${cov.map((c) => html`<${React.Fragment} key=${c.metric}><dt>${COV[c.metric] || c.metric.replace(/_/g, " ")}</dt><dd>${n(c.numerator)} of ${n(c.denominator)}${c.note ? html`<small>${c.note}</small>` : null}</dd></${React.Fragment}>`)}</dl></details>` : null}
  </div>`;
}

/* ---------- views.py's sections ---------- */
export function Table({ s, max = 12, cell }) {
  const [all, setAll] = useState(false);
  if (!s.rows.length) return html`<p class="empty-note">${s.note && /no |none|only/.test(s.note) ? s.note : "No rows on this block."}</p>`;
  const rows = all ? s.rows : s.rows.slice(0, max);
  const num = s.columns.map((_, i) => s.rows.every((r) => r[i] == null || isNum(r[i])));
  return html`<div class="tbl-wrap"><table class="tbl">
    <thead><tr>${s.columns.map((c, i) => html`<th key=${i} class=${num[i] ? "r" : ""} scope="col">${c}</th>`)}</tr></thead>
    <tbody>${rows.map((r, k) => html`<tr key=${k}>${r.map((v, i) => html`<td key=${i} class=${num[i] ? "r" : isPath(v) ? "mono" : ""}>${cell ? cell(s.columns[i], v, r) : v == null ? "–" : isNum(v) ? n(v) : v}</td>`)}</tr>`)}</tbody>
  </table>${s.rows.length > max ? html`<button class="btn ghost more" onClick=${() => setAll(!all)}>${all ? "Show fewer" : `Show all ${n(s.rows.length)}`}</button>` : null}</div>`;
}
const SOURCE = { template: "Template", llm: "LLM · passed the gate", cache: "LLM · passed the gate", block: "Stored" };
export function Texts({ s }) {
  const [copied, copy] = useCopy();
  if (!s.texts.length) return html`<p class="empty-note">Nothing to say on this block.</p>`;
  return html`<div class="texts">${s.texts.map((t, k) => html`<div class="note" key=${k}>
    <div class="src">${SOURCE[t.source] || t.source}${t.model ? " · " + t.model : ""}${t.team ? " · " + t.team : ""}</div>
    <div class="body">${t.body}</div>
    <div class="acts"><${CopyBtn} text=${t.body} id=${"t" + k} label=${t.kind === "draft" ? "Copy draft" : t.kind === "rewrite" ? "Copy rewrite" : "Copy"} copied=${copied} copy=${copy} /></div></div>`)}</div>`;
}
export function Diffs({ s }) {
  const [copied, copy] = useCopy();
  if (!s.diffs.length) return html`<p class="empty-note">No swaps on this block.</p>`;
  return html`<div class="texts">${s.diffs.map((d, k) => html`<div class="site-x" key=${k}>
    <div class="site-h"><span class="sub"><code>${d.where}</code>${d.team ? " · " + d.team : ""} · ${d.source}</span><${CopyBtn} text=${d.body} id=${"d" + k} label="Copy diff" copied=${copied} copy=${copy} /></div>
    <pre class="diff">${d.body.split("\n").map((l, i) => html`<span key=${i} class=${l.startsWith("+") ? "p" : l.startsWith("-") ? "m" : ""}>${l}${"\n"}</span>`)}</pre></div>`)}</div>`;
}
export function Section({ s, max, cell, children }) {
  return html`<section class="section"><div class="section-h"><h2>${s.title}</h2>${s.headline ? html`<b class="headline">${s.headline}</b>` : null}${s.note ? html`<small>${s.note}</small>` : null}</div>
    ${children || (s.texts ? html`<${Texts} s=${s} />` : s.diffs ? html`<${Diffs} s=${s} />` : html`<${Table} s=${s} max=${max} cell=${cell} />`)}</section>`;
}
