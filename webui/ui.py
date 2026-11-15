"""
The single-page UI, kept as a Python string on purpose.

Embedding the page means PyInstaller has no data files to place and no path to
resolve at runtime - a frozen build cannot get the UI "missing". The page talks to
`webui.server` over JSON + Server-Sent Events.
"""

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JMComic 下载器</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; padding:16px; font:15px/1.5 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
         background:#14171c; color:#d8dee9; }
  h1 { font-size:19px; margin:0 0 14px; font-weight:600; }
  .card { background:#1b1f26; border:1px solid #2a3038; border-radius:10px; padding:14px; margin-bottom:12px; }
  .row { display:flex; gap:10px; align-items:center; margin-bottom:10px; flex-wrap:wrap; }
  .row:last-child { margin-bottom:0; }
  label.cap { width:88px; flex:none; color:#93a1b1; font-size:14px; text-align:right; }
  input[type=text], input[type=number], select {
    flex:1; min-width:140px; padding:9px 10px; background:#0f1216; color:#e6edf3;
    border:1px solid #303842; border-radius:7px; font-size:14px; font-family:inherit; }
  input:focus, select:focus { outline:none; border-color:#4a90d9; }
  button { padding:10px 18px; border:0; border-radius:7px; background:#2d6cdf; color:#fff;
           font-size:14px; font-weight:600; cursor:pointer; font-family:inherit; }
  button:hover:not(:disabled) { background:#3a7cf0; }
  button:disabled { opacity:.45; cursor:not-allowed; }
  button.ghost { background:#2a3038; font-weight:500; }
  button.ghost:hover:not(:disabled) { background:#363e48; }
  .hint { color:#7b8794; font-size:12.5px; margin-left:98px; }
  .checks { display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  .checks label { display:flex; gap:5px; align-items:center; cursor:pointer; }
  input[type=checkbox] { width:16px; height:16px; accent-color:#2d6cdf; }
  #log { height:300px; overflow:auto; background:#0f1216; border:1px solid #262c34;
         border-radius:8px; padding:10px; font:12.5px/1.55 ui-monospace,Consolas,monospace;
         white-space:pre-wrap; word-break:break-all; }
  #status { margin-top:10px; color:#93a1b1; font-size:13.5px; }
  #status.ok { color:#3fb950; } #status.err { color:#f85149; }
  #status.warn { color:#d29922; }
  .bar { height:5px; background:#262c34; border-radius:3px; overflow:hidden; margin-top:8px; }
  .bar > i { display:block; height:100%; width:0; background:#2d6cdf; transition:width .3s; }
  .muted { color:#7b8794; }
  a { color:#4a90d9; }

  /* per-book outcome table */
  #results { display:none; }
  #results.show { display:block; }
  .res-head { display:flex; gap:12px; align-items:baseline; flex-wrap:wrap; margin-bottom:10px; }
  .res-head .t { font-weight:600; font-size:15px; }
  .res-head .s { color:#93a1b1; font-size:13px; }
  table#resTable { width:100%; border-collapse:collapse; font-size:13px; }
  #resTable th, #resTable td { text-align:left; padding:7px 8px; vertical-align:top;
                               border-top:1px solid #262c34; }
  #resTable th { color:#7b8794; font-weight:500; font-size:12.5px; white-space:nowrap; }
  #resTable td.id { font:12.5px/1.5 ui-monospace,Consolas,monospace; white-space:nowrap; }
  #resTable td.num { color:#7b8794; width:34px; }
  #resTable td.err { color:#f85149; word-break:break-all; }
  #resTable td.paths { font:12px/1.5 ui-monospace,Consolas,monospace; word-break:break-all;
                       color:#a9b6c5; }
  #resTable tr:first-child td { border-top:0; }
  .badge { display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px;
           white-space:nowrap; border:1px solid transparent; }
  .st-ok    { color:#3fb950; background:#12261a; border-color:#1f4a2c; }
  .st-err   { color:#f85149; background:#2a1416; border-color:#5a2226; }
  .st-warn  { color:#d29922; background:#2a2211; border-color:#584516; }
  .st-run   { color:#4a90d9; background:#111d2c; border-color:#20456b; }
  .st-idle  { color:#7b8794; background:#1a1f26; border-color:#2a3038; }
</style>
</head>
<body>
<h1>JMComic 下载器</h1>

<div class="card">
  <div class="row">
    <label class="cap" for="ids">车号 / 链接</label>
    <input type="text" id="ids" placeholder="例如 438696，多个用空格或逗号分隔" autocomplete="off">
  </div>
  <div class="hint">支持 438696、JM438696、18comic 链接</div>

  <div class="row">
    <label class="cap">类型</label>
    <div class="checks">
      <label><input type="radio" name="kind" value="album" checked> 整本</label>
      <label><input type="radio" name="kind" value="photo"> 单章</label>
    </div>
  </div>

  <div class="row">
    <label class="cap" for="saveDir">保存到</label>
    <input type="text" id="saveDir" autocomplete="off">
  </div>

  <div class="row">
    <label class="cap">导出</label>
    <div class="checks">
      <label><input type="checkbox" id="exp_pdf" checked> PDF</label>
      <label><input type="checkbox" id="exp_zip"> ZIP</label>
      <label><input type="checkbox" id="exp_png"> 长图</label>
      <span class="muted" style="font-size:12.5px">PDF / 长图 需 Pillow</span>
    </div>
  </div>

  <div class="row">
    <label class="cap" for="threads">图片并发</label>
    <input type="number" id="threads" min="1" max="50" value="30" style="flex:0 0 100px">
    <label class="cap" for="backend" style="width:auto">HTTP 后端</label>
    <select id="backend" style="flex:0 0 170px">
      <option value="curl_cffi">curl_cffi</option>
      <option value="requests">requests</option>
    </select>
  </div>

  <div class="row">
    <label class="cap" for="proxy">代理</label>
    <input type="text" id="proxy" placeholder="留空 = 跟随系统，如 127.0.0.1:7890" autocomplete="off">
  </div>

  <div class="row">
    <button id="start">开始下载</button>
    <button id="cancel" class="ghost" disabled>取消</button>
    <button id="clear" class="ghost">清空日志</button>
    <button id="quit" class="ghost" title="停止本地服务并关闭程序">退出程序</button>
    <span class="muted" id="where"></span>
  </div>
  <div class="bar"><i id="bar"></i></div>
  <div id="status">就绪</div>
</div>

<div class="card">
  <div id="log"></div>
</div>

<div class="card" id="results">
  <div class="res-head">
    <span class="t" id="resTitle">批量下载结果</span>
    <span class="s" id="resSummary"></span>
  </div>
  <table id="resTable">
    <thead><tr>
      <th>#</th><th>车号</th><th>状态</th><th>图片数 / 导出文件</th><th>说明</th>
    </tr></thead>
    <tbody id="resBody"></tbody>
  </table>
</div>

<script>
const TOKEN = new URLSearchParams(location.search).get('token') || "__TOKEN__";
const $ = (id) => document.getElementById(id);
const logEl = $('log'), statusEl = $('status'), barEl = $('bar');
let es = null, busy = false;

// Per-book outcome rows, keyed by the server-assigned index so duplicate ids in one
// batch cannot overwrite each other's row.
const STATUS_LABEL = { pending:'未开始', running:'下载中', success:'成功',
                       failed:'失败', cancelled:'取消' };
const STATUS_CLASS = { pending:'st-idle', running:'st-run', success:'st-ok',
                       failed:'st-err', cancelled:'st-warn' };
let rows = [];           // [{index,id,input,status,label,imageCount,exportFiles,error,note}]

function append(text, cls) {
  const atBottom = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 30;
  const span = document.createElement('span');
  if (cls) span.style.color = cls;
  span.textContent = text + "\n";
  logEl.appendChild(span);
  if (atBottom) logEl.scrollTop = logEl.scrollHeight;
}
function setStatus(text, cls) { statusEl.textContent = text; statusEl.className = cls || ''; }
function setBusy(on) {
  busy = on;
  $('start').disabled = on; $('cancel').disabled = !on;
  barEl.style.width = on ? '35%' : '100%';
  if (!on) setTimeout(() => { barEl.style.width = '0'; }, 700);
}

function resetResults() {
  rows = [];
  $('resBody').textContent = '';
  $('resTitle').textContent = '批量下载结果';
  $('resSummary').textContent = '';
  $('results').classList.remove('show');
}

function upsertRow(item) {
  const row = rows.find(r => r.index === item.index);
  if (row) Object.assign(row, item); else rows.push(Object.assign({}, item));
  rows.sort((a, b) => a.index - b.index);
}

function cell(tr, tag, text, cls) {
  const td = document.createElement(tag);
  // Assigning `undefined` to textContent renders the literal word "undefined",
  // so empty cells must be set explicitly.
  td.textContent = (text === null || text === undefined) ? '' : String(text);
  if (cls) td.className = cls;
  tr.appendChild(td);
  return td;
}

function renderTable() {
  const body = $('resBody');
  body.textContent = '';
  rows.forEach((row, i) => {
    const tr = document.createElement('tr');
    cell(tr, 'td', String(i + 1), 'num');
    // A row that failed id parsing has no resolved id: show the raw input instead.
    const label = row.resolved === false ? String(row.input || '') : ('JM' + row.id);
    const suffix = (row.resolved !== false && row.input && row.input !== row.id)
      ? '（' + row.input + '）' : '';
    cell(tr, 'td', label + suffix, 'id');
    const stCell = cell(tr, 'td');
    const badge = document.createElement('span');
    badge.className = 'badge ' + (STATUS_CLASS[row.status] || 'st-idle');
    badge.textContent = row.label || STATUS_LABEL[row.status] || row.status;
    stCell.appendChild(badge);

    const detail = cell(tr, 'td', '');
    if (row.status === 'success') {
      const count = (row.imageCount === null || row.imageCount === undefined) ? 0 : row.imageCount;
      detail.appendChild(document.createTextNode(count + ' 张图片'));
      const files = row.exportFiles || [];
      if (files.length) {
        const box = document.createElement('div');
        box.className = 'paths';
        files.forEach(p => {
          const line = document.createElement('div');
          line.textContent = '↳ ' + p;
          box.appendChild(line);
        });
        detail.appendChild(box);
      } else if (row.note) {
        detail.appendChild(document.createTextNode('（' + row.note + '）'));
      }
    } else {
      detail.textContent = '—';
    }

    const noteCell = cell(tr, 'td', '');
    if (row.error) {
      noteCell.classList.add('err');
      noteCell.textContent = row.error;
    } else if (row.note) {
      noteCell.className = 'muted';
      noteCell.textContent = row.note;
    } else if (row.status === 'success' && row.savePath) {
      noteCell.className = 'path';
      noteCell.textContent = row.savePath;
    }
    body.appendChild(tr);
  });
}

function summarise(ev) {
  const s = ev.summary || {};
  const total = (s.total === null || s.total === undefined) ? rows.length : s.total;
  const parts = ['共 ' + total + ' 本'];
  if (s.success) parts.push('成功 ' + s.success);
  if (s.failed) parts.push('失败 ' + s.failed);
  if (s.cancelled) parts.push('取消 ' + s.cancelled);
  if (s.pending) parts.push('未开始 ' + s.pending);
  $('resTitle').textContent =
    { success:'全部成功', partial:'部分失败', failed:'全部失败',
      cancelled:'已取消（未完成全部）', error:'任务失败' }[ev.outcome] || '批量下载结果';
  $('resSummary').textContent = parts.join(' · ');
}

async function api(path, body) {
  const res = await fetch(path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN), {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
  return data;
}

function subscribe() {
  if (es) return;
  es = new EventSource('/api/events?token=' + encodeURIComponent(TOKEN));
  es.onmessage = (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    handleEvent(msg);
  };
  es.onerror = () => { /* EventSource retries on its own */ };
}

// Split out of the EventSource callback so the table logic can also be driven by the
// POST response and by tests.
function handleEvent(msg) {
  if (msg.type === 'log') append(msg.text);
  else if (msg.type === 'plan') {
    // A fresh batch: the server replayed its plan, so start the table from scratch.
    resetResults();
    (msg.items || []).forEach(upsertRow);
    renderTable();
    $('results').classList.add('show');
  }
  else if (msg.type === 'item') {
    upsertRow(msg.item || {});
    renderTable();
  }
  else if (msg.type === 'result') {
    append('[完成] JM' + msg.item.id + ' ' + (msg.item.title || ''));
    append('       保存位置：' + (msg.item.savePath || ''));
    append('       图片：' + msg.item.imageCount + ' 张 · 耗时：' + msg.item.durationSec + ' 秒');
    (msg.item.exportFiles || []).forEach(p => append('       导出文件：' + p));
  }
  else if (msg.type === 'error') { append('[错误] ' + msg.text, '#f85149'); }
  else if (msg.type === 'done') {
    // The done event carries the authoritative per-book table; re-render from it so
    // the page agrees with the server even if an item event was missed.
    resetResults();
    (msg.items || []).forEach(upsertRow);
    summarise(msg);
    renderTable();
    $('results').classList.add('show');
    setBusy(false);
    setStatus(msg.text, { success:'ok', partial:'warn', failed:'err',
                          cancelled:'warn', error:'err' }[msg.outcome] || '');
    append('===== ' + msg.text + ' =====',
           { success:'#3fb950', error:'#f85149', failed:'#f85149' }[msg.outcome] || '#d29922');
  }
}

async function start() {
  const ids = $('ids').value.trim();
  if (!ids) { alert('请先输入至少一个车号'); return; }
  const exports = ['pdf','zip','png'].filter(s => $('exp_' + s).checked);
  const kind = document.querySelector('input[name=kind]:checked').value;
  append('');
  append('===== 开始：' + ids + ' · 类型=' + kind + ' · 导出=' + (exports.join(',') || '无') + ' =====');
  resetResults();
  setBusy(true); setStatus('正在准备…');
  try {
    const started = await api('/api/download', {
      ids, kind, exports,
      saveDir: $('saveDir').value.trim(),
      threads: parseInt($('threads').value, 10) || 30,
      proxy: $('proxy').value.trim(),
      backend: $('backend').value,
    });
    // Draw the table immediately from the submit response; the plan event that the
    // SSE stream replays carries the same rows.
    if (started && started.items) {
      started.items.forEach(upsertRow);
      summarise({ outcome:'', summary: started.summary || {} });
      renderTable();
      $('results').classList.add('show');
    }
    subscribe();
  } catch (e) { setBusy(false); setStatus('失败：' + e.message, 'err'); append('[错误] ' + e.message, '#f85149'); }
}

$('start').onclick = start;
$('cancel').onclick = async () => { try { await api('/api/cancel', {}); } catch {} };
$('clear').onclick = () => { logEl.textContent = ''; };
$('quit').onclick = async () => {
  if (!confirm('停止下载服务并退出程序？')) return;
  try { await api('/api/shutdown', {}); } catch {}
  if (es) { es.close(); es = null; }
  setStatus('程序已退出，可以关闭此页面。');
  document.querySelectorAll('button').forEach(b => b.disabled = true);
};
$('ids').addEventListener('keydown', e => { if (e.key === 'Enter') start(); });

(async () => {
  try {
    const cfg = await api('/api/config');
    $('saveDir').value = cfg.saveDir || '';
    $('backend').value = cfg.backend || 'curl_cffi';
    $('where').textContent = '默认保存到 ' + (cfg.saveDir || '');
    if (cfg.missingDeps && cfg.missingDeps.length) {
      append('提示：缺少导出依赖 ' + cfg.missingDeps.join('、') + '，对应导出可能不产出文件');
    }
  } catch (e) { append('[警告] 无法读取配置：' + e.message, '#d29922'); }
  subscribe();
})();
</script>
</body>
</html>
"""
