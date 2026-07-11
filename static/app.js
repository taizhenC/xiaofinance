/* infinance dashboard — vanilla JS + ECharts (all local, no external requests) */
const BULL = "#26a877", BEAR = "#e05252", NEUT = "#8b949e";
const GRID = "#262d38", INK = "#e6e8eb", INK3 = "#6b7480";
const MONO = 'ui-monospace, "Cascadia Code", Consolas, monospace';

const $ = (s) => document.querySelector(s);
let rankingChart = null;
let donuts = [];
let wasRunning = false;
let pollTimer = null;

/* ---------- formatters ---------- */
function relTime(ageMs) {
  if (ageMs == null) return "—";
  const m = Math.floor(ageMs / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m}分钟前`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}小时前`;
  return `${Math.floor(h / 24)}天前`;
}
function localTime(ms) {
  return ms ? new Date(ms).toLocaleString() : "—";
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok && r.status !== 409) throw new Error(`${path}: ${r.status}`);
  return { status: r.status, data: await r.json().catch(() => null) };
}

/* ---------- status + polling ---------- */
async function loadStatus() {
  const { data: s } = await api("/api/status");
  $("#windowBadge").textContent = `window: last ${s.window_hours}h`;
  $("#lastFetch").textContent = s.last_run
    ? `last fetch: ${localTime(s.last_run.finished_at_ms || s.last_run.started_at_ms)} (${s.last_run.status})`
    : "last fetch: never";
  $("#nextRun").textContent =
    s.scheduler.enabled && s.scheduler.next_run_at_ms
      ? `next auto-run: ${localTime(s.scheduler.next_run_at_ms)}`
      : "";
  $("#loginBanner").hidden = !s.login_required;
  $("#apiKeyBanner").hidden = s.has_api_key;
  $("#fetchBtn").disabled = s.running;
  $("#fetchSpinner").hidden = !s.running;
  $("#fetchBtnLabel").textContent = s.running ? "抓取中…" : "Fetch now";

  if (wasRunning && !s.running) refreshData(); // cycle just finished
  wasRunning = s.running;
  schedulePoll(s.running ? 3000 : 30000);
  return s;
}
function schedulePoll(ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(() => loadStatus().catch(console.error), ms);
}

/* ---------- heat trend (popularity vs previous fetch cycle; amber/gray so
   green/red stay reserved for sentiment) ---------- */
function trendLabel(tr) {
  if (!tr) return "";
  if (tr.dir === "new") return "🔥 新上榜";
  if (tr.dir === "up") return `↑ 升温 +${tr.delta_pct}%`;
  if (tr.dir === "down") return `↓ 降温 ${tr.delta_pct}%`;
  return "";
}
function trendBadge(tr) {
  const label = trendLabel(tr);
  if (!label) return "";
  const cls = tr.dir === "down" ? "badge-cool" : "badge-heat";
  const title = tr.dir === "new" ? "上一抓取周期未出现" : "热度较上一抓取周期的变化";
  return `<span class="badge ${cls}" title="${title}">${label}</span>`;
}

/* ---------- ranking chart ---------- */
function netColor(e) {
  const sc = e.sentiment_counts;
  if (!sc) return NEUT;
  if (sc.bullish > sc.bearish) return BULL;
  if (sc.bearish > sc.bullish) return BEAR;
  return NEUT;
}
function renderRanking(entries) {
  const withData = entries.filter((e) => e.mentions > 0);
  $("#rankingEmpty").hidden = withData.length > 0;
  const el = $("#rankingChart");
  el.style.height = `${Math.max(120, withData.length * 30 + 50)}px`;
  if (!rankingChart) rankingChart = echarts.init(el, null, { renderer: "canvas" });
  const rows = [...withData].sort((a, b) => a.score - b.score); // bottom-up for horizontal bars
  rankingChart.setOption({
    backgroundColor: "transparent",
    grid: { left: 8, right: 30, top: 6, bottom: 22, containLabel: true },
    xAxis: {
      type: "value",
      axisLabel: { color: INK3, fontSize: 11 },
      splitLine: { lineStyle: { color: GRID } },
      axisLine: { show: false },
    },
    yAxis: {
      type: "category",
      data: rows.map((e) => e.ticker),
      axisLabel: { color: INK, fontFamily: MONO, fontSize: 12 },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    series: [{
      type: "bar",
      barWidth: 14,
      data: rows.map((e) => ({
        value: e.score,
        itemStyle: { color: netColor(e), borderRadius: [0, 4, 4, 0] },
      })),
    }],
    tooltip: {
      backgroundColor: "#1b222c", borderColor: GRID, textStyle: { color: INK, fontSize: 12 },
      formatter: (p) => {
        const e = rows[p.dataIndex];
        const sc = e.sentiment_counts;
        const senti = sc ? `▲${sc.bullish} ▼${sc.bearish} —${sc.neutral}` : "无分析";
        const raw = e.note_count_raw > e.note_count || e.comment_count_raw > e.comment_count
          ? `（含转发 ${e.note_count_raw}帖/${e.comment_count_raw}评）` : "";
        const trend = trendLabel(e.trend);
        return `<b style="font-family:${MONO}">${e.ticker}</b> ${esc(e.name_cn)}${trend ? ` · ${trend}` : ""}<br>` +
          `score ${e.score} · ${e.note_count}帖 ${e.comment_count}评 ${raw}<br>${senti}`;
      },
    },
  }, true);
  rankingChart.off("click");
  rankingChart.on("click", (p) => {
    const card = document.getElementById(`card-${rows[p.dataIndex].ticker}`);
    if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

/* ---------- stock cards ---------- */
function sentiLegend(sc) {
  return `<div class="senti-legend">
    <span><i class="dot" style="background:${BULL}"></i>▲ 看多 <b>${sc.bullish}</b></span>
    <span><i class="dot" style="background:${BEAR}"></i>▼ 看空 <b>${sc.bearish}</b></span>
    <span><i class="dot" style="background:${NEUT}"></i>— 中性 <b>${sc.neutral}</b></span>
  </div>`;
}
function cardHtml(e, d) {
  const a = d.analysis;
  const meta = [];
  const tb = trendBadge(e.trend);
  if (tb) meta.push(tb);
  meta.push(`<span class="badge">${e.note_count}帖 · ${e.comment_count}评` +
    (e.note_count_raw > e.note_count ? ` <span title="含转发重复">(raw ${e.note_count_raw}/${e.comment_count_raw})</span>` : "") + `</span>`);
  if (e.latest_item_age_ms != null)
    meta.push(`<span class="badge">最新内容 ${relTime(e.latest_item_age_ms)}</span>`);
  if (a) {
    const stale = a.age_ms > 6 * 3600 * 1000;
    meta.push(`<span class="badge ${stale ? "badge-amber" : ""}">分析于 ${relTime(a.age_ms)}</span>`);
    if (a.status === "ok" && a.irrelevant_item_count != null && a.input_item_count)
      meta.push(`<span class="badge" title="模型判定与该股投资无关的条目">噪音 ${a.irrelevant_item_count}/${a.input_item_count} 已剔除</span>`);
  }

  let body = "";
  if (a && a.status === "ok") {
    body += `<div class="senti-row"><div class="senti-donut" id="donut-${e.ticker}"></div>${sentiLegend(a.sentiment_counts || { bullish: 0, bearish: 0, neutral: 0 })}</div>`;
    body += `<p class="summary">${esc(a.summary)}</p>`;
    if (a.bull_points?.length)
      body += `<ul class="points bull">${a.bull_points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`;
    if (a.bear_points?.length)
      body += `<ul class="points bear">${a.bear_points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`;
    (a.notable_quotes || []).forEach((q) => {
      body += `<blockquote class="quote">${esc(q)}</blockquote>`;
    });
  } else if (a && a.status === "no_api_key") {
    body += `<p class="muted small">未配置 DEEPSEEK_API_KEY，仅显示热门引用：</p>`;
    (a.notable_quotes || []).forEach((q) => {
      body += `<blockquote class="quote">${esc(q)}</blockquote>`;
    });
  } else if (a && a.status === "error") {
    body += `<p class="muted small">最近一次分析失败（${esc(a.error || "unknown")}），显示上一版可用内容为空。</p>`;
  } else if (e.mentions === 0) {
    body += `<p class="muted small">最近24小时没有新内容。</p>`;
  } else {
    body += `<p class="muted small">提及数不足，尚未生成分析。</p>`;
  }

  if (d.items?.length) {
    body += `<details class="items-toggle"><summary>查看 ${d.items.length} 条来源</summary>` +
      d.items.map((i) => {
        const dup = i.cluster_size > 1 ? ` ×${i.cluster_size}相似` : "";
        const link = i.url ? ` <a href="${esc(i.url)}" target="_blank" rel="noopener">原帖</a>` : "";
        return `<div class="item-line"><span class="q-meta">[${i.type === "note" ? "帖" : "评"}] ${relTime(i.age_ms)} 赞${i.likes}${dup}</span>${esc(i.text)}${link}</div>`;
      }).join("") + `</details>`;
  }

  return `<article class="card" id="card-${e.ticker}">
    <div class="card-head">
      <span class="tk">${e.ticker}</span>
      <span class="cn">${esc(e.name_cn)}</span>
      ${e.tracked ? '<span class="pin" title="tracked">📌 tracked</span>' : ""}
    </div>
    <div class="card-meta">${meta.join("")}</div>
    ${body}
  </article>`;
}

function renderDonut(ticker, sc) {
  const el = document.getElementById(`donut-${ticker}`);
  if (!el) return;
  const chart = echarts.init(el, null, { renderer: "canvas" });
  const net = sc.bullish - sc.bearish;
  chart.setOption({
    backgroundColor: "transparent",
    series: [{
      type: "pie",
      radius: ["62%", "85%"],
      itemStyle: { borderColor: "#151b23", borderWidth: 2 },
      label: { show: false },
      silent: true,
      data: [
        { value: sc.bullish, itemStyle: { color: BULL } },
        { value: sc.bearish, itemStyle: { color: BEAR } },
        { value: sc.neutral, itemStyle: { color: NEUT } },
      ],
    }],
    title: {
      text: net > 0 ? `▲${net}` : net < 0 ? `▼${-net}` : "±0",
      left: "center", top: "center",
      textStyle: { color: net > 0 ? BULL : net < 0 ? BEAR : NEUT, fontSize: 15, fontFamily: MONO },
    },
  });
  donuts.push(chart);
}

async function buildCards(entries) {
  donuts.forEach((c) => c.dispose());
  donuts = [];
  const details = await Promise.all(
    entries.map((e) => api(`/api/stocks/${e.ticker}`).then((r) => r.data).catch(() => null))
  );
  $("#cards").innerHTML = entries
    .map((e, i) => (details[i] ? cardHtml(e, details[i]) : ""))
    .join("");
  entries.forEach((e, i) => {
    const a = details[i]?.analysis;
    if (a?.status === "ok" && a.sentiment_counts) renderDonut(e.ticker, a.sentiment_counts);
  });
}

/* ---------- radar / tracked / suggestions / runs ---------- */
function renderRadar(radar) {
  $("#radarPanel").hidden = radar.length === 0;
  $("#radarStrip").innerHTML = radar.map((r) =>
    `<span class="chip" title="${esc(r.top_quote || "")}">
       ${r.trend?.dir === "new" ? "🔥 " : ""}<span class="tk">${r.ticker}</span> ${esc(r.name_cn)} ·${r.mentions}
       <button data-track="${r.ticker}" title="加入跟踪">＋跟踪</button>
     </span>`).join("");
}
async function loadTracked() {
  const { data } = await api("/api/tracked");
  $("#trackedList").innerHTML = data.length
    ? data.map((t) =>
        `<span class="chip"><span class="tk">${t.ticker}</span>${
          t.custom_keywords.length ? " " + esc(t.custom_keywords.join(", ")) : ""
        }<button data-untrack="${t.ticker}" title="移除">✕</button></span>`).join("")
    : '<span class="muted small">暂无跟踪 — 加一个试试，比如 COST</span>';
}
async function loadSuggestions() {
  const { data } = await api("/api/alias_suggestions");
  $("#suggCount").textContent = data.length || "";
  $("#suggestionsList").innerHTML = data.length
    ? data.map((s) =>
        `<div class="sugg-row">
           <span class="term">${esc(s.term)}</span> →
           <span class="tk">${esc(s.guessed_ticker)}</span>
           <button class="btn btn-mini" data-accept="${s.id}">接受</button>
           <button class="btn btn-mini" data-reject="${s.id}">拒绝</button>
           <span class="evi">“${esc(s.evidence_quote || "")}”</span>
         </div>`).join("")
    : '<p class="muted small">暂无待审核的别名建议（黑话扫描每N个周期运行一次）。</p>';
}
async function loadRuns() {
  const { data } = await api("/api/runs?limit=20");
  $("#runsTable tbody").innerHTML = data.map((r) => {
    const dur = r.finished_at_ms ? `${Math.round((r.finished_at_ms - r.started_at_ms) / 60000)}min` : "—";
    return `<tr>
      <td>${r.id}</td><td>${r.mode}</td>
      <td class="st-${r.status}">${r.status}</td>
      <td>${r.notes_fresh}帖/${r.comments_fresh}评</td>
      <td>${dur}</td><td>${localTime(r.started_at_ms)}</td>
      <td class="err" title="${esc(r.error || "")}">${esc(r.error || "")}</td>
    </tr>`;
  }).join("");
}

/* ---------- actions ---------- */
document.addEventListener("click", async (ev) => {
  const t = ev.target;
  if (t.dataset.track) {
    await api("/api/tracked", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ticker: t.dataset.track }) });
    refreshData();
  } else if (t.dataset.untrack) {
    await api(`/api/tracked/${t.dataset.untrack}`, { method: "DELETE" });
    refreshData();
  } else if (t.dataset.accept || t.dataset.reject) {
    const id = t.dataset.accept || t.dataset.reject;
    await api(`/api/alias_suggestions/${id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: t.dataset.accept ? "accept" : "reject" }) });
    loadSuggestions();
  }
});
$("#trackForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const ticker = $("#trackTicker").value.trim().toUpperCase();
  const kws = $("#trackKeywords").value.trim();
  await api("/api/tracked", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ticker, custom_keywords: kws }) });
  $("#trackTicker").value = ""; $("#trackKeywords").value = "";
  refreshData();
});
$("#fetchBtn").addEventListener("click", async () => {
  const { status } = await api("/api/fetch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "both" }) });
  if (status === 202 || status === 409) loadStatus();
});

/* ---------- boot ---------- */
async function refreshData() {
  const { data } = await api("/api/ranking");
  renderRanking(data.ranking);
  renderRadar(data.radar);
  await buildCards(data.ranking);
  loadTracked(); loadSuggestions(); loadRuns();
}
window.addEventListener("resize", () => rankingChart?.resize());
loadStatus().then(refreshData).catch(console.error);
