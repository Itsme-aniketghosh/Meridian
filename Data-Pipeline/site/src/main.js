/* The shell: Home for everyone, then views by role (02-product's personas). Routes live in the hash, so the
   static site needs no server routing: #/home?repo=, #/retirements, #/owner/<symbol>?repo=, #/team/<team>?repo=&symbol=,
   #/push/<sha>, #/tickets?repo=&symbol= */
import React, { useState, useEffect, useMemo } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import "./site.css";
import { html, useApi, sec, recs, n, fmt, short, Icon, IC, Logo } from "./lib.js";
import { Home } from "./home.js";
import { Retirements, Migration, Team, Push, Tickets } from "./views.js";

// ?theme=light|dark pins a theme (a projector, a screenshot); otherwise the system's choice
const pinned = new URLSearchParams(location.search).get("theme");
if (pinned === "light" || pinned === "dark") document.documentElement.dataset.theme = pinned;

const ARG = { owner: "symbol", team: "team", push: "sha" };
function parse() {
  const [p, q] = location.hash.replace(/^#\/?/, "").split("?");
  const [view, ...rest] = p.split("/");
  const params = Object.fromEntries(new URLSearchParams(q || ""));
  if (ARG[view] && rest.length) params[ARG[view]] = decodeURIComponent(rest.join("/"));
  return { view: VIEWS[view] ? view : "home", params };
}
function href(view, params = {}) {
  const p = { ...params }, arg = ARG[view] && p[ARG[view]];
  if (arg) delete p[ARG[view]];
  const q = new URLSearchParams(Object.entries(p).filter(([, v]) => v != null && v !== "")).toString();
  return "#/" + view + (arg ? "/" + encodeURIComponent(arg) : "") + (q ? "?" + q : "");
}

const VIEWS = {
  home: ["Home", IC.home, Home], retirements: ["Retirements", IC.portfolio, Retirements], tickets: ["Ticket tools", IC.tickets, Tickets],
  owner: ["The migration", IC.migration, Migration], team: ["My team", IC.team, Team], push: ["Check a push", IC.push, Push],
};
const ROLES = { staff: ["Staff engineer", ["retirements", "tickets"]], owner: ["API owner", ["owner"]], consumer: ["API consumer", ["team", "owner"]], dev: ["Developer", ["push"]] };
const crumb = (view, p) => ({
  home: ["Home", p.repo && short(p.repo)], retirements: ["Retirements"], tickets: ["Retirements", "Ticket tools"],
  owner: ["Retirements", p.symbol], team: ["Teams", p.team && short(p.team)], push: ["Check a push", p.sha],
}[view].filter(Boolean));

function Command({ onClose, go }) {
  const sc = useApi("/api/scopes");
  const items = useMemo(() => [
    ...Object.entries(VIEWS).map(([id, [label]]) => ({ label: "Go to " + label, hint: "View", run: () => go(id) })),
    ...(sc.data ? recs(sec(sc.data, "Retiring APIs")).map((r) => ({ label: `${r.symbol} · ${short(r.repo)}`, hint: "Retiring API", run: () => go("owner", { symbol: r.symbol, repo: r.repo }) })) : []),
    ...(sc.data ? [...new Set(recs(sec(sc.data, "Retiring APIs")).map((r) => r.repo))].map((r) => ({ label: r, hint: "Repository", run: () => go("home", { repo: r }) })) : []),
  ], [sc.data]);
  const [q, setQ] = useState(""); const [i, setI] = useState(0);
  const list = items.filter((x) => x.label.toLowerCase().includes(q.trim().toLowerCase()));
  const run = (x) => { if (x) { x.run(); onClose(); } };
  return html`<div class="scrim" onMouseDown=${(e) => e.target === e.currentTarget && onClose()}>
    <div class="cmd" role="dialog" aria-modal="true" aria-label="Jump to">
      <input id="cmd-input" autoFocus placeholder="Jump to a view, API or repository…" value=${q} onInput=${(e) => { setQ(e.target.value); setI(0); }}
        onKeyDown=${(e) => { if (e.key === "ArrowDown") { e.preventDefault(); setI(Math.min(list.length - 1, i + 1)); } else if (e.key === "ArrowUp") { e.preventDefault(); setI(Math.max(0, i - 1)); } else if (e.key === "Enter") run(list[i]); else if (e.key === "Escape") onClose(); }} />
      <ul role="listbox">${list.length ? list.slice(0, 12).map((x, k) => html`<li key=${x.label + x.hint} role="option" aria-selected=${k === i} onMouseEnter=${() => setI(k)} onClick=${() => run(x)}><span>${x.label}</span><span>${x.hint}</span></li>`)
        : html`<li aria-disabled="true">Nothing matches "${q}"</li>`}</ul>
      <div class="cmd-foot"><span><kbd>↑</kbd> <kbd>↓</kbd> move</span><span><kbd>↵</kbd> open</span><span><kbd>esc</kbd> close</span><span class="muted">Filters this list only; nothing is sent</span></div>
    </div></div>`;
}

function App() {
  const [{ view, params }, setRoute] = useState(parse);
  const [cmd, setCmd] = useState(false);
  const [role, setRole] = useState("staff");
  const home = useApi("/api/home");
  useEffect(() => { const r = Object.keys(ROLES).find((k) => ROLES[k][1].includes(view)); if (r && !ROLES[role][1].includes(view)) setRole(r); }, [view]);
  useEffect(() => { const f = () => setRoute(parse()); addEventListener("hashchange", f); return () => removeEventListener("hashchange", f); }, []);
  useEffect(() => { const f = (e) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setCmd((c) => !c); } }; addEventListener("keydown", f); return () => removeEventListener("keydown", f); }, []);
  useEffect(() => { document.title = view === "home" ? "Meridian" : `${VIEWS[view][0]} · Meridian`; }, [view]);
  const go = (v, p = {}, replace = false) => {
    const h = href(v, p);
    if (replace) history.replaceState(null, "", h); else if (location.hash !== h) history.pushState(null, "", h);
    setRoute(parse()); if (!replace) scrollTo({ top: 0 });
  };
  const V = VIEWS[view][2];
  const navLink = (id) => html`<a key=${id} href=${href(id)} aria-current=${view === id ? "page" : undefined}><${Icon} d=${VIEWS[id][1]} />${VIEWS[id][0]}</a>`;
  const meta = home.data && home.data.meta;
  const path = crumb(view, params);
  return html`<div class="app">
    <nav class="side" aria-label="Meridian">
      <a class="ws" href="#/home" aria-label="Meridian home"><${Logo} h=${15} /></a>
      <button class="search" onClick=${() => setCmd(true)}><${Icon} d=${IC.search} size=${14} /><span class="lbl">Jump to</span><kbd>⌘K</kbd></button>
      <div class="nav">${navLink("home")}</div>
      <label class="role"><span class="group" style=${{ padding: 0 }}>Your views as</span>
        <select id="role" value=${role} onChange=${(e) => { setRole(e.target.value); go(ROLES[e.target.value][1][0]); }}>${Object.entries(ROLES).map(([k, [l]]) => html`<option key=${k} value=${k}>${l}</option>`)}</select></label>
      <div class="nav">${ROLES[role][1].map(navLink)}</div>
      <div class="side-foot">${meta ? html`<span>Block <code>${meta.run_id.slice(0, 8)}</code> · ${fmt(meta.snapshot_at)}</span>` : null}<span>Read-only · teams only, never people</span></div>
    </nav>
    <main>
      <div class="top">${path.map((c, k) => html`<${React.Fragment} key=${k}>${k ? html`<span class="sep">/</span>` : null}${k === path.length - 1 ? html`<b>${c}</b>` : html`<span>${c}</span>`}</${React.Fragment}>`)}</div>
      <div class="view" key=${view + JSON.stringify(params)}><${V} go=${go} params=${params} /></div>
    </main>
    ${cmd ? html`<${Command} onClose=${() => setCmd(false)} go=${go} />` : null}
  </div>`;
}
createRoot(document.getElementById("app")).render(html`<${App} />`);
