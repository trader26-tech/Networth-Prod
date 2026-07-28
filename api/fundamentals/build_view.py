"""
Generate a self-contained HTML preliminary view of the scored universe.

Output: api/fundamentals/data/snowflake_view.html
Opens in any browser — no server, no dependencies.

Features:
  - Summary cards (totals per bucket, by cap tier)
  - Filter chips (bucket, cap tier)
  - Search by ticker / name / sub-sector
  - Sortable table with composite + 6 axis scores
  - Click row to see all 45 raw metrics

Run:
    python3 api/fundamentals/build_view.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from snowflake import score_universe, UNIVERSE_CSV

OUT_HTML = UNIVERSE_CSV.parent / "snowflake_view.html"


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AlgoInvest — Snowflake Preliminary View</title>
<style>
  :root {
    --bg: #0b0e14;
    --panel: #151a23;
    --panel2: #1c2330;
    --border: #2a3245;
    --text: #e6e9ef;
    --text-dim: #8b94a8;
    --accent: #4d9aff;
    --keep: #2dbb7d;
    --hold: #4d9aff;
    --trim: #d9a441;
    --exit: #e0556b;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    font-size: 13px;
    line-height: 1.4;
  }
  header {
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    padding: 16px 24px;
  }
  header h1 { font-size: 18px; font-weight: 600; }
  header .sub { font-size: 12px; color: var(--text-dim); margin-top: 2px; }

  .summary {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    padding: 16px 24px;
    background: var(--panel);
  }
  .stat {
    background: var(--panel2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 14px;
  }
  .stat .lbl { font-size: 11px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; }
  .stat .val { font-size: 22px; font-weight: 600; margin-top: 4px; }
  .stat.keep .val { color: var(--keep); }
  .stat.hold .val { color: var(--hold); }
  .stat.trim .val { color: var(--trim); }
  .stat.exit .val { color: var(--exit); }
  .stat .pct { font-size: 11px; color: var(--text-dim); margin-top: 2px; }

  .toolbar {
    display: flex;
    gap: 12px;
    padding: 12px 24px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    align-items: center;
    flex-wrap: wrap;
  }
  .chip {
    padding: 6px 12px;
    border-radius: 16px;
    border: 1px solid var(--border);
    background: var(--panel2);
    color: var(--text-dim);
    cursor: pointer;
    font-size: 12px;
    user-select: none;
  }
  .chip.active { color: var(--text); border-color: var(--accent); background: rgba(77, 154, 255, 0.1); }
  .chip.keep.active { color: var(--keep); border-color: var(--keep); background: rgba(45, 187, 125, 0.1); }
  .chip.hold.active { color: var(--hold); border-color: var(--hold); background: rgba(77, 154, 255, 0.1); }
  .chip.trim.active { color: var(--trim); border-color: var(--trim); background: rgba(217, 164, 65, 0.1); }
  .chip.exit.active { color: var(--exit); border-color: var(--exit); background: rgba(224, 85, 107, 0.1); }
  input[type="search"], select {
    background: var(--panel2);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
    font-family: inherit;
  }
  input[type="search"] { width: 240px; }

  main { padding: 0 24px 24px; }
  table {
    width: 100%;
    border-collapse: collapse;
    background: var(--panel2);
    border: 1px solid var(--border);
    margin-top: 12px;
  }
  thead { background: var(--panel); position: sticky; top: 0; z-index: 1; }
  th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); }
  th { font-size: 11px; text-transform: uppercase; color: var(--text-dim); letter-spacing: 0.5px; cursor: pointer; user-select: none; white-space: nowrap; }
  th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
  tr { cursor: pointer; }
  tr:hover { background: rgba(255,255,255,0.03); }
  .bucket-pill { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; }
  .bucket-pill.KEEP_AND_ADD { background: rgba(45, 187, 125, 0.15); color: var(--keep); }
  .bucket-pill.HOLD { background: rgba(77, 154, 255, 0.15); color: var(--hold); }
  .bucket-pill.TRIM { background: rgba(217, 164, 65, 0.15); color: var(--trim); }
  .bucket-pill.EXIT { background: rgba(224, 85, 107, 0.15); color: var(--exit); }
  .axis-score { font-variant-numeric: tabular-nums; padding: 2px 6px; border-radius: 4px; font-size: 11px; }
  .axis-0, .axis-1 { background: rgba(224, 85, 107, 0.15); color: var(--exit); }
  .axis-2 { background: rgba(217, 164, 65, 0.15); color: var(--trim); }
  .axis-3 { background: rgba(217, 164, 65, 0.2); color: var(--trim); }
  .axis-4 { background: rgba(77, 154, 255, 0.15); color: var(--hold); }
  .axis-5, .axis-6 { background: rgba(45, 187, 125, 0.15); color: var(--keep); }

  /* Modal */
  .overlay {
    display: none;
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.7);
    z-index: 10;
    align-items: center; justify-content: center;
  }
  .overlay.open { display: flex; }
  .modal {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    width: min(800px, 92vw);
    max-height: 86vh;
    overflow: auto;
    padding: 20px 24px;
  }
  .modal h2 { font-size: 16px; margin-bottom: 4px; }
  .modal .meta { font-size: 12px; color: var(--text-dim); margin-bottom: 16px; }
  .axes-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; margin-bottom: 16px; }
  .axes-grid .ax { background: var(--panel2); border: 1px solid var(--border); border-radius: 6px; padding: 8px; text-align: center; }
  .axes-grid .ax .lbl { font-size: 10px; color: var(--text-dim); text-transform: uppercase; }
  .axes-grid .ax .val { font-size: 18px; font-weight: 600; margin-top: 2px; }
  .axes-grid .ax .sub { font-size: 10px; color: var(--text-dim); }
  .raw-table { width: 100%; }
  .raw-table td { padding: 4px 8px; border: none; font-size: 12px; }
  .raw-table td.k { color: var(--text-dim); width: 50%; }
  .raw-table td.v { font-variant-numeric: tabular-nums; }
  .close-btn { float: right; cursor: pointer; color: var(--text-dim); font-size: 20px; line-height: 1; }
  .close-btn:hover { color: var(--text); }

  .reason { font-size: 11px; color: var(--text-dim); margin-top: 2px; }
</style>
</head>
<body>
<header>
  <h1>AlgoInvest — Snowflake Preliminary View</h1>
  <div class="sub">__SUMMARY_SUB__</div>
</header>

<div class="summary" id="summary"></div>

<div class="toolbar">
  <div id="bucketChips"></div>
  <div id="capChips"></div>
  <input type="search" id="searchBox" placeholder="Search ticker, name, sub-sector…">
  <select id="sortBy">
    <option value="composite">Sort: Composite (desc)</option>
    <option value="market_cap">Sort: Market Cap (desc)</option>
    <option value="ticker">Sort: Ticker (A→Z)</option>
    <option value="value">Sort: Value axis</option>
    <option value="future">Sort: Future axis</option>
    <option value="past">Sort: Past axis</option>
    <option value="health">Sort: Health axis</option>
    <option value="governance">Sort: Governance axis</option>
  </select>
  <span id="resultCount" style="color: var(--text-dim); font-size: 12px;"></span>
</div>

<main>
  <table>
    <thead>
      <tr>
        <th>Ticker</th>
        <th>Name</th>
        <th>Sub-Sector</th>
        <th class="num">Cap (₹Cr)</th>
        <th>Tier</th>
        <th class="num">Composite</th>
        <th>Bucket</th>
        <th class="num">V</th>
        <th class="num">F</th>
        <th class="num">P</th>
        <th class="num">H</th>
        <th class="num">D</th>
        <th class="num">G</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
</main>

<div class="overlay" id="overlay" onclick="if(event.target.id==='overlay')closeModal()">
  <div class="modal" id="modal"></div>
</div>

<script>
const DATA = __DATA_JSON__;
const state = { bucket: 'ALL', cap: 'ALL', search: '', sort: 'composite' };

function bucketCounts() {
  const c = {};
  for (const s of DATA) c[s.bucket] = (c[s.bucket]||0) + 1;
  return c;
}
function capCounts() {
  const c = {};
  for (const s of DATA) c[s.cap_tier] = (c[s.cap_tier]||0) + 1;
  return c;
}

function renderSummary() {
  const c = bucketCounts();
  const total = DATA.length;
  const pct = (n) => total ? ((n/total)*100).toFixed(1) + '%' : '0%';
  document.getElementById('summary').innerHTML = `
    <div class="stat"><div class="lbl">Universe</div><div class="val">${total.toLocaleString()}</div><div class="pct">stocks scored</div></div>
    <div class="stat keep"><div class="lbl">Keep &amp; Add</div><div class="val">${c.KEEP_AND_ADD||0}</div><div class="pct">${pct(c.KEEP_AND_ADD||0)}</div></div>
    <div class="stat hold"><div class="lbl">Hold</div><div class="val">${c.HOLD||0}</div><div class="pct">${pct(c.HOLD||0)}</div></div>
    <div class="stat trim"><div class="lbl">Trim</div><div class="val">${c.TRIM||0}</div><div class="pct">${pct(c.TRIM||0)}</div></div>
    <div class="stat exit"><div class="lbl">Exit</div><div class="val">${c.EXIT||0}</div><div class="pct">${pct(c.EXIT||0)}</div></div>
  `;
}

function renderChips() {
  const bc = bucketCounts();
  const cc = capCounts();
  const bucketDef = [['ALL','All'],['KEEP_AND_ADD','Keep'],['HOLD','Hold'],['TRIM','Trim'],['EXIT','Exit']];
  document.getElementById('bucketChips').innerHTML = bucketDef.map(([k,l])=>{
    const cls = k==='KEEP_AND_ADD'?'keep':k==='HOLD'?'hold':k==='TRIM'?'trim':k==='EXIT'?'exit':'';
    const n = k==='ALL' ? DATA.length : (bc[k]||0);
    return `<span class="chip ${cls} ${state.bucket===k?'active':''}" onclick="setBucket('${k}')">${l} <span style="opacity:0.6">${n}</span></span>`;
  }).join(' ');
  const capDef = [['ALL','All caps'],['large','Large'],['mid','Mid'],['small','Small']];
  document.getElementById('capChips').innerHTML = capDef.map(([k,l])=>{
    const n = k==='ALL' ? DATA.length : (cc[k]||0);
    return `<span class="chip ${state.cap===k?'active':''}" onclick="setCap('${k}')">${l} <span style="opacity:0.6">${n}</span></span>`;
  }).join(' ');
}

function setBucket(b) { state.bucket = b; renderChips(); renderRows(); }
function setCap(c) { state.cap = c; renderChips(); renderRows(); }

function filtered() {
  const q = state.search.trim().toLowerCase();
  return DATA.filter(s => {
    if (state.bucket !== 'ALL' && s.bucket !== state.bucket) return false;
    if (state.cap !== 'ALL' && s.cap_tier !== state.cap) return false;
    if (q) {
      const hay = (s.ticker + ' ' + s.name + ' ' + s.subsector).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function sortRows(rows) {
  const k = state.sort;
  const get = (s) => {
    if (k === 'composite') return s.composite ?? -Infinity;
    if (k === 'market_cap') return s.market_cap_cr ?? -Infinity;
    if (k === 'ticker') return s.ticker;
    return s.axes[k]?.score ?? -Infinity;
  };
  rows.sort((a,b) => {
    const va = get(a), vb = get(b);
    if (typeof va === 'string') return va.localeCompare(vb);
    return vb - va;
  });
  return rows;
}

function fmt(n, d=1) {
  if (n === null || n === undefined || isNaN(n)) return '—';
  return Number(n).toLocaleString('en-IN', { maximumFractionDigits: d });
}
function axisClass(n) { return 'axis-' + Math.max(0, Math.min(6, Math.round(n||0))); }

function renderRows() {
  const rows = sortRows(filtered());
  document.getElementById('resultCount').textContent = `${rows.length.toLocaleString()} matches`;
  const shown = rows.slice(0, 2000);  // soft cap to keep DOM snappy
  document.getElementById('rows').innerHTML = shown.map((s,i)=>`
    <tr onclick="openModal(${DATA.indexOf(s)})">
      <td><b>${s.ticker}</b></td>
      <td>${s.name}</td>
      <td>${s.subsector || ''}</td>
      <td class="num">${fmt(s.market_cap_cr, 0)}</td>
      <td>${s.cap_tier}</td>
      <td class="num"><b>${fmt(s.composite, 2)}</b></td>
      <td><span class="bucket-pill ${s.bucket}">${s.bucket.replace('_',' ')}</span></td>
      <td class="num"><span class="axis-score ${axisClass(s.axes.value.score)}">${fmt(s.axes.value.score,1)}</span></td>
      <td class="num"><span class="axis-score ${axisClass(s.axes.future.score)}">${fmt(s.axes.future.score,1)}</span></td>
      <td class="num"><span class="axis-score ${axisClass(s.axes.past.score)}">${fmt(s.axes.past.score,1)}</span></td>
      <td class="num"><span class="axis-score ${axisClass(s.axes.health.score)}">${fmt(s.axes.health.score,1)}</span></td>
      <td class="num"><span class="axis-score ${axisClass(s.axes.dividend.score)}">${fmt(s.axes.dividend.score,1)}</span></td>
      <td class="num"><span class="axis-score ${axisClass(s.axes.governance.score)}">${fmt(s.axes.governance.score,1)}</span></td>
    </tr>
  `).join('');
  if (rows.length > 2000) {
    document.getElementById('rows').innerHTML += `<tr><td colspan="13" style="text-align:center;color:var(--text-dim);padding:14px;">…${(rows.length-2000).toLocaleString()} more rows hidden. Refine filters to see them.</td></tr>`;
  }
}

function openModal(idx) {
  const s = DATA[idx];
  const axesHTML = ['value','future','past','health','dividend','governance'].map(a=>{
    const ax = s.axes[a];
    return `<div class="ax"><div class="lbl">${a}</div><div class="val ${axisClass(ax.score)}">${fmt(ax.score,1)}/6</div><div class="sub">${ax.passed}/${ax.available} checks</div></div>`;
  }).join('');
  const rawRows = Object.entries(s.raw).map(([k,v])=>`<tr><td class="k">${k}</td><td class="v">${v||'—'}</td></tr>`).join('');
  document.getElementById('modal').innerHTML = `
    <span class="close-btn" onclick="closeModal()">×</span>
    <h2>${s.ticker} — ${s.name}</h2>
    <div class="meta">${s.subsector} · ₹${fmt(s.market_cap_cr,0)} Cr · ${s.cap_tier} cap · <span class="bucket-pill ${s.bucket}">${s.bucket.replace('_',' ')}</span></div>
    <div class="reason"><b>Composite:</b> ${fmt(s.composite,2)}/6 — ${s.reason}</div>
    <div class="axes-grid" style="margin-top:14px">${axesHTML}</div>
    <h3 style="font-size:13px;margin-bottom:8px;color:var(--text-dim);">All 45 raw metrics</h3>
    <table class="raw-table">${rawRows}</table>
  `;
  document.getElementById('overlay').classList.add('open');
}
function closeModal() { document.getElementById('overlay').classList.remove('open'); }

document.getElementById('searchBox').addEventListener('input', e => { state.search = e.target.value; renderRows(); });
document.getElementById('sortBy').addEventListener('change', e => { state.sort = e.target.value; renderRows(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

renderSummary();
renderChips();
renderRows();
</script>
</body>
</html>
"""


def main() -> None:
    scored = score_universe()
    print(f"Scored {len(scored)} stocks")
    bc = Counter(s["bucket"] for s in scored)
    for b, n in bc.most_common():
        print(f"  {b:>14}: {n:>5}")

    # Pull just the columns we want as raw, in a sensible order
    data = scored  # already the right shape

    sub = (
        f"{len(scored):,} stocks scored on 6-axis Snowflake · "
        f"Generated from tickertape_universe.csv · "
        f"Click any row for full 45-metric breakdown."
    )

    html = (
        HTML_TEMPLATE
        .replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
        .replace("__SUMMARY_SUB__", sub)
    )
    OUT_HTML.write_text(html, encoding="utf-8")
    size_mb = OUT_HTML.stat().st_size / 1024 / 1024
    print(f"\nWrote: {OUT_HTML}")
    print(f"  Size: {size_mb:.1f} MB")
    print(f"  Open in browser: file://{OUT_HTML}")


if __name__ == "__main__":
    main()
