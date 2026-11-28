/* Home, for everyone: "a new engineer's first week". Every number is a row of /api/home (views.py). */
import React, { useState, useMemo, useRef } from "react";
import { html, useApi, qs, sec, recs, n, pctOf, fmt, short, Icon, IC, useWidth, useCopy, CopyBtn, Loading, Failed, DataLine, get } from "./lib.js";

/* ---------- the block's Home sections, reshaped for the charts ---------- */
function shape(v) {
  const head = recs(sec(v, "Headline"))[0] || {};
  const start = recs(sec(v, "Start here")), calls = recs(sec(v, "Retiring APIs by area"));
  const areas = recs(sec(v, "Areas")).map((a) => ({
    area: a.area, files: a.files, touched: a["changed in the last year"], changes_90: a["changes in the last 90 days"], team: a["owning team"] || "",
    start: start.filter((s) => s.area === a.area).map((s) => [s.path, s["changes in the last 90 days"]]),
    calls: Object.fromEntries(calls.filter((c) => c.area === a.area).map((c) => [c.symbol, c["call sites"]])),
  }));
  const hotS = sec(v, "Where bugs came from"), graphS = sec(v, "Who calls out");
  const hot = recs(hotS).map((h) => [h.path, h["bug-introducing commits"], h["changes in the last year"], h["fix keys"] || ""]);
  const links = graphS ? graphS.rows.map((r) => [r[0], r[1], r[2]]) : [];
  const kb = recs(sec(v, "Knowledge base"));
  return { repo: v.scope.repo, files: head.files, touched: head["changed in the last year"], clock: head["repo clock"], areas, hot, hotNote: hotS && hotS.note,
    links, graphNote: graphS && graphS.note, kb, kbNote: (sec(v, "Knowledge base") || {}).note };
}
const heat = (a) => (a.files ? a.touched / a.files : 0);

/* ---------- where the work is: a squarified treemap, sized by files, shaded by the share changed this year ---------- */
function squarify(items, x, y, w, h) {
  const out = [], total = items.reduce((a, d) => a + d.v, 0) || 1, scale = w * h / total;
  let rest = items.map((d) => ({ ...d, a: d.v * scale }));
  while (rest.length) {
    const vert = w >= h, side = vert ? h : w;
    let row = [], best = Infinity;
    for (const d of rest) {
      const t = row.concat(d), s = t.reduce((a, e) => a + e.a, 0), mx = Math.max(...t.map((e) => e.a)), mn = Math.min(...t.map((e) => e.a));
      const worst = Math.max(side * side * mx / (s * s), s * s / (side * side * mn));
      if (worst > best) break;
      best = worst; row = t;
    }
    const s = row.reduce((a, e) => a + e.a, 0), thick = s / side;
    let off = 0;
    for (const d of row) { const len = d.a / thick; out.push(vert ? { ...d, x, y: y + off, w: thick, h: len } : { ...d, x: x + off, y, w: len, h: thick }); off += len; }
    if (vert) { x += thick; w -= thick; } else { y += thick; h -= thick; }
    rest = rest.slice(row.length);
  }
  return out;
}
function Heatmap({ repo, sel, setSel }) {
  const [box, W] = useWidth(280, 900);
  const H = Math.round(W < 600 ? W * 1.05 : W * 0.56);
  const rects = useMemo(() => squarify(repo.areas.map((a, i) => ({ v: a.files, i })).filter((d) => d.v > 0).sort((p, q) => q.v - p.v), 0, 0, W, H), [repo, W, H]);
  const [hov, setHov] = useState(null);
  const hr = hov != null ? rects.find((x) => x.i === hov) : null;
  return html`<div class="tmap" ref=${box} style=${{ height: H + "px" }} onPointerLeave=${() => setHov(null)}>
    ${rects.map((r) => { const a = repo.areas[r.i], p = heat(a), hot = p > 0.55;
      const big = r.w > 116 && r.h > 100, mid = r.w > 66 && r.h > 40;
      return html`<button key=${a.area} class=${"cell" + (sel === r.i ? " on" : "") + (hot ? " hot" : "")}
        style=${{ left: r.x / W * 100 + "%", top: r.y / H * 100 + "%", width: r.w / W * 100 + "%", height: r.h / H * 100 + "%", "--p": Math.round(Math.pow(p, 0.8) * 100) + "%" }}
        onClick=${() => setSel(r.i)} onPointerEnter=${() => setHov(r.i)} onFocus=${() => setHov(r.i)}
        aria-pressed=${sel === r.i} aria-label=${`${a.area}: ${n(a.files)} files, ${pctOf(p)} changed in the year, ${n(a.changes_90)} changes in 90 days`}>
        ${mid ? html`<span class="cell-name">${a.area}</span>` : null}
        ${big ? html`<span class="cell-p">${pctOf(p)}</span><span class="cell-sub">${n(a.files)} files · ${a.team || "unowned"}</span>` : mid ? html`<span class="cell-sub">${pctOf(p)}</span>` : null}
      </button>`; })}
    ${hr ? html`<div class="tip" style=${{ left: (hr.x + hr.w / 2) / W * 100 + "%", top: Math.max(hr.y / H * 100, 8) + "%" }}>
      <b>${repo.areas[hov].area}</b><span>${n(repo.areas[hov].touched)} of ${n(repo.areas[hov].files)} files changed in the year · ${n(repo.areas[hov].changes_90)} changes in 90 days</span></div>` : null}
  </div>
  <div class="heat-legend"><span>Quiet</span><i aria-hidden="true"></i><span>Busy</span><small>Colour: share of an area's files changed in the year before ${fmt(repo.clock)}. Size: number of files.</small></div>`;
}
function StartHere({ a, go, repo }) {
  const p = heat(a), calls = Object.entries(a.calls || {});
  return html`<div class="start" key=${a.area}>
    <div class="pk-title"><span class="sub">${a.team ? "Owned by " + a.team : "Unowned, needs review"}</span><h2><code>${a.area}</code></h2></div>
    <div class="figures"><div class="fig"><b>${pctOf(p)}</b><span>changed in the year</span></div><div class="fig"><b>${n(a.changes_90)}</b><span>changes, 90 days</span></div><div class="fig"><b>${n(a.files)}</b><span>files</span></div></div>
    ${a.start.length ? html`<div class="section"><div class="section-h"><h2>Start here</h2><small>Most-changed files, last 90 days</small></div>
      <div>${a.start.map(([path, c]) => html`<div class="mini" key=${path}><span class="mono" title=${path}>${path}</span><span>${n(c)} changes</span><span></span></div>`)}</div></div>`
      : html`<p class="note">Nothing here changed in the last 90 days.${p < 0.15 ? " A newcomer can leave this area for later." : ""}</p>`}
    ${calls.length ? html`<div class="note"><div class="src">Retiring APIs still called here</div>${calls.map(([s, c]) => html`<div key=${s} class="call-row"><code>${s}</code><span>${n(c)} call sites</span>
        <button class="btn ghost sm" onClick=${() => go("owner", { symbol: s, repo })}>Migration <${Icon} d=${IC.arrow} size=${12} /></button></div>`)}</div>` : null}
  </div>`;
}

/* ---------- who calls out: callers -> (intermediate) -> retiring APIs, from call_edge ---------- */
const label = (k) => { const [p, s] = k.includes("::") ? k.split("::") : [null, k]; return { scope: s === "<module>" ? "module level" : s, file: p ? p.split("/").slice(-2).join("/") : null }; };
function CallGraph({ links: all }) {
  const srcs = new Set(all.map((l) => l[0])), dsts = new Set(all.map((l) => l[1]));
  const inter = [...dsts].filter((x) => srcs.has(x));
  const apis = [...dsts].filter((x) => !srcs.has(x));
  const apiSet = new Set(apis), keep = new Set([...inter, ...apis]);
  const links = all.filter((l) => keep.has(l[1]));
  const callers = [...new Set(links.map((l) => l[0]))].filter((x) => !dsts.has(x));
  const total = (k) => links.filter((l) => l[0] === k || l[1] === k).reduce((a, l) => a + l[2], 0);
  callers.sort((a, b) => total(b) - total(a));
  const cols = inter.length ? [callers, inter, apis] : [callers, apis];
  const [box, bw] = useWidth(640);
  const W = Math.max(640, bw), rowH = 34, H = Math.max(...cols.map((c) => c.length)) * rowH + 24;
  const X = inter.length ? [0, W * 0.5, W * 0.8] : [0, W * 0.7];
  const colW = inter.length ? [W * 0.34, W * 0.2, W * 0.2] : [W * 0.42, W * 0.3];
  const pos = {};
  cols.forEach((c, ci) => { const off = (H - c.length * rowH) / 2; c.forEach((k, i) => { pos[k] = { x: X[ci], y: off + i * rowH + rowH / 2, w: colW[ci], col: ci }; }); });
  const [hov, setHov] = useState(null);
  const lit = (l) => !hov || l[0] === hov || l[1] === hov || (inter.includes(l[0]) && links.some((m) => m[1] === l[0] && m[0] === hov));
  const maxC = Math.max(1, ...links.map((l) => l[2]));
  return html`<div class="cg" ref=${box} onPointerLeave=${() => setHov(null)}><svg viewBox=${`0 0 ${W} ${H}`} style=${{ width: W + "px", minWidth: 0 }} role="img" aria-label=${`Methods that call retiring APIs: ${callers.length} shown, flowing into ${apis.join(", ")}`}>
    ${links.map((l, k) => { const a = pos[l[0]], b = pos[l[1]]; if (!a || !b) return null;
      const x1 = a.x + a.w, x2 = b.x, mx = (x1 + x2) / 2;
      return html`<path key=${k} d=${`M${x1},${a.y} C${mx},${a.y} ${mx},${b.y} ${x2},${b.y}`} fill="none"
        stroke=${apiSet.has(l[1]) ? "var(--heat-hi)" : "var(--text-2)"} stroke-opacity=${lit(l) ? (hov ? 0.9 : 0.45) : 0.06}
        stroke-width=${1 + Math.sqrt(l[2] / maxC) * 7} class="cg-edge"><title>${l[2]} calls</title></path>`; })}
    ${Object.entries(pos).map(([k, p]) => { const lb = label(k), api = p.col === cols.length - 1, on = !hov || hov === k || links.some((l) => (l[0] === hov && l[1] === k) || (l[1] === hov && l[0] === k));
      return html`<g key=${k} class=${"cg-node" + (api ? " api" : "")} opacity=${on ? 1 : 0.3} onPointerEnter=${() => setHov(k)} tabindex="0" onFocus=${() => setHov(k)} onBlur=${() => setHov(null)} role="img" aria-label=${`${lb.scope}${lb.file ? " in " + lb.file : ""}: ${total(k)} calls`}>
        <rect x=${p.x} y=${p.y - 13} width=${p.w} height="26" rx="7" />
        <text x=${p.x + 10} y=${p.y + (lb.file ? -1 : 4)} class="cg-t">${api ? lb.scope + "()" : lb.scope.length > 40 ? "…" + lb.scope.slice(-39) : lb.scope}</text>
        ${lb.file ? html`<text x=${p.x + 10} y=${p.y + 10} class="cg-s">${lb.file}</text>` : null}
        <text x=${p.x + p.w - 8} y=${p.y + 4} text-anchor="end" class="cg-n">${total(k)}</text></g>`; })}
  </svg></div>`;
}

/* ---------- where bugs came from: changes x bug-introducing commits per file (square-root scales) ---------- */
function Hotspots({ hot, clock }) {
  const [box, W] = useWidth(300);
  const H = Math.round(Math.min(440, Math.max(300, W * 0.48))), L = 56, R = 20, T = 20, B = 44;
  const maxX = Math.max(...hot.map((h) => h[2]), 1), maxY = Math.max(...hot.map((h) => h[1]), 1);
  const x = (v) => L + Math.sqrt(v / maxX) * (W - L - R), y = (v) => T + (1 - Math.sqrt(v / maxY)) * (H - T - B);
  const med = (arr) => { const s = [...arr].sort((a, b) => a - b); return s[Math.floor(s.length / 2)]; };
  const mx = med(hot.map((h) => h[2])), my = med(hot.map((h) => h[1]));
  const keys = (h) => (h[3] ? h[3].split(",").map((s) => s.trim()).filter(Boolean) : []);
  const [hov, setHov] = useState(null);
  const top = [...hot].sort((a, b) => (b[1] / maxY + b[2] / maxX) - (a[1] / maxY + a[2] / maxX)).slice(0, 5).map((h) => h[0]);
  const hv = hov != null ? hot[hov] : null;
  return html`<div class="chartbox" ref=${box} onPointerLeave=${() => setHov(null)}>
    <svg viewBox=${`0 0 ${W} ${H}`} role="img" aria-label="Files by changes in the last year and by bug-introducing commits; top right are hotspots">
      <rect x=${x(mx)} y=${T} width=${Math.max(0, W - R - x(mx))} height=${Math.max(0, y(my) - T)} fill="var(--heat-hi)" opacity=".07" rx="6" />
      <text x=${W - R - 8} y=${T + 16} text-anchor="end" class="axis strong">Hotspots: busy and bug-prone</text>
      <line x1=${x(mx)} x2=${x(mx)} y1=${T} y2=${H - B} stroke="var(--line-2)" stroke-dasharray="3 4" />
      <line x1=${L} x2=${W - R} y1=${y(my)} y2=${y(my)} stroke="var(--line-2)" stroke-dasharray="3 4" />
      <line x1=${L} x2=${W - R} y1=${H - B} y2=${H - B} stroke="var(--line)" />
      ${[...new Set([0, Math.round(maxY / 4), maxY])].map((v) => html`<text key=${v} x=${L - 8} y=${y(v) + 3.5} text-anchor="end" class="axis">${v}</text>`)}
      ${[...new Set([0, Math.round(maxX / 4), maxX])].map((v) => html`<text key=${"x" + v} x=${x(v)} y=${H - B + 16} text-anchor="middle" class="axis">${v}</text>`)}
      <text x=${(L + W - R) / 2} y=${H - 6} text-anchor="middle" class="axis">Changes in the year before ${fmt(clock)}</text>
      <text x=${14} y=${(T + H - B) / 2} text-anchor="middle" class="axis" transform=${`rotate(-90 14 ${(T + H - B) / 2})`}>Bug-introducing commits</text>
      ${hot.map((h, k) => { const hs = h[2] >= mx && h[1] >= my, r = 4 + Math.min(10, keys(h).length * 1.5);
        return html`<circle key=${h[0]} cx=${x(h[2])} cy=${y(h[1])} r=${r} fill=${hs ? "var(--heat-hi)" : "var(--text-3)"} fill-opacity=${hov === k ? 0.95 : hs ? 0.55 : 0.3}
          stroke=${hov === k ? "var(--text)" : "var(--pane)"} stroke-width="1.5" onPointerEnter=${() => setHov(k)} tabindex="0" onFocus=${() => setHov(k)} onBlur=${() => setHov(null)}
          aria-label=${`${h[0]}: ${h[1]} bug-introducing commits, ${h[2]} changes in the year`} />`; })}
      ${hot.filter((h) => top.includes(h[0])).map((h) => html`<text key=${"l" + h[0]} x=${x(h[2]) + 14} y=${y(h[1]) + 4} class="axis strong">${h[0].split("/").pop()}</text>`)}
    </svg>
    ${hv ? html`<div class="tip" style=${{ left: x(hv[2]) / W * 100 + "%", top: y(hv[1]) / H * 100 + "%" }}><b>${hv[0].split("/").pop()}</b>
      <span>${hv[1]} bug-introducing commits · ${hv[2]} changes in the year</span>
      ${keys(hv).length ? html`<span class="mono keys">fixed in ${keys(hv).slice(0, 6).join(", ")}${keys(hv).length > 6 ? ` and ${keys(hv).length - 6} more` : ""}</span>` : null}</div>` : null}
  </div>`;
}

/* ---------- export for a coding agent: /api/export, built server-side from the same rows ---------- */
function Export({ repo, files }) {
  const ref = useRef(null), dl = useRef(null);
  const [agent, setAgent] = useState("claude");
  const [text, setText] = useState({});
  const [copied, copy] = useCopy();
  const file = agent === "codex" ? "AGENTS.md" : "CLAUDE.md";
  const load = (k) => get("/api/export" + qs({ repo, agent: k })).then((t) => setText((o) => ({ ...o, [k]: t })), (e) => setText((o) => ({ ...o, [k]: { error: e.message } })));
  const pick = (k) => { setAgent(k); if (!text[k]) load(k); };
  const open = (k) => { pick(k); ref.current.showModal(); requestAnimationFrame(() => dl.current && dl.current.focus()); };
  const body = text[agent];
  const save = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([body], { type: "text/markdown" })); a.download = file; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); };
  const ready = typeof body === "string";
  return html`<div class="export">
    <button class="btn" onClick=${() => open("claude")}>Export for Claude Code</button>
    <button class="btn ghost" onClick=${() => open("codex")}>Export for Codex</button>
    <span class="sub">One file a coding agent reads first, instead of crawling ${n(files)} files</span>
    <dialog ref=${ref} class="xp" aria-labelledby="xp-h" onClick=${(e) => e.target === ref.current && ref.current.close()}>
      <div class="xp-h"><div><h2 id="xp-h">${file}</h2><small>Areas, owning teams, hotspots, retiring APIs, who calls them and ready swaps for <code>${repo}</code>.
        ${ready ? ` ${n(body.split("\n").length)} lines, about ${n(Math.round(body.length / 4))} tokens.` : ""} Facts only, no people, no ticket text. Nothing is sent anywhere: you take the file.</small></div>
        <div class="seg" role="group" aria-label="Agent">${[["claude", "Claude Code"], ["codex", "Codex"]].map(([k, l]) => html`<button key=${k} aria-pressed=${agent === k} onClick=${() => pick(k)}>${l}</button>`)}</div></div>
      ${ready ? html`<pre class="xp-body">${body}</pre>` : body && body.error ? html`<div class="state err">${body.error}</div>` : html`<div class="state"><span class="spin" aria-hidden="true"></span>Building the file…</div>`}
      <div class="acts" style=${{ flexWrap: "wrap" }}><button class="btn" ref=${dl} disabled=${!ready} onClick=${save}><${Icon} d=${IC.down} size=${14} />Download ${file}</button>
        ${ready ? html`<${CopyBtn} text=${body} id="xp" label="Copy" copied=${copied} copy=${copy} />` : null}
        <button class="btn ghost" onClick=${() => ref.current.close()}>Close</button></div>
    </dialog></div>`;
}

/* ---------- what broke and how we fixed it (PLAN §16): real failures, from the reports and the merged fixes ---------- */
const BROKE = [
  ["The fact gate read ticket keys as numbers", "Texts for teams fell back to the template 41 of 68 times: each cited a GitHub fix key like #6921, and the gate checked it as a bare number.",
    "#N is now checked as a ticket key. 0 of 68 fall back, and all 200 planted errors are still caught.", "#122"],
  ["A retrain crash looked like \"not better, kept\"", "Every way the retrain could fail read as a calm refusal, so a crash or an out-of-memory kill would have alerted nobody.",
    "The gate exits 3 on a refusal and 99 on nothing new; anything else fails the run and alerts.", "#178"],
  ["A broken image was one step from serving", "A planted scorer that reads a feature as 0 passed the build.", "The smoke test compares /score with the model pipeline's own risk, so it stops before any push; on main a canary watches 10% of traffic first.", "#157, #181"],
  ["The endpoint trusted Content-Length", "A negative length was accepted, and an oversized body was refused before it was read, which Windows clients saw as a reset instead of a 413.",
    "Negative lengths are refused, and the body is drained before the 413.", "#143"],
  ["Main went red when two PRs met", "Each PR passed alone; together the deployment tests ran where their packages weren't installed.", "Deployment tests run in the model's environment, where their packages are.", "#142"],
];
function Broke() {
  return html`<section class="card" id="broke"><div class="card-h"><h2>What broke, and how we fixed it</h2><small>Real failures from this project, each with the change that fixed it</small></div>
    <ol class="broke">${BROKE.map(([t, what, fix, pr]) => html`<li key=${t}><b>${t}</b><p>${what}</p><p class="fix"><span class="fx-word">Fix</span>${fix} <span class="mono muted">${pr}</span></p></li>`)}</ol></section>`;
}

const STEPS = [["where", "See where the work is", "Busy areas glow, quiet ones can wait"], ["risk", "Know where bugs hide", "Files that change a lot and break a lot"],
  ["retiring", "Find what's being retired", "Who still calls the old APIs"], ["push", "Check your first push", "The nine push checks and the model's risk"]];

export function Home({ go, params }) {
  const { data: v, error, loading } = useApi("/api/home" + qs({ repo: params.repo }));
  const repo = useMemo(() => v && shape(v), [v]);
  const hottest = (r) => r.areas.reduce((b, a, i) => (a.changes_90 > r.areas[b].changes_90 ? i : b), 0);
  const [selBy, setSel] = useState({});
  if (loading) return html`<${Loading} />`;
  if (error) return html`<${Failed} error=${error} />`;
  if (!repo.areas.length) return html`<div class="home"><${DataLine} meta=${v.meta} /><p class="empty-note">This repository has no files in the block.</p></div>`;
  const sel = selBy[repo.repo] ?? hottest(repo);
  const quiet = 1 - repo.touched / repo.files;
  const busy = [...repo.areas].sort((x, y) => y.changes_90 - x.changes_90).slice(0, 2);
  const quietBig = [...repo.areas].sort((x, y) => (y.files - y.touched) - (x.files - x.touched))[0];
  const order = [...repo.areas].sort((x, y) => y.changes_90 - x.changes_90);
  const max90 = Math.max(1, order[0].changes_90);
  const unowned = repo.areas.filter((a) => !a.team).reduce((s, a) => s + a.files, 0) / repo.files;
  const jump = (id) => (id === "push" ? go("push") : document.getElementById(id)?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" }));
  const repos = v.links.map(([, href]) => new URLSearchParams(href.split("?")[1] || "").get("repo")).filter(Boolean);
  const kbTotal = repo.kb.reduce((s, r) => s + (r.rows || 0), 0);
  return html`<div class="home">
    <${DataLine} meta=${v.meta} />
    <section class="hero">
      ${repos.length > 1 ? html`<label class="repo-pick"><span class="sub">Repository</span><select id="repo" value=${repo.repo} onChange=${(e) => go("home", { repo: e.target.value })}>
        ${repos.map((r) => html`<option key=${r} value=${r}>${r}</option>`)}</select></label>` : null}
      <h1><span class="big">${pctOf(quiet)}</span> of ${short(repo.repo)}'s code hasn't changed in a year.</h1>
      <p>${busy.length > 1 ? html`The work is in <code>${busy[0].area}</code> and <code>${busy[1].area}</code>: start there. ` : null}${quietBig ? html`<code>${quietBig.area}</code> holds ${n(quietBig.files)} files and only ${n(quietBig.touched)} changed, so it can wait.` : null}
        <span class="muted"> A year here means the year before ${short(repo.repo)}'s latest commit in the block, ${fmt(repo.clock)}.</span></p>
      <${Export} repo=${repo.repo} files=${repo.files} />
      <ol class="story">${STEPS.map(([id, t, s], k) => html`<li key=${id}><button onClick=${() => jump(id)}><span class="story-n">${k + 1}</span><span><b>${t}</b><small>${s}</small></span><${Icon} d=${id === "push" ? IC.arrow : IC.down} size=${14} /></button></li>`)}</ol>
    </section>
    <section class="work" id="where">
      <div class="card map-card"><div class="card-h"><h2>Where the work is</h2><small>${n(repo.files)} files in ${repo.areas.length} areas · select one</small></div>
        <${Heatmap} repo=${repo} sel=${sel} setSel=${(i) => setSel((o) => ({ ...o, [repo.repo]: i }))} /></div>
      <aside class="card"><${StartHere} a=${repo.areas[sel]} go=${go} repo=${repo.repo} /></aside>
    </section>
    <section class="problems">
      <div class="card" id="risk"><div class="card-h"><h2>Where bugs came from</h2><small>${repo.hot.length ? `The ${repo.hot.length} files with the most bug-introducing commits, traced from fixed tickets · hover for the fix keys` : ""}</small></div>
        ${repo.hot.length ? html`<${Hotspots} hot=${repo.hot} clock=${repo.clock} key=${repo.repo} />` : html`<p class="empty-note">${repo.hotNote || "No bug history for this repository in the block."}</p>`}</div>
      <div class="card" id="retiring"><div class="card-h"><h2>Who calls out</h2><small>${repo.links.length ? "Methods calling the APIs being retired, two levels deep · hover a method" : ""}</small></div>
        ${repo.links.length ? html`<${CallGraph} links=${repo.links} key=${repo.repo} />` : html`<p class="empty-note">${repo.graphNote || `${short(repo.repo)} calls none of the APIs being retired, so there's no call graph for it.`}</p>`}</div>
    </section>
    <section class="card"><div class="card-h"><h2>What each area is doing</h2><small>Busiest first, last 90 days · ${pctOf(unowned)} of files are in areas with no owning team</small></div>
      <div class="bars">${order.map((a) => { const idx = repo.areas.indexOf(a); return html`<button class="bar-row area-row" key=${a.area} onClick=${() => { setSel((o) => ({ ...o, [repo.repo]: idx })); jump("where"); }} aria-current=${idx === sel ? "true" : "false"}>
        <span class="bar-label mono">${a.area}</span><span class="bar-track"><span class="bar-fill" style=${{ width: a.changes_90 / max90 * 100 + "%" }}></span></span>
        <span class="bar-val">${n(a.changes_90)}</span><span class="sub">${a.team || "unowned"}</span></button>`; })}</div></section>
    <${Broke} />
    ${repo.kb.length ? html`<section aria-label="What Meridian has read"><div class="kb-strip">
      ${[...repo.kb].sort((a, b) => b.rows - a.rows).slice(0, 7).map((r) => html`<div key=${r.table}><b>${n(r.rows)}</b><span class="mono">${r.table}</span></div>`)}
      <div><b>${n(kbTotal)}</b><span>rows in ${repo.kb.length} tables</span></div></div>
      ${repo.kbNote ? html`<p class="sub kb-note">${repo.kbNote}</p>` : null}</section>` : null}
  </div>`;
}
