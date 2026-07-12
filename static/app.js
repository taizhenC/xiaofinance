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
const PHASE_CN = {
  search: "搜索帖子", note_details: "抓帖子详情", comments: "抓评论",
  login: "等待登录", starting: "启动中",
};

// The crawl throttles itself to one request per 8s on purpose (account risk), so raw
// counts move slowly and a keyword's note count freezes entirely while its comments
// fetch. Estimate within-keyword progress as 35% notes + 65% comment coverage, and show
// a heartbeat off the log file so "slow by design" stays distinguishable from "hung".
function crawlFraction(p) {
  const total = p.keyword_total || 1;
  const kIdx = p.keyword_index || 0;
  if (!kIdx) return 0;
  const cur = (p.per_keyword || [])[kIdx - 1];
  const notes = cur ? cur.notes : 0;
  const noteFrac = Math.min(1, notes / (p.target_per_keyword || 20));
  const cmtFrac = notes ? Math.min(1, (p.kw_comment_notes_done || 0) / notes) : 0;
  return Math.min(1, (kIdx - 1 + 0.35 * noteFrac + 0.65 * cmtFrac) / total);
}
function renderCrawlProgress(s, p) {
  const kIdx = p.keyword_index || 0;
  const pct = Math.round(crawlFraction(p) * 100);
  const mins = s.last_run.started_at_ms ? Math.round((s.now_ms - s.last_run.started_at_ms) / 60000) : null;
  const kw = p.keyword ? ` · ${kIdx || "?"}/${p.keyword_total}「${esc(p.keyword)}」` : "";
  const phase = PHASE_CN[p.phase] ? ` · ${PHASE_CN[p.phase]}` : "";
  const idle = p.last_activity_ms != null ? Math.max(0, s.now_ms - p.last_activity_ms) : null;
  const heart = idle == null ? ""
    : idle > 90000 ? ` · <b class="warn">无活动 ${Math.round(idle / 60000)}分钟</b>`
    : ` · <span class="pulse-dot"></span>${idle < 8000 ? "刚刚" : Math.round(idle / 1000) + "秒前"}`;
  const risk = p.captchas ? ` · <b class="warn">⚠ 风控验证码×${p.captchas}</b>` : "";
  $("#lastFetch").innerHTML =
    `抓取中 ${pct}%${mins != null ? `（已${mins}分）` : ""} · ${p.notes}帖/${p.comments}评${kw}${phase}${heart}${risk}`;

  const bar = $("#crawlBar");
  bar.hidden = false;
  bar.innerHTML = (p.per_keyword || []).map((k, i) => {
    const n = i + 1;
    const fill = kIdx && n < kIdx ? 100
      : n === kIdx ? Math.round((0.35 * Math.min(1, k.notes / (p.target_per_keyword || 20))
        + 0.65 * (k.notes ? Math.min(1, (p.kw_comment_notes_done || 0) / k.notes) : 0)) * 100)
      : 0;
    return `<span class="crawl-seg${n === kIdx ? " active" : ""}" title="${esc(k.keyword)}: ${k.notes}帖/${k.comments}评"><i style="width:${fill}%"></i></span>`;
  }).join("");
}

async function loadStatus() {
  const { data: s } = await api("/api/status");
  $("#windowBadge").textContent = `window: last ${s.window_hours}h`;
  const p = s.running && s.last_run ? s.last_run.progress : null;
  if (p) {
    renderCrawlProgress(s, p);
  } else {
    $("#crawlBar").hidden = true;
    $("#lastFetch").textContent = s.last_run
      ? `last fetch: ${localTime(s.last_run.finished_at_ms || s.last_run.started_at_ms)} (${s.last_run.status})`
      : "last fetch: never";
  }
  $("#nextRun").textContent =
    s.scheduler.enabled && s.scheduler.next_run_at_ms
      ? `next auto-run: ${localTime(s.scheduler.next_run_at_ms)}`
      : "";
  $("#loginBanner").hidden = !s.login_required;
  $("#apiKeyBanner").hidden = s.has_api_key;
  $("#fetchBtn").disabled = s.running;
  $("#fetchSpinner").hidden = !s.running;
  $("#fetchBtnLabel").textContent = s.running ? "抓取中…" : "Fetch now";

  if (s.running) loadRuns().catch(console.error);
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
  if (tr.dir === "up") return tr.delta_pct == null ? "↑ 升温" : `↑ 升温 +${tr.delta_pct}%`;
  if (tr.dir === "down") return tr.delta_pct == null ? "↓ 降温" : `↓ 降温 ${tr.delta_pct}%`;
  return "";
}
function trendBadge(tr) {
  const label = trendLabel(tr);
  if (!label) return "";
  const cls = tr.dir === "down" ? "badge-cool" : "badge-heat";
  const title = tr.dir === "new" ? "上一抓取周期未出现" : "热度较上一抓取周期的变化";
  return `<span class="badge ${cls}" title="${title}">${label}</span>`;
}

/* ---------- price reality check (Stooq daily closes) ---------- */
function quoteText(q) {
  if (!q || q.price == null) return "";
  if (q.change_pct == null) return `$${q.price}`;
  const sign = q.change_pct > 0 ? "+" : "";
  return `$${q.price} (${sign}${q.change_pct}%)`;
}
function quoteBadge(e) {
  const q = e.quote;
  if (!q || q.change_pct == null) return "";
  const col = q.change_pct > 0 ? BULL : q.change_pct < 0 ? BEAR : NEUT;
  const sign = q.change_pct > 0 ? "+" : "";
  return `<span class="badge" title="最近交易日收盘 vs 前一交易日（${esc(q.market_date)}，Yahoo 免费数据，非实时）">股价 $${q.price} <b style="color:${col}">${sign}${q.change_pct}%</b></span>`;
}
function divergenceBadge(e) {
  if (!e.divergence) return "";
  return `<span class="badge badge-amber" title="小红书舆论倾向与最近股价方向相反 — 可能已被定价，或情绪滞后/领先">🔀 舆论与股价背离</span>`;
}

function sparklineSvg(hist) {
  if (!hist || hist.length < 3) return "";
  const w = 130, h = 26, pad = 3;
  const max = Math.max(...hist.map((p) => p.score), 1);
  const pts = hist.map((p, i) => {
    const x = pad + (i * (w - 2 * pad)) / (hist.length - 1);
    const y = h - pad - (p.score / max) * (h - 2 * pad);
    return [x.toFixed(1), y.toFixed(1)];
  });
  const [lx, ly] = pts[pts.length - 1];
  const scores = hist.map((p) => p.score);
  const title = `热度走势（近${hist.length}个抓取周期）min ${Math.min(...scores)} → max ${Math.max(...scores)}`;
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}"><title>${esc(title)}</title>
    <polyline points="${pts.map((p) => p.join(",")).join(" ")}" fill="none" stroke="#4f8cd9" stroke-width="1.5" stroke-linejoin="round"/>
    <circle cx="${lx}" cy="${ly}" r="2.2" fill="#4f8cd9"/></svg>`;
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
        const px = quoteText(e.quote);
        return `<b style="font-family:${MONO}">${e.ticker}</b> ${esc(e.name_cn)}${trend ? ` · ${trend}` : ""}${px ? ` · ${px}` : ""}<br>` +
          `score ${e.score} · ${e.note_count}帖 ${e.comment_count}评 ${raw}<br>${senti}${e.divergence ? "<br>🔀 舆论与股价背离" : ""}`;
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
  const qb = quoteBadge(e);
  if (qb) meta.push(qb);
  const db = divergenceBadge(e);
  if (db) meta.push(db);
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

  const spark = sparklineSvg(e.history);
  return `<article class="card" id="card-${e.ticker}">
    <div class="card-head">
      <span class="tk">${e.ticker}</span>
      <span class="cn">${esc(e.name_cn)}</span>
      ${e.tracked ? '<span class="pin" title="tracked">📌 tracked</span>' : ""}
    </div>
    ${spark ? `<div class="spark-row">${spark}<span class="muted small">热度走势</span></div>` : ""}
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

/* ---------- sectors ---------- */
const SECTOR_COLORS = {
  半导体: "#4c8dff", 科技: "#3fb0ac", 中概: "#e0574f", 金融: "#c9a227",
  医药: "#8a63d2", 消费: "#e08a3c", 汽车: "#5aa9e6", 能源电力: "#2f9e6e",
  工业军工: "#7d8590", 加密: "#d9a441", 旅游航空: "#b06fbd", 题材: "#9c6b4f",
  ETF指数: "#5d6673", 其他: "#495057",
};
const sectorColor = (s) => SECTOR_COLORS[s] || "#495057";

function renderSectors(sectors, windows) {
  const shown = (sectors || []).filter((s) => s.share > 0);
  $("#sectorPanel").hidden = shown.length === 0;
  if (!shown.length) return;

  const hours = windows?.context_hours ?? 72;
  $("#sectorWindow").textContent = `近 ${hours}h`;

  // One sector owning most of the board isn't a bug to hide — it's the headline, and the
  // number is here so it can't be mistaken for broad-based interest.
  const top = shown[0];
  $("#sectorConcentration").textContent =
    top.share >= 50 ? `${top.sector} 占 ${top.share}% — 讨论高度集中` : `${shown.length} 个板块有讨论`;

  $("#sectorBar").innerHTML = shown.map((s) =>
    `<span class="sector-seg" style="width:${s.share}%;background:${sectorColor(s.sector)}"
           title="${esc(s.sector)} ${s.share}% · ${s.tickers}只 · ${s.mentions}次提及"></span>`
  ).join("");

  $("#sectorLeaders").innerHTML = shown.map((s) => {
    const l = s.leader;
    if (!l) return "";
    const faint = l.focused_mentions < 1 ? ' style="opacity:.55"' : "";
    const why = l.focused_mentions < 1 ? " title=\"只在盘点/标签里被提到，没有专门讨论\"" : "";
    return `<span class="chip"${faint}${why}>
      <i class="dot" style="background:${sectorColor(s.sector)}"></i>${esc(s.sector)}
      <span class="muted small">${s.share}%</span>
      <span class="tk">${l.ticker}</span> ${esc(l.name_cn || "")}
      <span class="muted small">·${l.mentions}</span>
    </span>`;
  }).join("");
}

/* ---------- 大盘 / indexes ---------- */
// Index talk is most of what XHS says about US markets — 纳指 and 标普 outrun every company
// name in the corpus. On the stock board it would bury the stocks; hidden, it was the
// largest thing the dashboard could not see. So: its own strip, its own scale.
function renderIndexes(indexes) {
  const rows = indexes || [];
  $("#indexPanel").hidden = rows.length === 0;
  if (!rows.length) return;

  const commentShare = rows.reduce((n, e) => n + (e.comment_count || 0), 0);
  $("#indexNote").textContent = commentShare
    ? `${rows.length} 项 · 其中 ${commentShare} 次来自评论区`
    : `${rows.length} 项`;

  $("#indexStrip").innerHTML = rows.map((e) => {
    const sc = e.sentiment_counts;
    const senti = sc
      ? `<span class="senti"><b style="color:${BULL}">${sc.bullish || 0}</b>/<b style="color:${BEAR}">${sc.bearish || 0}</b>/<b style="color:${NEUT}">${sc.neutral || 0}</b></span>`
      : "";
    return `<div class="index-cell">
      <div class="index-top">
        <span class="tk">${e.ticker}</span>
        <span class="muted small">${esc(e.name_cn || "")}</span>
        ${trendBadge(e.trend)}
      </div>
      <div class="index-score">${e.score}</div>
      <div class="index-meta">
        <span class="muted small" title="${e.note_count} 帖 · ${e.comment_count} 评">${e.mentions} 次提及</span>
        ${quoteBadge(e)}
        ${senti}
      </div>
    </div>`;
  }).join("");
}

/* ---------- radar / tracked / suggestions / runs ---------- */
function renderRadar(radar, windows) {
  $("#radarPanel").hidden = radar.length === 0;
  const rw = $("#radarWindow");
  if (rw) rw.textContent = `近 ${windows?.context_hours ?? 72}h`;
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
async function loadScoreboard() {
  const { data } = await api("/api/scoreboard");
  const o = data.overall;
  const fmt = (rate, correct, n) => (rate == null ? "—" : `${rate}% (${correct}/${n})`);
  $("#sbSummary").textContent = o.hit_rate_1d != null ? `次日 ${o.hit_rate_1d}%` : "";
  if (!data.calls.length) {
    $("#scoreboardBody").innerHTML =
      '<p class="muted small">暂无可评估的判断 — 需要积累几天的分析与价格数据（舆论明显偏向后对照次日/7日股价）。</p>';
    return;
  }
  const head = `<p class="small muted">近${data.window_days}天，舆论明显偏向（|▲−▼|≥2）后股价是否同向。` +
    `次日命中 <b>${fmt(o.hit_rate_1d, o.correct_1d, o.evaluated_1d)}</b> · 7日命中 <b>${fmt(o.hit_rate_7d, o.correct_7d, o.evaluated_7d)}</b></p>`;
  const cell = (mv, ok) =>
    mv == null ? '<td class="muted">待定</td>' : `<td>${mv > 0 ? "+" : ""}${mv}% ${ok ? "✓" : "✗"}</td>`;
  const rows = data.calls.map((c) => {
    const lean = c.dir === "up"
      ? `<span style="color:${BULL}">▲ 看多</span>`
      : `<span style="color:${BEAR}">▼ 看空</span>`;
    return `<tr><td>${c.date}</td><td class="tk">${c.ticker}</td><td>${lean} (${c.net > 0 ? "+" : ""}${c.net})</td>` +
      `${cell(c.move_1d_pct, c.correct_1d)}${cell(c.move_7d_pct, c.correct_7d)}</tr>`;
  }).join("");
  $("#scoreboardBody").innerHTML = head +
    `<div class="table-wrap"><table><thead><tr><th>日期</th><th>ticker</th><th>舆论</th><th>次日</th><th>7日</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function progressNote(p) {
  const bits = [];
  if (p.keyword) bits.push(`关键词 ${p.keyword_index || "?"}/${p.keyword_total}「${esc(p.keyword)}」`);
  if (PHASE_CN[p.phase]) bits.push(PHASE_CN[p.phase]);
  // A crawl that is being CAPTCHA'd still returns rows, just slower and slower — say so
  // while it happens instead of only in the post-mortem error column.
  if (p.captchas) bits.push(`<b class="warn">⚠ 风控验证码 ×${p.captchas}</b>`);
  if (p.last_error) bits.push(`<span class="muted" title="${esc(p.last_error)}">${esc(p.last_error.slice(0, 60))}…</span>`);
  return bits.join(" · ");
}

async function loadRuns() {
  const { data } = await api("/api/runs?limit=20");
  $("#runsTable tbody").innerHTML = data.map((r) => {
    const p = r.progress;
    const mins = Math.round(((r.finished_at_ms || Date.now()) - r.started_at_ms) / 60000);
    const dur = r.finished_at_ms || r.status === "running" ? `${mins}min` : "—";
    const fresh = p ? `${p.notes}帖/${p.comments}评` : `${r.notes_fresh}帖/${r.comments_fresh}评`;
    const last = p ? progressNote(p) : esc(r.error || "");
    return `<tr>
      <td>${r.id}</td><td>${r.mode}</td>
      <td class="st-${r.status}">${r.status}</td>
      <td>${fresh}</td>
      <td>${dur}</td><td>${localTime(r.started_at_ms)}</td>
      <td class="err${p ? " live" : ""}" title="${esc(r.error || "")}">${last}</td>
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
  renderIndexes(data.indexes);
  renderRanking(data.ranking);
  renderSectors(data.sectors, data.windows);
  renderRadar(data.radar, data.windows);
  // stocks lead; the index cards follow, so 大盘 reads as context rather than as the headline
  await buildCards([...data.ranking, ...(data.indexes || [])]);
  loadTracked(); loadSuggestions(); loadRuns(); loadScoreboard();
}
window.addEventListener("resize", () => rankingChart?.resize());
loadStatus().then(refreshData).catch(console.error);
