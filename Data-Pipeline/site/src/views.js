/* The four doors of 02-product, each one views.py endpoint: staff, owner, consumer, push, plus the ticket tools. */
import React, { useState, useEffect } from "react";
import { html, useApi, qs, sec, recs, n, fmt, daysSince, short, Icon, IC, Status, useCopy, CopyBtn, useListKeys, Loading, Failed, DataLine, Section, Table, RISK } from "./lib.js";

const Head = ({ title, sub, children }) => html`<div class="head"><div><h1>${title}</h1>${sub ? html`<p>${sub}</p>` : null}</div>${children}</div>`;
const Fig = ({ v, l, cls }) => html`<div class="fig"><b class=${cls || ""}>${v}</b><span>${l}</span></div>`;

/* ---------- Retirements (staff engineer): every retiring API, and the cross-team work it hides ---------- */
export function Retirements({ go, params }) {
  const { data: sc, error, loading } = useApi("/api/scopes");
  const rows = sc ? recs(sec(sc, "Retiring APIs")) : [];
  const start = Math.max(0, rows.findIndex((r) => r.repo === params.repo && r.symbol === params.symbol));
  const [i, setI] = useState(start);
  useEffect(() => setI(start), [sc]);
  const m = rows[Math.min(i, rows.length - 1)];
  const open = (idx) => rows[idx] && go("owner", { symbol: rows[idx].symbol, repo: rows[idx].repo });
  useListKeys(rows.length, setI, open, i);
  if (loading) return html`<${Loading} />`;
  if (error) return html`<${Failed} error=${error} />`;
  const total = rows.reduce((a, r) => a + r["call sites"], 0);
  return html`<div class="content">
    <div class="list-col">
      <${DataLine} meta=${sc.meta} />
      <${Head} title="Retirements" sub="Every API being retired, who still calls it, and the cross-team work around it.">
        <div class="figures"><${Fig} v=${n(rows.length)} l="APIs being retired" /><${Fig} v=${n(total)} l="call sites left" /></div></${Head}>
      <div class="section"><div class="section-h"><h2>APIs</h2><small>Most call sites first · J / K to move · Enter opens the migration</small></div>
        <div class="list">
          <div class="cols c-scope" aria-hidden="true"><span>API</span><span class="r">Call sites</span><span class="r">Teams</span></div>
          ${rows.length ? rows.map((r, idx) => html`<button key=${r.repo + r.symbol} class="row c-scope" aria-current=${idx === i ? "true" : "false"} onClick=${() => setI(idx)} onDoubleClick=${() => open(idx)}>
            <span style=${{ minWidth: 0 }}><span class="name mono">${r.symbol}</span><span class="sub mono">${r.repo}</span></span>
            <span class="r">${n(r["call sites"])}</span><span class="r muted">${n(r.teams)}</span></button>`) : html`<p class="empty">No retiring API has call sites in this block.</p>`}
        </div></div>
    </div>
    <aside class="peek" aria-live="polite">${m ? html`<${StaffPeek} key=${m.repo + m.symbol} m=${m} go=${go} />` : null}</aside>
  </div>`;
}
function StaffPeek({ m, go }) {
  const { data: v, error, loading } = useApi("/api/staff" + qs({ repo: m.repo, symbol: m.symbol }));
  const [more, setMore] = useState(false);
  return html`<div class="peek-in">
    <div class="pk-title"><span class="sub mono">${m.repo}</span><h2><code>${m.symbol}</code></h2></div>
    <div class="acts" style=${{ flexWrap: "wrap" }}><button class="btn" onClick=${() => go("owner", { symbol: m.symbol, repo: m.repo })}>Open the migration <kbd>↵</kbd></button>
      <button class="btn ghost" onClick=${() => go("tickets", { repo: m.repo, symbol: m.symbol })}>Ticket tools</button></div>
    ${loading ? html`<${Loading} what="Reading this API's rows" />` : error ? html`<${Failed} error=${error} />` : html`
      ${v.meta.data === "fixture" ? html`<${DataLine} meta=${v.meta} />` : null}
      ${v.sections.filter((s) => s.rows ? s.rows.length || more : true).map((s) => html`<${Section} key=${s.title} s=${s} max=${6} />`)}
      ${v.sections.some((s) => s.rows && !s.rows.length) ? html`<button class="btn ghost" onClick=${() => setMore(!more)}>${more ? "Hide empty checks" : `Show the ${v.sections.filter((s) => s.rows && !s.rows.length).length} checks with nothing to report`}</button>` : null}`}
  </div>`;
}

/* ---------- The migration (API owner): which team holds the date, and why ---------- */
const STALL_WORD = { STALLED: "STALLED", blocked: "blocked", moving: "moving", done: "done" };
export function Migration({ go, params }) {
  const path = params.symbol ? `/api/owner/${encodeURIComponent(params.symbol)}` + qs({ repo: params.repo }) : null;
  const scopes = useApi(params.symbol ? null : "/api/scopes");
  useEffect(() => { const r = scopes.data && recs(sec(scopes.data, "Retiring APIs"))[0]; if (r) go("owner", { symbol: r.symbol, repo: r.repo }, true); }, [scopes.data]);
  const { data: v, error, loading } = useApi(path);
  const [i, setI] = useState(0);
  const teams = v ? recs(sec(v, "Per team")) : [];
  const reasons = v ? sec(v, "Possible reasons, for each stalled team") : null;
  const repo = v ? v.scope.repo : params.repo;
  const openTeam = (idx) => teams[idx] && go("team", { team: teams[idx].team, repo, symbol: params.symbol });
  useListKeys(teams.length, setI, openTeam, i);
  if (!path || loading) return html`<${Loading} />`;
  if (error) return html`<${Failed} error=${error} />`;
  const done = recs(sec(v, "Done"))[0] || {}, eta = recs(sec(v, "ETA"))[0] || {};
  const t = teams[Math.min(i, teams.length - 1)];
  const text = t && reasons ? reasons.texts.find((x) => x.team === t.team || x.target === t.team) : null;
  return html`<div class="content">
    <div class="list-col">
      <${DataLine} meta=${v.meta} />
      <${Head} title=${html`<code>${v.scope.symbol}</code>`} sub=${html`<span class="mono">${repo}</span> · ${n(teams.length)} consuming ${teams.length === 1 ? "team" : "teams"}`} />
      <div class="figures">
        <${Fig} v=${`${n(done.removed)} / ${n(done["call sites"])}`} l="call sites removed" />
        <${Fig} v=${eta["estimated done"] ? fmt(eta["estimated done"]) : "No ETA yet"} l=${eta["estimated done"] ? "estimated done" : "too few removals to project"} />
        <${Fig} v=${n(teams.filter((x) => x.status === "STALLED").length)} l="teams stalled" cls=${teams.some((x) => x.status === "STALLED") ? "late" : ""} />
      </div>
      <div class="section"><div class="section-h"><h2>Teams</h2><small>J / K to move · Enter opens the team's list</small></div>
        <div class="list"><div class="cols c-team" aria-hidden="true"><span>Team</span><span class="r">Left</span><span>Status</span><span>Waiting on</span></div>
          ${teams.map((x, idx) => html`<button key=${x.team} class="row c-team" aria-current=${idx === i ? "true" : "false"} onClick=${() => setI(idx)} onDoubleClick=${() => openTeam(idx)}>
            <span style=${{ minWidth: 0 }}><span class="name">${x.team}</span><span class="sub">${n(x.risky)} in bug-prone files</span></span>
            <span class="r">${n(x.left)}</span><${Status} s=${x.status} since=${x["days since a change"] != null ? `${n(x["days since a change"])}d` : ""} />
            <span class="sub" style=${{ whiteSpace: "normal" }}>${x["waiting on"] || "–"}</span></button>`)}</div></div>
      <div class="section"><div class="section-h"><h2>Will it leave on time?</h2></div>
        <p class="empty-note">No burn-down yet: the block holds one snapshot, and a burn-down needs the call sites removed between two. The refresh job's next blocks will draw it.</p></div>
    </div>
    <aside class="peek" aria-live="polite">${t ? html`<div class="peek-in" key=${t.team}>
      <div class="pk-title"><${Status} s=${t.status} /><h2>${t.team}</h2><span class="sub">${n(t.left)} call sites left${t["days since a change"] != null ? ` · no change in ${n(t["days since a change"])} days` : ""}</span></div>
      ${text ? html`<${Reasons} t=${text} />` : html`<p class="note">${t.status === "STALLED" ? "No reasons stored for this team." : `${t.team} isn't stalled, so there are no reasons to rank.`}</p>`}
      <div class="acts"><button class="btn" onClick=${() => openTeam(i)}>Open ${short(t.team)}'s list <kbd>↵</kbd></button></div>
    </div>` : null}</aside>
  </div>`;
}
function Reasons({ t }) {
  const [copied, copy] = useCopy();
  const list = (t.facts && t.facts.reasons) || [];
  return html`<div>
    ${list.length ? html`<div class="reasons">${list.map((r, k) => html`<div class="reason" key=${r.reason}><div class="reason-h"><span style=${{ color: "var(--text)" }}>${k + 1}. ${r.reason}</span><span class="muted">${n(r.evidence)} ${r.evidence === 1 ? "piece" : "pieces"} of evidence</span></div></div>`)}
      <p class="sub" style=${{ whiteSpace: "normal" }}>In the auditor's order. Reasons, never probabilities.</p></div>` : null}
    <div class="note"><div class="src">${t.source === "template" ? "Template" : "LLM · passed the gate"}${t.model ? " · " + t.model : ""}</div><div class="body">${t.body}</div>
      <div class="acts"><${CopyBtn} text=${t.body} id="note" label="Copy note" copied=${copied} copy=${copy} /></div></div>
  </div>`;
}

/* ---------- My team (API consumer): call sites file by file, with the codemod's swaps ---------- */
export function Team({ go, params }) {
  const path = params.team ? `/api/consumer/${encodeURIComponent(params.team)}` + qs({ repo: params.repo, symbol: params.symbol }) : null;
  const scopes = useApi(params.team ? null : "/api/scopes");
  useEffect(() => { const r = scopes.data && recs(sec(scopes.data, "Retiring APIs"))[0]; if (r) go("owner", { symbol: r.symbol, repo: r.repo }, true); }, [scopes.data]);
  const { data: v, error, loading } = useApi(path);
  const tix = useApi(v ? "/api/tickets" + qs({ repo: v.scope.repo, symbol: v.scope.symbol }) : null);
  const [copied, copy] = useCopy();
  if (!path || loading) return html`<${Loading} />`;
  if (error) return html`<${Failed} error=${error} />`;
  const files = sec(v, "Call sites per file"), diffs = sec(v, "Diffs");
  const sites = files ? files.rows.reduce((a, r) => a + r[1], 0) : 0;
  const allSwaps = diffs && diffs.diffs.length ? diffs.diffs.map((d) => d.body).join("\n\n") : "";
  const mine = tix.data ? tix.data.sections.filter((s) => s.texts).map((s) => ({ ...s, texts: s.texts.filter((t) => !t.team || t.team === params.team) })) : [];
  return html`<div class="content">
    <div class="list-col">
      <${DataLine} meta=${v.meta} />
      <${Head} title=${params.team} sub=${html`Your <code>${v.scope.symbol}</code> calls in <span class="mono">${v.scope.repo}</span>, file by file. Each has the codemod's one-line swap, except where a person has to decide.`} />
      <div class="figures"><${Fig} v=${n(sites)} l="call sites left" /><${Fig} v=${n(files ? files.rows.length : 0)} l="files" />
        <${Fig} v=${n((sec(v, "Blocked by") || { rows: [] }).rows.length)} l="sites blocked by others" /><${Fig} v=${n((sec(v, "You are blocking") || { rows: [] }).rows.length)} l="teams you block" /></div>
      ${v.sections.filter((s) => s.title !== "Diffs").map((s) => html`<${Section} key=${s.title} s=${s} max=${15} />`)}
      ${diffs && diffs.diffs.length > 1 ? html`<div class="acts"><${CopyBtn} text=${allSwaps} id="all" label=${`Copy all ${diffs.diffs.length} swaps`} copied=${copied} copy=${copy} /></div>` : null}
      ${diffs ? html`<${Section} s=${diffs} />` : null}
      <p class="sub" style=${{ whiteSpace: "normal" }}>A call inside an <code>except</code> block has no swap: a person decides the handling.</p>
    </div>
    <aside class="peek"><div class="peek-in">
      <div class="pk-title"><h2>Tickets for ${short(params.team)}</h2><span class="sub" style=${{ whiteSpace: "normal" }}>Copy a draft or rewrite into your tracker; Meridian reads what happens to it</span></div>
      ${tix.loading ? html`<${Loading} what="Reading ticket tools" />` : mine.some((s) => s.texts.length) ? mine.filter((s) => s.texts.length).map((s) => html`<${Section} key=${s.title} s=${s} />`) : html`<p class="empty-note">No ticket suggestions for this team on this block.</p>`}
    </div></aside>
  </div>`;
}

/* ---------- Check a push (developer): the stored P1–P9 notes, and the model's risk as a ranking ---------- */
const CHECKS = { P1: "Drift", P2: "Legacy use", P3: "Collision", P4: "New block", P5: "Ticket can move", P6: "Mismatch", P7: "Risky edit", P8: "Bad key", P9: "Defect risk" };
const EXAMPLES = [["apache/spark", "3d819389819557523f373c192f88a594b665734d", "a validation commit the model was judged on"]];
export function Push({ go, params }) {
  const list = useApi("/api/push");
  const pushes = list.data ? recs(sec(list.data, "Pushes")) : [];
  const sha = params.sha || (pushes[0] && pushes[0].push);
  const one = useApi(sha ? "/api/push/" + encodeURIComponent(sha) : null);
  const [score, setScore] = useState({});
  const [ex, setEx] = useState(0);
  const run = () => {
    const [repo, s] = EXAMPLES[ex];
    setScore({ loading: true });
    fetch(RISK + "/score", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repo, sha: s }) })
      .then((r) => r.json().then((j) => (r.ok ? setScore({ data: j }) : setScore({ error: j.error || `the risk service answered ${r.status}` }))))
      .catch(() => setScore({ error: "the risk service didn't answer" }));
  };
  if (list.loading) return html`<${Loading} />`;
  if (list.error) return html`<${Failed} error=${list.error} />`;
  const notes = one.data ? sec(one.data, "Notes") : null;
  const s = score.data;
  return html`<div class="content">
    <div class="list-col">
      <${DataLine} meta=${list.data.meta} />
      <${Head} title="Check a push" sub="Before you merge: the nine push checks run against the codebase, and the model ranks how likely the push is to bring a bug. The checks write notes; they never block a merge." />
      <div class="section"><div class="section-h"><h2>Pushes with notes</h2><small>Checked on every push; pick one</small></div>
        <div class="list" role="radiogroup" aria-label="Pushes">${pushes.map((p) => html`<label class="push" key=${p.push}>
          <input type="radio" name="push" checked=${sha === p.push} onChange=${() => go("push", { sha: p.push }, true)} />
          <span class="name"><code>${p.push}</code></span><span class="sub" style=${{ gridColumn: 3, gridRow: 1 }}>${n(p.notes)} ${p.notes === 1 ? "note" : "notes"}</span><span class="sub">base run ${p["base run"]}</span></label>`)}</div></div>
      ${notes ? html`<div class="section"><div class="section-h"><h2>The checks</h2><small>${notes.note}</small></div>
        <div class="list">${Object.entries(CHECKS).map(([k, nm]) => { const hit = notes.rows.filter((r) => r[0] === k);
          return html`<div class="row checks" key=${k} style=${{ cursor: "default" }}><code class="muted">${k}</code>
            <span style=${{ minWidth: 0 }}><span class="name" style=${{ fontWeight: 500 }}>${nm}</span>${hit.map((r, j) => html`<span key=${j} class="sub mono" style=${{ whiteSpace: "normal" }}>${r[2]} · ${r[3]}</span>`)}</span>
            <span class=${"chk " + (hit.length ? "info" : "pass")}>${hit.length ? "Note" : "Passed"}</span></div>`; })}</div></div>` : one.loading ? html`<${Loading} what="Reading the notes" />` : null}
    </div>
    <aside class="peek" aria-live="polite"><div class="peek-in">
      <div class="pk-title"><h2>The model's risk</h2><span class="sub" style=${{ whiteSpace: "normal" }}>Live from the scoring service. A ranking, never a verdict: P9 alerts on lines × files, since the model can't reach the 0.80 precision bar.</span></div>
      <label class="repo-pick"><span class="sub">Commit</span><select id="example" value=${ex} onChange=${(e) => { setEx(+e.target.value); setScore({}); }}>
        ${EXAMPLES.map(([r, c, why], k) => html`<option key=${c} value=${k}>${r} ${c.slice(0, 7)} · ${why}</option>`)}</select></label>
      <div><button class="btn" onClick=${run} disabled=${score.loading}>${score.loading ? "Scoring…" : "Score it"}</button></div>
      ${s ? html`<div class="score"><b>${s.risk.toFixed(2)}</b><span class="muted">risk · higher than ${Math.round(s.percentile_val * 100)}% of validation commits</span></div>
        <dl class="kv"><dt>P9, lines × files</dt><dd>${s.p9.alert ? "above the line: a note for the reviewer" : "below the line"}</dd><dt>Model</dt><dd class="mono">${s.model.version.slice(0, 7)} · md5 ${s.model.md5.slice(0, 8)}</dd></dl>`
        : score.error ? html`<div class="state err" role="alert">${score.error}</div>` : null}
      <p class="sub" style=${{ whiteSpace: "normal" }}>No LLM is involved in checking a push.</p>
    </div></aside>
  </div>`;
}

/* ---------- ticket tools (staff engineer): vague tickets, rewrites, drafts for untracked work, wrong owners ---------- */
export function Tickets({ params }) {
  const { data: v, error, loading } = useApi("/api/tickets" + qs({ repo: params.repo, symbol: params.symbol }));
  if (loading) return html`<${Loading} />`;
  if (error) return html`<${Failed} error=${error} />`;
  return html`<div class="content single"><div class="list-col">
    <${DataLine} meta=${v.meta} />
    <${Head} title="Ticket tools" sub=${html`Drafts and rewrites for <code>${v.scope.symbol}</code> in <span class="mono">${v.scope.repo}</span>. Copy them into your tracker: Meridian never files or sends anything.`} />
    ${v.sections.map((s) => html`<${Section} key=${s.title} s=${s} max=${8} cell=${(c, val) => (typeof val === "string" && val.length > 160 ? val.slice(0, 160) + "…" : val == null ? "–" : typeof val === "number" ? n(val) : val)} />`)}
  </div></div>`;
}
