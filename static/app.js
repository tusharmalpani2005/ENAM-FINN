"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = (u, o) => fetch(u, o).then(async r => { if (!r.ok) { let m = r.status; try { m = (await r.json()).detail || m } catch { } throw new Error(m); } return r.json(); });
const ls = { get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d } catch { return d } }, set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)) } catch { } } };

const S = { note: "", manual: false, tab: "today", meta: null, hours: ls.get("hours", 24), volx: ls.get("volx", 2), px: ls.get("px", 4), cats: ls.get("cats", []), thread: [], netTicker: null, theme: null, mode: ls.get("mode", "dark") };
const ini = n => n.split(/\s+/).map(w => w[0]).slice(0, 2).join("");
const pct = (v, d = 1) => v == null ? "–" : `<span class="${v >= 0 ? "up" : "dn"}">${v >= 0 ? "+" : ""}${v.toFixed(d)}%</span>`;
const ago = iso => { const h = (Date.now() - new Date(iso)) / 36e5; return h < 1 ? "now" : h < 24 ? Math.round(h) + "h" : Math.round(h / 24) + "d"; };
const num = v => Number(v).toLocaleString("en-IN", { maximumFractionDigits: 1 });

function spark(vals, w = 120, h = 32, color = "var(--ac)") {
  const mn = Math.min(...vals), mx = Math.max(...vals), r = mx - mn || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * w).toFixed(1)},${(h - 3 - (v - mn) / r * (h - 6)).toFixed(1)}`).join(" ");
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
}
function bars(vals, labels, w = 300, h = 70, fmt = num) {
  const mx = Math.max(...vals.map(Math.abs)) || 1, bw = w / vals.length;
  return `<svg width="100%" viewBox="0 0 ${w} ${h + 16}">${vals.map((v, i) => { const bh = Math.abs(v) / mx * h; return `<rect x="${i * bw + 6}" y="${h - bh}" width="${bw - 12}" height="${bh}" rx="3" fill="${v < 0 ? "var(--dn)" : "var(--ac)"}" opacity=".85"/><text x="${i * bw + bw / 2}" y="${h + 12}" font-size="10" text-anchor="middle" fill="var(--mu)">${labels[i]}</text><text x="${i * bw + bw / 2}" y="${h - bh - 3}" font-size="9" text-anchor="middle" fill="var(--tx)">${fmt(v)}</text>` }).join("")}</svg>`;
}

/* ---------- sheet ---------- */
function openSheet(html) { $("#sheetbody").innerHTML = html; $("#sheet").hidden = false; $("#scrim").hidden = false; $("#sheet").scrollTop = 0; }
function closeSheet() { $("#sheet").hidden = true; $("#scrim").hidden = true; }
$("#scrim").onclick = closeSheet;

/* ---------- shared blocks ---------- */
function contactRow(c, extra = "") {
  const p = c.contact || c;
  return `<div class="link" data-act="person" data-id="${p.id}"><div class="av">${esc(ini(p.name))}</div><div><b>${esc(p.name)}</b> <span class="sub">${esc(p.role)}, ${esc(p.org)}</span>${extra ? `<div class="sub" style="color:#cfcbc0">${extra}</div>` : ""}</div></div>`;
}
function networkBlock(n) {
  if (!n) return "";
  let h = "";
  if (n.overlaps && n.overlaps.length) h += `<div class="sub" style="margin-top:8px">IN YOUR NETWORK</div>` + n.overlaps.map(o => contactRow(o.contact, esc(o.evidence.join(" · ")) + ` <span class="sub">(strength ${o.contact.strength}/5, last spoke ${o.contact.last_contacted_days}d ago)</span>`)).join("");
  else if (n.overlaps) h += `<div class="sub" style="margin-top:8px">No direct overlap with your network.</div>`;
  if (n.second_degree && n.second_degree.length) h += `<div class="sub" style="margin-top:8px">ONE CONNECTION AWAY</div>` + n.second_degree.map(s => contactRow(s.via, esc(s.path))).join("");
  if (n.who_to_call) h += `<div class="sub" style="margin-top:8px">WHO TO CALL</div>` + n.who_to_call.map(p => contactRow(p, esc(p.why))).join("");
  return h;
}
function resultsBlock(r) {
  const row = (k, l) => r[k] ? `<div>${l}</div><div class="r">${num(r[k].cur)}</div><div class="r">${pct(r[k].qoq)}</div><div class="r">${pct(r[k].yoy)}</div>` : "";
  return `<div class="metrics"><div class="h">₹ cr</div><div class="h r">Q</div><div class="h r">QoQ</div><div class="h r">YoY</div>${row("revenue", "Revenue")}${row("ebitda", "EBITDA")}${row("pbt", "PBT")}${row("pat", "PAT")}</div>` +
    (r.ebitda?.margin ? `<div class="sub">EBITDA margin ${r.ebitda.margin}% (${r.ebitda.margin_yoy_bps >= 0 ? "+" : ""}${r.ebitda.margin_yoy_bps} bps YoY)</div>` : "");
}
function filingCard(i) {
  const p = i.person;
  return `<div class="card ${i.tier}" data-id="${i.id}">
    <div class="top"><span class="tk">${i.ticker}</span><span class="cat">${esc(i.label)}</span><span class="sp"></span><span>${ago(i.ts)}</span></div>
    <div class="subject">${esc(i.subject)}</div>
    ${i.results ? resultsBlock(i.results) : ""}
    ${p ? `<div class="person"><span class="nm">${esc(p.name)}</span> · ${esc(p.role)}<br>${p.history.slice().reverse().map(h => `${esc(h.role)}, ${esc(h.org)} <span class="sub">${h.start}–${h.end || "now"}</span>`).join("<br>")}<br><span class="sub">${p.edu.map(e => esc(e.school) + " " + e.year).join(" · ")}</span></div>` : ""}
    ${i.why ? `<div class="why">${esc(i.why)}</div>` : ""}
    ${networkBlock(i.network)}
    <div class="row" style="margin-top:10px">${["results", "m&a", "capex"].includes(i.category) || store.ready(i.ticker) ? `<button class="btn" data-act="research" data-t="${i.ticker}">Dig in</button>` : ""}<button class="btn" data-act="ask" data-q="who should I call about ${esc(i.company)}">Who to call</button></div>
  </div>`;
}
const store = { ready: t => S.meta && S.meta.research_ready.includes(t) };

/* ---------- TODAY ---------- */
async function today() {
  $("main").classList.remove("ask-mode");
  const v = $("#view");
  v.innerHTML = `<div class="row wrap" style="margin:6px 0">${[[24, "24h"], [72, "3 days"], [168, "7 days"], [720, "30 days"]].map(([h, l]) => `<button class="chip ${S.hours == h ? "on" : ""}" data-act="hours" data-h="${h}">${l}</button>`).join("")}</div>${S.note ? `<div class="sub" style="margin:2px 0 6px">${esc(S.note)}</div>` : ""}<div id="tbody" class="sub">Loading…</div>`;
  const [d, al] = await Promise.all([
    api(`/api/digest?hours=${S.hours}${S.cats.length ? "&cats=" + S.cats.join(",") : ""}`),
    api(`/api/alerts?vol_x=${S.volx}&px=${S.px}`)]);
  if (!d.items.length && S.hours < 720 && !S.manual) {   // quiet window (weekend / holiday / old snapshot): widen 24h -> 7d -> 30d instead of a blank page
    const next = S.hours < 168 ? 168 : 720, lbl = h => h === 24 ? "24 hours" : h === 72 ? "3 days" : "7 days";
    S.note = `Nothing was filed in the last ${lbl(S.hours)}, so this shows the last ${next === 168 ? "7 days" : "30 days"}.`;
    S.hours = next; return today();
  }
  const c = d.counts, must = d.items.filter(i => i.tier === "must"), look = d.items.filter(i => i.tier === "look"), rt = d.items.filter(i => i.tier === "routine");
  $("#tbody").className = "";
  $("#tbody").innerHTML = `
    <div class="summary"><b>${c.must}</b> need you, <b>${c.look}</b> worth a look, ${c.routine} routine filings tucked away.</div>
    ${d.items.length ? "" : `<div class="sub">Nothing was filed in this window. Try a longer one.</div>`}
    ${al.length ? `<h2>Unusual moves <span class="sub" style="text-transform:none;letter-spacing:0">(vol ≥ ${S.volx}× or ±${S.px}%)</span></h2><div class="strip">${al.map(a => `<div class="alert"><div class="row"><span class="t">${a.ticker}</span><span class="sp"></span>${pct(a.change)}</div>${spark(a.spark, 150, 30, a.change >= 0 ? "var(--up)" : "var(--dn)")}<div class="n">${esc(a.reasons.join(" · "))}</div></div>`).join("")}</div>` : `<h2>Unusual moves</h2><div class="sub">Nothing crossed your thresholds.</div>`}
    ${must.length ? `<h2>Needs you</h2>${must.map(filingCard).join("")}` : ""}
    ${look.length ? `<h2>Worth a look</h2>${look.map(filingCard).join("")}` : ""}
    ${d.reconnect.length ? `<h2>Keep the network warm</h2>${d.reconnect.map(r => `<div class="reconnect"><div>${esc(r.nudge)}</div><div class="sub" style="margin:4px 0 8px">${esc(r.event)}</div><div class="row"><button class="btn" data-act="person" data-id="${r.contact.id}">Open profile</button></div></div>`).join("")}` : ""}
    ${rt.length ? `<details><summary>${rt.length} routine filings (collapsed)</summary>${rt.map(i => `<div class="routine"><b>${i.ticker}</b> <span class="cat">${esc(i.label)}</span> ${esc(i.subject)} <span class="sub">${ago(i.ts)}</span></div>`).join("")}</details>` : ""}`;
}

/* ---------- research sheet ---------- */
async function research(t, pre) {
  openSheet(`<div class="sub">Reading primary sources for ${t}…</div>`);
  let r; try { r = pre || await api("/api/research/" + t); } catch (e) { return openSheet(`<h3>${t}</h3><p class="sub">${esc(e.message)}</p>`); }
  openSheet(researchHTML(r));
}
function researchHTML(r) {
  const s = r.snapshot, t = r.trajectory, b = r.balance_sheet, c = r.cash_quality;
  const sec = (title, body, open) => `<details ${open ? "open" : ""}><summary><b style="color:var(--tx)">${title}</b></summary>${body}</details>`;
  return `<div class="sub">${esc(r.source)} · primary sources only</div><h3 style="font-size:22px;margin-top:2px">${esc(r.name)}</h3>
  <p style="margin:8px 0">${esc(r.verdict)}</p>
  <div class="kv"><div><b>${num(s.revenue_fy)}</b><span>FY revenue ₹ cr</span></div><div><b>${s.ebitda_margin_fy}%</b><span>EBITDA margin</span></div><div><b>${s.roe}%</b><span>ROE</span></div></div>
  ${sec("Narrative vs numbers", `<div class="sub" style="margin:6px 0">Promised on the last four calls vs. what the reported quarters show · <b>${r.scorecard.kept}/${r.scorecard.measured}</b> met or beaten</div>
    <table class="tbl"><tr><th>Call</th><th>Said</th><th>Got</th><th></th></tr>${r.narrative_vs_numbers.map(g => `<tr><td>${g.said_in}<br><span class="sub">${g.metric === "capex" ? "capex" : g.metric === "revenue" ? "rev growth" : "EBITDA mgn"} ${esc(g.horizon)}</span></td><td>${g.metric === "capex" ? "₹" + num(g.low) + " cr" : g.low === g.high ? "~" + g.low + "%" : g.low + "–" + g.high + "%"}</td><td>${g.delivered == null ? "–" : g.delivered + "%"}</td><td><span class="pill ${g.status}">${g.status}</span></td></tr>${g.note ? `<tr><td colspan="4" class="sub" style="text-align:left">${esc(g.note)}</td></tr>` : ""}`).join("")}</table>`, true)}
  ${sec("Bull vs bear", `<div class="bb" style="margin-top:8px"><div class="bu"><b class="up">Bull</b>${r.bull.length ? r.bull.map(x => `<p>${esc(x.point)}<br><small>${esc(x.source)}</small></p>`).join("") : "<p class='sub'>No standout strengths in the primary data.</p>"}</div><div class="be"><b class="dn">Bear</b>${r.bear.length ? r.bear.map(x => `<p>${esc(x.point)}<br><small>${esc(x.source)}</small></p>`).join("") : "<p class='sub'>No major red flags in the primary data.</p>"}</div></div>`, true)}
  ${sec("Business", `<p>${esc(r.business.summary)}</p>${r.business.segments.map(g => `<div class="row" style="margin:4px 0"><span class="sub" style="width:55%">${esc(g.name)}</span><div style="flex:1;background:var(--p2);border-radius:4px;height:8px"><div style="width:${g.pct}%;height:8px;border-radius:4px;background:var(--ac)"></div></div><span class="sub">${g.pct}%</span></div>`).join("")}`)}
  ${sec("Trajectory", `<div class="sub">Revenue ₹ cr, CAGR ${t.revenue_cagr}% · PAT CAGR ${t.pat_cagr}%</div>${bars(t.revenue, t.years)}<div class="sub">EBITDA margin %</div>${bars(t.margin, t.years, 300, 50, v => v + "")}`)}
  ${sec("Balance sheet", `<table class="tbl"><tr><th></th>${b.years.map(y => `<th>${y}</th>`).join("")}</tr><tr><td>Debt ₹ cr</td>${b.debt.map(x => `<td>${num(x)}</td>`).join("")}</tr><tr><td>D/E</td>${b.de.map(x => `<td>${x}</td>`).join("")}</tr><tr><td>WC days</td>${b.wc_days.map(x => `<td>${x}</td>`).join("")}</tr></table>`)}
  ${sec("Cash quality", `<div class="sub">Cumulative CFO / PAT: <b style="color:var(--tx)">${c.cum_cfo_to_pat}×</b> · cumulative FCF ₹${num(c.cum_fcf)} cr</div><table class="tbl"><tr><th></th>${c.years.map(y => `<th>${y}</th>`).join("")}</tr><tr><td>CFO</td>${c.cfo.map(x => `<td>${num(x)}</td>`).join("")}</tr><tr><td>Capex</td>${c.capex.map(x => `<td>${num(x)}</td>`).join("")}</tr><tr><td>CFO/PAT</td>${c.cfo_to_pat.map(x => `<td>${x}</td>`).join("")}</tr></table>`)}
  ${sec("Annual-report flags", r.red_flags.map(f => `<p><b>${esc(f.label)}</b><br><span class="sub">${esc(f.text)}</span></p>`).join(""))}
  ${r.network && r.network.length ? `<h2>Who in your network can help</h2>${r.network.map(p => contactRow(p, esc(p.why))).join("")}` : ""}`;
}

/* ---------- person sheet ---------- */
async function personSheet(id) {
  const d = await api("/api/network/person/" + id), p = d.person;
  openSheet(`<div class="row"><div class="av" style="width:44px;height:44px">${esc(ini(p.name))}</div><div><h3>${esc(p.name)}</h3><div class="sub">${esc(p.role)}, ${esc(p.org)}</div></div></div>
  ${p.met ? `<p class="sub">${esc(p.met)} · strength ${p.strength}/5 · last spoke ${p.last}d ago</p>` : ""}
  <h2>Career</h2>${p.history.slice().reverse().map(h => `<div style="margin:4px 0"><b>${esc(h.org)}</b> <span class="sub">${esc(h.role)} · ${h.start}–${h.end || "now"}</span></div>`).join("")}
  <div class="sub" style="margin-top:6px">${p.edu.map(e => esc(e.school) + " " + e.year).join(" · ")}</div>
  ${p.themes ? `<h2>Knows about</h2><div class="row wrap">${p.themes.map(t => `<span class="chip">${esc(t)}</span>`).join("")}</div>` : ""}
  ${d.overlaps.length || d.second_degree.length ? `<h2>How you're connected</h2>${networkBlock({ overlaps: d.overlaps.slice(0, 3), second_degree: d.second_degree })}` : ""}
  ${d.questions.length ? `<h2>Worth asking</h2>${d.questions.map(q => `<p style="margin:6px 0">• ${esc(q)}</p>`).join("")}` : ""}`);
}

/* ---------- ASK ---------- */
const SUGG = ["What changed today?", "Dig into Asian Paints", "Who should I call about Deepak Nitrite?", "Who is Ananya Rao?", "My go-to people on semiconductors", "Who should I reconnect with?"];
function askView() {
  $("main").classList.add("ask-mode");
  $("#view").innerHTML = `<div class="thread" id="thread">${S.thread.length ? S.thread.map(m => m.html).join("") : `<div class="summary" style="margin-top:24px">Ask FINN anything about your book, a company, or who you know.</div><div class="row wrap">${SUGG.map(q => `<button class="chip" data-act="ask" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>`}</div>
  <div class="askbar"><input id="q" placeholder="Ask FINN..." enterkeyhint="send" autocomplete="off"><button class="go" id="go" aria-label="Send"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 14-7-3 14-4-5-7-2Z"/><path d="m12 14 7-9"/></svg></button></div>`;
  const send = () => { const i = $("#q"); if (i.value.trim()) doAsk(i.value.trim()); };
  $("#go").onclick = send; $("#q").onkeydown = e => { if (e.key === "Enter") send(); };
  const m = $("#view"); m.scrollTop = m.scrollHeight;
}
async function doAsk(q) {
  S.tab !== "ask" && setTab("ask");
  S.thread.push({ html: `<div class="q">${esc(q)}</div>` });
  const thinkingId = `thinking-${Date.now()}`;
  S.thread.push({ id: thinkingId, html: `<div class="thinking"><span>FINN is thinking</span><i></i><i></i><i></i></div>` });
  askView();
  try {
    const r = await api("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ q }) });
    S.thread = S.thread.filter(m => m.id !== thinkingId);
    S.thread.push({ html: `<h2>${esc(r.title)}</h2>` + renderCard(r) });
  } catch (e) {
    S.thread = S.thread.filter(m => m.id !== thinkingId);
    S.thread.push({ html: `<h2>FINN</h2><div class="card"><div>${esc(e.message)}</div></div>` });
  }
  askView();
}
function mdInline(text) {
  let out = esc(text);
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
  out = out.replace(/(^|[^_])_([^_]+)_(?!_)/g, "$1<em>$2</em>");
  return out;
}
function markdown(text) {
  const lines = String(text ?? "").replace(/\r/g, "").split("\n"), out = [];
  let paragraph = [], list = null;
  const close = () => {
    if (paragraph.length) { out.push(`<p>${paragraph.map(mdInline).join("<br>")}</p>`); paragraph = []; }
    if (list) { out.push(`</${list}>`); list = null; }
  };
  for (const line of lines) {
    const heading = line.match(/^#{1,3}\s+(.+)$/);
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (!line.trim()) { close(); continue; }
    if (heading) { close(); out.push(`<h3>${mdInline(heading[1])}</h3>`); continue; }
    if (bullet || numbered) {
      const type = bullet ? "ul" : "ol";
      if (list !== type) { close(); out.push(`<${type}>`); list = type; }
      out.push(`<li>${mdInline((bullet || numbered)[1])}</li>`);
      continue;
    }
    if (list) close();
    paragraph.push(line);
  }
  close();
  return out.join("");
}
function renderCard(r) {
  const d = r.data;
  if (r.card === "text") return `<div class="card answer">${markdown(d.text)}</div>`;
  if (r.card === "people") return d.people.length ? d.people.map(p => contactRow(p, esc(p.why))).join("") : `<div class="sub">Nobody in your network matches. That itself is a finding.</div>`;
  if (r.card === "warm") return d.items.map(w => `<div class="reconnect"><div>${esc(w.nudge)}</div><div class="sub">${esc(w.event)}</div></div>`).join("");
  if (r.card === "digest") return `<div class="summary"><b>${d.counts.must}</b> need you, ${d.counts.look} worth a look.</div>` + d.items.map(filingCard).join("") + `<button class="btn" data-act="tab" data-t="today">Open full digest</button>`;
  if (r.card === "person") { const p = d.person; return `<div class="card"><b>${esc(p.name)}</b> · ${esc(p.role)}, ${esc(p.org)}<br>${p.history.slice().reverse().map(h => `<span class="sub">${esc(h.role)}, ${esc(h.org)} ${h.start}–${h.end || "now"}</span>`).join("<br>")}${networkBlock({ overlaps: d.overlaps.slice(0, 3), second_degree: d.second_degree })}${d.questions.length ? `<div class="why">${d.questions.map(esc).join("<br>")}</div>` : ""}</div>`; }
  if (r.card === "research") return `<div class="card"><p style="margin:0 0 8px">${esc(d.verdict)}</p><div class="sub">Scorecard: ${d.scorecard.kept}/${d.scorecard.measured} guidance calls met</div><div class="row" style="margin-top:10px"><button class="btn pri" data-act="open-research" data-t="${d.ticker}">Open full research</button></div></div>`;
  return "";
}

/* ---------- NETWORK ---------- */
async function networkView() {
  $("main").classList.remove("ask-mode");
  const m = S.meta; S.netTicker = S.netTicker || "DEEPAKNTR";
  $("#view").innerHTML = `<h2>Who can help me understand…</h2><select id="nsel">${m.portfolio.map(c => `<option value="${c.ticker}" ${c.ticker === S.netTicker ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select><div id="graph"></div>
  <h2>Expertise map</h2><div class="row wrap">${m.themes.map(t => `<button class="chip ${S.theme === t ? "on" : ""}" data-act="theme" data-t="${esc(t)}">${esc(t)}</button>`).join("")}</div><div id="exp"></div>`;
  $("#nsel").onchange = e => { S.netTicker = e.target.value; drawGraph(); };
  drawGraph(); if (S.theme) drawExp();
}
async function drawGraph() {
  const g = await api("/api/network/graph/" + S.netTicker), W = 340, H = 300, cx = W / 2, cy = H / 2;
  const n = g.nodes.length, mx = Math.max(...g.nodes.map(x => x.score), 1);
  const pts = g.nodes.map((p, i) => { const a = -Math.PI / 2 + i * 2 * Math.PI / Math.max(n, 1), rad = 70 + (1 - p.score / mx) * 55; return { p, x: cx + Math.cos(a) * rad, y: cy + Math.sin(a) * rad }; });
  $("#graph").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%">${[60, 105, 130].map(r => `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--line)" stroke-dasharray="2 4"/>`).join("")}
    ${pts.map(o => `<line x1="${cx}" y1="${cy}" x2="${o.x}" y2="${o.y}" stroke="var(--ac)" stroke-opacity="${.15 + o.p.strength * .1}" stroke-width="${o.p.strength / 2}"/>`).join("")}
    <circle cx="${cx}" cy="${cy}" r="34" fill="var(--ac)"/><text x="${cx}" y="${cy + 4}" text-anchor="middle" font-size="9" font-weight="700" fill="#15120a">${S.netTicker.slice(0, 9)}</text>
    ${pts.map(o => `<g data-act="person" data-id="${o.p.id}" style="cursor:pointer"><circle cx="${o.x}" cy="${o.y}" r="${10 + o.p.strength * 1.6}" fill="#2b3140" stroke="var(--ac)" stroke-width="1"/><text x="${o.x}" y="${o.y + 4}" text-anchor="middle" font-size="10" font-weight="700" fill="var(--tx)">${esc(ini(o.p.name))}</text><text x="${o.x}" y="${o.y + 26}" text-anchor="middle" font-size="9" fill="var(--mu)">${esc(o.p.name.split(" ")[0])}</text></g>`).join("")}</svg>
    <div class="sub">Closer to centre = better placed to help. Thicker line = stronger relationship.</div>${g.nodes.slice(0, 4).map(p => contactRow(p, esc(p.why))).join("")}`;
}
async function drawExp() { const r = await api("/api/network/expertise?theme=" + encodeURIComponent(S.theme)); $("#exp").innerHTML = r.map(p => contactRow(p, esc(p.why))).join("") || `<div class="sub">No one yet.</div>`; }

/* ---------- settings ---------- */
function settings() {
  const cats = S.meta.categories.filter(c => c !== "unclassified");
  openSheet(`<h3>What counts as unusual</h3>
  <label class="set">Volume above <b id="vx">${S.volx}</b>× its 20-day average</label><input type="range" id="volx" min="1.2" max="5" step="0.1" value="${S.volx}">
  <label class="set">Price move beyond ±<b id="pxv">${S.px}</b>% in a day</label><input type="range" id="px" min="1" max="10" step="0.5" value="${S.px}">
  <label class="set">Filing types I always want to see (must-see items always show)</label>
  <div class="row wrap">${cats.map(c => `<button class="chip ${S.cats.includes(c) ? "on" : ""}" data-act="cat" data-c="${c}">${esc(S.meta.labels[c] || c)}</button>`).join("")}</div>
  <p class="sub" style="margin-top:16px">Settings live only on this device. The server is stateless.</p><button class="btn pri" id="done">Apply</button>`);
  $("#volx").oninput = e => { S.volx = +e.target.value; $("#vx").textContent = S.volx; };
  $("#px").oninput = e => { S.px = +e.target.value; $("#pxv").textContent = S.px; };
  $("#done").onclick = () => { ls.set("volx", S.volx); ls.set("px", S.px); ls.set("cats", S.cats); closeSheet(); render(); };
}

/* ---------- wiring ---------- */
function setTab(t) { S.tab = t; document.querySelectorAll("nav button").forEach(b => b.classList.toggle("on", b.dataset.tab === t)); render(); }
function render() { ({ today, ask: askView, network: networkView })[S.tab](); }
document.addEventListener("click", e => {
  const t = e.target.closest("[data-tab],[data-act]"); if (!t) return;
  if (t.dataset.tab) return setTab(t.dataset.tab);
  const a = t.dataset.act;
  if (a === "hours") { S.note = ""; S.manual = true; S.hours = +t.dataset.h; ls.set("hours", S.hours); today(); }
  else if (a === "research" || a === "open-research") research(t.dataset.t);
  else if (a === "person") personSheet(t.dataset.id);
  else if (a === "ask") { closeSheet(); doAsk(t.dataset.q); }
  else if (a === "tab") setTab(t.dataset.t);
  else if (a === "theme") { S.theme = t.dataset.t; networkView(); }
  else if (a === "cat") { const c = t.dataset.c; S.cats = S.cats.includes(c) ? S.cats.filter(x => x !== c) : [...S.cats, c]; t.classList.toggle("on"); }
});
function applyMode() {
  document.documentElement.dataset.theme = S.mode;
  $("#mode").setAttribute("aria-label", S.mode === "dark" ? "Switch to light mode" : "Switch to dark mode");
}
$("#mode").onclick = () => { S.mode = S.mode === "dark" ? "light" : "dark"; ls.set("mode", S.mode); applyMode(); };
$("#gear").onclick = settings;
applyMode();
(async () => {
  S.meta = await api("/api/meta"); $("#demo").textContent = "Synthetic: " + S.meta.synthetic_layers.join(", ") + (S.meta.live_fundamentals.length ? "" : ", research"); $("#demo").hidden = false;
  render();
  if ("serviceWorker" in navigator) { /* intentionally no SW: always fresh data */ }
})();