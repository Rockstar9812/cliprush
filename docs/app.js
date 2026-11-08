// ClipRush web app - works on iPhone (add to Home Screen) and laptop (Edge). No tracking, no third-party code.
import { decryptBundle } from "./crypto.js";

const LS = {
  get: (k, d = null) => { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
  del: (k) => { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

let DATA = null;
let PASSWORD = null;
let TAB = "batches";
let FILTER = "all";
let FB = LS.get("cr_feedback", { clips: {} });
const EDITS = LS.get("cr_edits", {});

// ------------------------------------------------------------------ repo + fetch
function repo() {
  const q = new URLSearchParams(location.search).get("repo");
  if (q) { LS.set("cr_repo", q); return q; }
  const saved = LS.get("cr_repo");
  if (saved) return saved;
  const m = location.hostname.match(/^([^.]+)\.github\.io$/);
  const seg = location.pathname.split("/").filter(Boolean)[0];
  return m && seg ? `${m[1]}/${seg}` : "";
}
const token = () => LS.get("cr_token", "");

async function fetchFile(name, ref) {
  const r = repo();
  if (r === "local") { // offline demo / testing: files next to the app in ./local/
    const res = await fetch(`./local/${ref === "data" ? "" : ref + "-"}${name}?t=${Date.now()}`, { cache: "no-store" });
    return res.ok ? res.text() : null;
  }
  if (!r) throw new Error("Set your repository (yourname/cliprush) on the unlock screen.");
  const h = { Accept: "application/vnd.github.raw+json" };
  if (token()) h.Authorization = `Bearer ${token()}`;
  try {
    const res = await fetch(`https://api.github.com/repos/${r}/contents/${name}?ref=${ref}&t=${Date.now()}`, { headers: h, cache: "no-store" });
    if (res.ok) return await res.text();
    if (res.status === 404) return null;
  } catch { /* fall through */ }
  const raw = await fetch(`https://raw.githubusercontent.com/${r}/${ref}/${name}?t=${Date.now()}`, { cache: "no-store" });
  if (raw.status === 404) return null;
  if (!raw.ok) throw new Error(`Could not load data (${raw.status})`);
  return raw.text();
}

async function load(pw, { quiet = false } = {}) {
  let txt = null;
  try {
    txt = await fetchFile("app.enc", "data");
    if (txt) LS.set("cr_cache", txt);
  } catch (e) {
    txt = LS.get("cr_cache");
    if (!txt) throw e;
    if (!quiet) toast("Offline - showing last saved clips");
  }
  if (!txt) throw new Error("No data yet. The cloud brain has not finished its first run (check GitHub Actions).");
  DATA = await decryptBundle(JSON.parse(txt), pw);
  await syncFeedback(false);
  render();
}

// ------------------------------------------------------------------ feedback (approve / posted / skip)
async function syncFeedback(push) {
  try {
    const remote = JSON.parse((await fetchFile("feedback.json", "feedback")) || "{}");
    for (const [id, v] of Object.entries(remote.clips || {})) {
      const mine = FB.clips[id];
      if (!mine || (v.at || "") > (mine.at || "")) FB.clips[id] = v;
    }
    LS.set("cr_feedback", FB);
  } catch { /* remote optional */ }
  if (push && token()) await pushFeedback();
}

async function pushFeedback() {
  const r = repo();
  const h = { Authorization: `Bearer ${token()}`, Accept: "application/vnd.github+json" };
  const api = `https://api.github.com/repos/${r}`;
  try {
    let ref = await fetch(`${api}/git/ref/heads/feedback`, { headers: h });
    if (ref.status === 404) {
      const repoInfo = await (await fetch(api, { headers: h })).json();
      const main = await (await fetch(`${api}/git/ref/heads/${repoInfo.default_branch}`, { headers: h })).json();
      await fetch(`${api}/git/refs`, { method: "POST", headers: h, body: JSON.stringify({ ref: "refs/heads/feedback", sha: main.object.sha }) });
    }
    const cur = await fetch(`${api}/contents/feedback.json?ref=feedback`, { headers: h });
    const sha = cur.ok ? (await cur.json()).sha : undefined;
    const body = { message: "ClipRush feedback", branch: "feedback",
      content: btoa(unescape(encodeURIComponent(JSON.stringify(FB)))), ...(sha ? { sha } : {}) };
    const put = await fetch(`${api}/contents/feedback.json`, { method: "PUT", headers: h, body: JSON.stringify(body) });
    if (!put.ok) throw new Error(put.status);
  } catch (e) {
    toast("Saved on this device (sync failed - check token in Health > Settings)");
  }
}

function setStatus(id, status, extra = {}) {
  FB.clips[id] = { ...(FB.clips[id] || {}), status, at: new Date().toISOString(), ...extra };
  LS.set("cr_feedback", FB);
  render();
  if (token()) pushFeedback();
}

// ------------------------------------------------------------------ helpers
const clipById = (id) => (DATA?.clips || []).find((c) => c.id === id);
const statusOf = (c) => FB.clips[c.id]?.status || c.status || "";
const capOf = (c) => EDITS[c.id] ?? c.caption;
function ago(iso) {
  if (!iso) return "";
  const m = Math.max(0, (Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 60) return `${Math.round(m)}m ago`;
  if (m < 48 * 60) return `${Math.round(m / 60)}h ago`;
  return `${Math.round(m / 1440)}d ago`;
}
const fmtNum = (n) => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : String(n || 0);
const rankCls = (s) => s >= 75 ? "r-hi" : s >= 58 ? "r-mid" : "r-lo";
const CAT = { celebrity: "Celebrity", comedy: "Comedy", relationships: "Relationships", athlete: "Sports", nba: "NBA", adult: "Adult-star", watchlist: "Watchlist" };

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 2600);
}

// ------------------------------------------------------------------ rendering
function clipCard(c, n) {
  const st = statusOf(c);
  const cap = capOf(c);
  const wc = cap.trim().split(/\s+/).filter(Boolean).length;
  const reasons = (c.reasons || []).slice(0, 4).map((r) => `<span class="chip ${/comment|replayed/i.test(r) ? "hot" : ""}">${esc(r)}</span>`).join("");
  return `<article class="card" data-id="${c.id}">
    <div class="thumb" data-act="preview" style="background-image:url('${esc(c.thumb)}')">
      <div class="play">▶</div>
      <span class="tag">${n ? `#${n} · ` : ""}${esc(CAT[c.cat] || c.cat || "")}</span>
      <span class="rank ${rankCls(c.score)}">${Math.round(c.score)}</span>
      <span class="dur">${Math.round(c.duration)}s · at ${esc(c.at)}</span>
    </div>
    <div class="body">
      <div class="src"><b>${esc(c.guest || "")}</b><span>${esc(c.show)}</span><span>· ${ago(c.published || c.found)}</span>
        ${st ? `<span class="status st-${st}">${st}</span>` : ""}</div>
      <textarea class="cap" data-act="edit" rows="2">${esc(cap)}</textarea>
      <div class="wc ${wc < 8 || wc > 12 ? "bad" : ""}">${wc} words ${wc < 8 || wc > 12 ? "(best: 8-12)" : ""}</div>
      ${c.why ? `<div class="why">AI: ${esc(c.why)}</div>` : ""}
      <div class="chips">${reasons}</div>
      <div class="actions">
        <button class="btn" data-act="preview">Preview</button>
        <button class="btn" data-act="copy">Copy</button>
        <button class="btn ${st === "approved" ? "good" : "primary"}" data-act="approve">${st === "approved" ? "Approved" : "Approve"}</button>
        <button class="btn" data-act="more">More</button>
      </div>
    </div>
  </article>`;
}

function viewBatches() {
  const b = DATA.batches || [];
  if (!b.length) return `<div class="empty">No batches configured.</div>`;
  return b.map((batch) => {
    const clips = batch.clips.map(clipById).filter(Boolean);
    const prep = new Date(batch.prep_at);
    const ready = Date.now() >= prep.getTime();
    return `<section>
      <div class="batch-head"><h3>${esc(batch.name)} batch · ${esc(batch.time_ist)} IST</h3>
        <div class="meta">${clips.length} clips · ${ready ? "ready to post" : `best picks so far (final list by ${prep.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })})`}</div></div>
      ${clips.length ? `<div class="grid" style="margin-top:12px">${clips.map((c, i) => clipCard(c, i + 1)).join("")}</div>`
        : `<div class="empty">Nothing strong enough yet. New episodes are checked every 5 minutes.</div>`}
    </section>`;
  }).join("");
}

function viewClips() {
  const cats = ["all", ...new Set((DATA.clips || []).map((c) => c.cat).filter(Boolean))];
  let clips = (DATA.clips || []).slice();
  if (FILTER === "approved" || FILTER === "posted") clips = clips.filter((c) => statusOf(c) === FILTER);
  else if (FILTER !== "all") clips = clips.filter((c) => c.cat === FILTER);
  clips = clips.filter((c) => FILTER === "posted" || statusOf(c) !== "skipped");
  const fbtn = (k, label) => `<button class="btn ${FILTER === k ? "on" : ""}" data-filter="${k}">${esc(label)}</button>`;
  return `<div class="filters">${cats.map((k) => fbtn(k, k === "all" ? "All" : CAT[k] || k)).join("")}${fbtn("approved", "Approved")}${fbtn("posted", "Posted")}</div>
    <div class="section-title"><h2>${clips.length} clips</h2><span class="muted small">sorted by score + episode heat</span></div>
    ${clips.length ? `<div class="grid">${clips.map((c) => clipCard(c)).join("")}</div>` : `<div class="empty">No clips here yet.</div>`}`;
}

function viewEpisodes() {
  const label = { done: "analysed", needs_local: "waiting for laptop", no_transcript: "captions not ready", new: "queued", skipped: "skipped", info: "reading" };
  const eps = DATA.episodes || [];
  return `<div class="section-title"><h2>New episodes</h2><span class="muted small">last 7 days</span></div>
    <div class="list">${eps.map((e) => `<a class="row" href="https://youtu.be/${esc(e.id)}" target="_blank" rel="noopener" style="text-decoration:none;color:inherit">
      <div class="grow"><div class="t">${esc(e.title)}</div>
      <div class="s">${esc(e.show)}${e.guest ? ` · ${esc(e.guest)}` : ""} · ${fmtNum(e.views)} views · ${e.comments_n ?? 0} comments read · ${ago(e.published || e.found)}${e.skip_reason ? ` · ${esc(e.skip_reason)}` : ""}</div></div>
      <span class="stage ${esc(e.stage)}">${esc(label[e.stage] || e.stage)}</span></a>`).join("") || `<div class="empty">No new episodes in the last 48 hours yet.</div>`}</div>`;
}

function viewInsights() {
  const ins = DATA.insights || {};
  const max = Math.max(1.0001, ...(ins.people || []).map((p) => p[1]));
  const bars = (arr) => arr.map(([k, v]) => `<div>${esc(k)}</div><div class="bar"><i style="width:${Math.min(100, (v / max) * 100)}%"></i></div><div>${v.toFixed(2)}x</div>`).join("");
  return `<div class="notice ok">Learned from ${ins.n_posts || 0} real X clips (views compared within each account, so a big account does not skew it).</div>
    <div class="panel"><h3>What is working on X right now</h3>${(ins.lines || []).map((l) => `<p style="margin:6px 0">• ${esc(l)}</p>`).join("") || "<p class='muted'>Collecting data...</p>"}</div>
    ${ins.trending?.length ? `<div class="panel"><h3>Trending people (searched first)</h3><div class="chips">${ins.trending.map((t) => `<span class="chip hot">${esc(t)}</span>`).join("")}</div></div>` : ""}
    ${ins.people?.length ? `<div class="panel"><h3>People who beat their account's normal views</h3><div class="bars">${bars(ins.people)}</div></div>` : ""}
    ${ins.topics?.length ? `<div class="panel"><h3>Topics that lift views</h3><div class="bars">${bars(ins.topics)}</div></div>` : ""}
    ${ins.examples?.length ? `<div class="panel"><h3>Real captions from top clips (the AI copies this style)</h3>${ins.examples.map((e) => `<p style="margin:6px 0">“${esc(e)}”</p>`).join("")}</div>` : ""}
    ${Object.keys(ins.accounts || {}).length ? `<div class="panel"><h3>Accounts studied</h3><div class="kv">${Object.entries(ins.accounts).map(([a, v]) => `<div class="k">@${esc(a)} (${v.posts} clips)</div><div>${fmtNum(v.median_views)} typical</div>`).join("")}</div></div>` : ""}`;
}

function viewHealth() {
  const h = DATA.health || {};
  const xs = h.x_study || {};
  const ai = h.ai || {};
  const notices = [];
  if (h.youtube_blocked || h.pending_for_laptop) notices.push(`<div class="notice warn">YouTube is refusing GitHub's cloud right now. ${h.pending_for_laptop || 0} episode(s) are waiting - turn on your laptop and they finish automatically.</div>`);
  if (h.sources_found < h.sources_total) notices.push(`<div class="notice warn">${h.sources_total - h.sources_found} podcast channel(s) not found yet - fix their handle in config/sources.yaml (list below).</div>`);
  const ageMin = (Date.now() - new Date(DATA.generated).getTime()) / 60000;
  if (ageMin > 30) notices.push(`<div class="notice bad">Last update was ${ago(DATA.generated)}. Check GitHub > Actions for errors.</div>`);
  const missing = (DATA.sources || []).filter((s) => !s.ok);
  return `${notices.join("") || `<div class="notice ok">Everything is running.</div>`}
    <div class="panel"><h3>Status</h3><div class="kv">
      <div class="k">Last update</div><div>${ago(DATA.generated)} (${esc(h.mode)})</div>
      <div class="k">Podcast channels found</div><div>${h.sources_found}/${h.sources_total}</div>
      <div class="k">AI captions</div><div>${ai.available ? `on · ${fmtNum(ai.tokens_today)} tokens today` : "rules only (free AI key not set or quota used)"}</div>
      <div class="k">X study</div><div>${fmtNum(xs.tracked || 0)} clips tracked · ${ago(xs.last_run) || "not run yet"}</div>
      <div class="k">Apify backup</div><div>${esc(xs.apify || "not set")}</div>
    </div></div>
    ${missing.length ? `<div class="panel"><h3>Channels to fix</h3>${missing.map((s) => `<p style="margin:4px 0">• ${esc(s.name)}</p>`).join("")}</div>` : ""}
    <div class="panel"><h3>X sources</h3><div class="kv">${Object.entries(xs.sources || {}).map(([k, v]) => `<div class="k">${esc(k)}</div><div>${esc(v)}</div>`).join("") || "<div class='muted'>not run yet</div>"}</div></div>
    <div class="panel"><h3>Activity</h3><div class="log">${esc((h.events || []).slice().reverse().join("\n")) || "No events yet."}</div></div>
    <div class="panel"><h3>Settings (this device)</h3>
      <label class="small muted">Repository</label><input id="setRepo" value="${esc(repo())}" placeholder="yourname/cliprush" />
      <label class="small muted">Sync token (optional - syncs Approve/Posted between phone and laptop)</label><input id="setToken" type="password" value="${esc(token())}" placeholder="github_pat_..." />
      <label class="small muted">iPhone Shortcut name</label><input id="setShortcut" value="${esc(LS.get("cr_shortcut", "ClipRush Cut"))}" />
      <div class="actions" style="grid-template-columns:1fr 1fr"><button class="btn primary" data-act="save-settings">Save</button><button class="btn" data-act="lock">Lock & forget password</button></div>
    </div>`;
}

function render() {
  if (!DATA) return;
  const h = DATA.health || {};
  const pill = $("#statusPill");
  const ageMin = (Date.now() - new Date(DATA.generated).getTime()) / 60000;
  pill.className = "pill " + (ageMin > 30 ? "bad" : h.youtube_blocked || h.pending_for_laptop ? "warn" : "ok");
  pill.textContent = `${ago(DATA.generated)}${h.pending_for_laptop ? ` · ${h.pending_for_laptop} need laptop` : ""}`;
  const v = { batches: viewBatches, clips: viewClips, episodes: viewEpisodes, insights: viewInsights, health: viewHealth }[TAB];
  $("#view").innerHTML = v();
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === TAB));
}

// ------------------------------------------------------------------ actions
function preview(c) {
  const s = Math.max(0, Math.floor(c.start)); const e = Math.ceil(c.end);
  const src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(c.video_id)}?start=${s}&end=${e}&autoplay=1&playsinline=1&rel=0&modestbranding=1`;
  $("#modalBody").innerHTML = `<iframe class="player" src="${src}" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe>
    <h3>${esc(capOf(c))}</h3>
    <p class="muted small" style="margin:0 4px 8px">${esc(c.show)} · ${esc(c.episode)}</p>
    <p class="small" style="margin:0 4px 10px;color:#c9d0db">“${esc((c.quote || "").slice(0, 400))}”</p>
    <div class="actions"><button class="btn" data-act="replay" data-id="${c.id}">Replay</button>
      <a class="btn" style="text-align:center;text-decoration:none" href="${esc(c.url)}" target="_blank" rel="noopener">YouTube</a>
      <button class="btn" data-act="copy" data-id="${c.id}">Copy</button>
      <button class="btn primary" data-act="approve" data-id="${c.id}">Approve</button></div>`;
  $("#modal").classList.remove("hidden");
}

function approve(c) {
  setStatus(c.id, "approved");
  if (isIOS) {
    const name = `${(c.guest || c.show || "clip").replace(/[^A-Za-z0-9]+/g, "_").slice(0, 24)}_${c.id.slice(0, 6)}`;
    const input = `${c.video_id} ${c.start} ${c.end} ${name}`;
    const sc = LS.get("cr_shortcut", "ClipRush Cut");
    navigator.clipboard?.writeText(capOf(c)).catch(() => {});
    location.href = `shortcuts://run-shortcut?name=${encodeURIComponent(sc)}&input=text&text=${encodeURIComponent(input)}`;
    toast("Caption copied · cutting on your phone");
  } else {
    toast("Approved - your laptop cuts approved clips first");
  }
}

async function copyCaption(c) {
  try { await navigator.clipboard.writeText(capOf(c)); toast("Caption copied"); }
  catch { toast("Copy blocked - long-press the caption to copy"); }
}

function more(c) {
  $("#modalBody").innerHTML = `<h3>${esc(capOf(c))}</h3>
    <div class="kv" style="margin:10px 4px">
      <div class="k">Score</div><div>${c.score} (rules ${c.rule_score}${c.ai ? `, AI viral ${c.ai.viral}/10, stands alone ${c.ai.standalone}/10` : ""})</div>
      <div class="k">Episode heat</div><div>${c.hot}</div><div class="k">Hook (on video)</div><div>${esc(c.hook)}</div>
      <div class="k">Moment</div><div>${esc(c.at)} · ${Math.round(c.duration)}s</div></div>
    <div class="chips" style="margin:0 4px 12px">${(c.reasons || []).map((r) => `<span class="chip">${esc(r)}</span>`).join("")}</div>
    <div class="actions" style="grid-template-columns:repeat(3,1fr)">
      <button class="btn good" data-act="posted" data-id="${c.id}">Mark posted</button>
      <button class="btn" data-act="skip" data-id="${c.id}">Skip</button>
      <button class="btn" data-act="reset" data-id="${c.id}">Reset</button></div>`;
  $("#modal").classList.remove("hidden");
}

document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-act],[data-tab],[data-filter]");
  if (!t) return;
  if (t.dataset.tab) { TAB = t.dataset.tab; window.scrollTo(0, 0); render(); return; }
  if (t.dataset.filter) { FILTER = t.dataset.filter; render(); return; }
  const id = t.dataset.id || t.closest("[data-id]")?.dataset.id;
  const c = id && clipById(id);
  const act = t.dataset.act;
  if (act === "edit") return;
  if (act === "preview" && c) preview(c);
  else if (act === "replay" && c) preview(c);
  else if (act === "copy" && c) copyCaption(c);
  else if (act === "approve" && c) approve(c);
  else if (act === "more" && c) more(c);
  else if (act === "posted" && c) {
    const link = prompt("Paste your X post link (optional - lets ClipRush learn from your own results):", "") || "";
    setStatus(c.id, "posted", { link: link.trim() }); closeModal(); toast("Marked as posted");
  } else if (act === "skip" && c) { setStatus(c.id, "skipped"); closeModal(); }
  else if (act === "reset" && c) { setStatus(c.id, ""); closeModal(); }
  else if (act === "save-settings") {
    LS.set("cr_repo", $("#setRepo").value.trim()); LS.set("cr_token", $("#setToken").value.trim());
    LS.set("cr_shortcut", $("#setShortcut").value.trim() || "ClipRush Cut"); toast("Saved"); refresh();
  } else if (act === "lock") { LS.del("cr_pw"); PASSWORD = null; DATA = null; showLock(); }
});

document.addEventListener("input", (ev) => {
  const t = ev.target;
  if (t.dataset.act !== "edit") return;
  const id = t.closest("[data-id]").dataset.id;
  EDITS[id] = t.value; LS.set("cr_edits", EDITS);
  const wc = t.value.trim().split(/\s+/).filter(Boolean).length;
  const el = t.parentElement.querySelector(".wc");
  el.textContent = `${wc} words ${wc < 8 || wc > 12 ? "(best: 8-12)" : ""}`; el.classList.toggle("bad", wc < 8 || wc > 12);
});

function closeModal() { $("#modal").classList.add("hidden"); $("#modalBody").innerHTML = ""; }
$("#modalClose").onclick = closeModal;
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
$("#statusPill").onclick = () => { TAB = "health"; render(); };

async function refresh() {
  if (!PASSWORD) return;
  $("#refreshBtn").textContent = "…";
  try { await load(PASSWORD, { quiet: true }); } catch (e) { toast(e.message); }
  $("#refreshBtn").textContent = "⟳";
}
$("#refreshBtn").onclick = refresh;
setInterval(() => { if (document.visibilityState === "visible") refresh(); }, 5 * 60 * 1000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh(); });

// ------------------------------------------------------------------ unlock
function showLock(msg = "") {
  $("#lock").classList.remove("hidden");
  $("#repoInput").value = repo();
  $("#lockErr").textContent = msg;
  if (!repo()) $("#lock details").open = true;
}

$("#lockForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const pw = $("#pw").value;
  const r = $("#repoInput").value.trim();
  if (r) LS.set("cr_repo", r);
  $("#lockErr").textContent = "Unlocking…";
  try {
    await load(pw);
    PASSWORD = pw;
    if ($("#remember").checked) LS.set("cr_pw", pw);
    $("#lock").classList.add("hidden");
  } catch (e) {
    $("#lockErr").textContent = e.name === "OperationError" ? "Wrong password." : e.message;
  }
});

(async function start() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  const saved = LS.get("cr_pw");
  if (!saved) return showLock();
  try { await load(saved); PASSWORD = saved; } catch (e) { showLock(e.name === "OperationError" ? "Password changed - enter it again." : e.message); }
})();
