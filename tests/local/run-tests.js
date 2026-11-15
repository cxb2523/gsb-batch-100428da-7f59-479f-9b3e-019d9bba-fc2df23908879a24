/* 无头浏览器验证：加载本地夹具页，断言 runner 的结局分类、计数、顺序、
   首个失败原因，以及状态区在执行过程中的持续更新快照。全程 127.0.0.1，离线。 */
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const os = require('os');

const NODE = process.execPath;
const HERE = __dirname;
const FIXTURES = path.join(HERE, 'fixtures');
const CHROME_CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
];

function startServer() {
  return new Promise((resolve, reject) => {
    const child = spawn(NODE, [path.join(HERE, 'server.js')], { stdio: ['ignore', 'pipe', 'inherit'] });
    let out = '';
    child.stdout.on('data', d => {
      out += d.toString();
      const m = out.match(/^(\d+)/);
      if (m) resolve({ child, port: Number(m[1]) });
    });
    child.on('error', reject);
  });
}

function findBrowser() {
  for (const p of CHROME_CANDIDATES) if (fs.existsSync(p)) return p;
  return null;
}

function dumpDom(browser, url, profileDir) {
  return new Promise((resolve, reject) => {
    const args = [
      '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
      '--disable-extensions', '--no-sandbox',
      '--user-data-dir=' + profileDir,
      /* 4s 预算：夹具执行链在重定向倒计时(5s)触发前完成，避免内嵌页跳转导致 dump 挂起 */
      '--virtual-time-budget=4000',
      '--timeout=15000', '--dump-dom', url
    ];
    const child = spawn(browser, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '', err = '';
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      child.kill();
      reject(new Error('chrome timeout: ' + url));
    }, 30000);
    child.stdout.on('data', d => { out += d.toString(); });
    child.stderr.on('data', d => { err += d.toString(); });
    child.on('close', () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ out, err, code: 0 });
    });
    child.on('error', e => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(e);
    });
  });
}

function attr(tag, name) {
  const m = tag.match(new RegExp(name + '="([^"]*)"'));
  return m ? m[1] : null;
}
function roleText(dom, role) {
  /* DOM 里同时含初始 0 模板和最终值，取最后一次出现的数字 */
  const re = new RegExp('data-psb-role="' + role + '"[^>]*>([0-9]+)<', 'g');
  let m, last = null;
  while ((m = re.exec(dom)) !== null) last = Number(m[1]);
  return last;
}
function probeTag(dom) {
  const m = dom.match(/<div id="__probe"[^>]*>/);
  return m ? m[0] : '';
}

const checks = [];
function check(name, cond, detail) {
  checks.push({ name, ok: !!cond, detail: detail || '' });
}

async function main() {
  const browser = findBrowser();
  if (!browser) {
    console.error('SKIP: 未找到 Chrome/Edge，无法执行无头浏览器验证');
    process.exit(2);
  }
  console.log('browser:', browser);

  /* 先生成夹具（加密算法与 index.html 相同）*/
  require('child_process').execFileSync(NODE, [path.join(HERE, 'generate-fixtures.js')], { stdio: 'inherit' });

  const { child: server, port } = await startServer();
  const base = 'http://127.0.0.1:' + port + '/';

  const CASES = [
    {
      /* 探针(#0) + 成功(#1) + 执行异常(#2) + 成功(#3) */
      file: 'partial-failure.html', total: 4,
      verify(dom) {
        check('partial: 最终结局=部分失败', /data-psb-status="partial-failure"/.test(dom));
        check('partial: 已完成 4/4', roleText(dom, 'done') === 4 && roleText(dom, 'total') === 4,
          'done=' + roleText(dom, 'done'));
        check('partial: 失败数=1', roleText(dom, 'failed') === 1);
        const order = attr(probeTag(dom), 'data-order');
        check('partial: 脚本按序结算 0:success,1:success,2:error,3:success',
          order === '0:success,1:success,2:error,3:success', order);
        check('partial: 失败前脚本副作用生效（链未被阻断）', dom.includes('s0-ran'));
        check('partial: 失败后脚本仍执行', dom.includes('<title>s2-ran</title>'));
        check('partial: 首个失败原因指向 #2 且含报错文本',
          /首个失败原因：脚本#2（执行异常）.*boom-inline-1/.test(dom));
        check('partial: 逐条日志含执行异常条目', /data-psb-outcome="error"[^>]*>脚本#2/.test(dom));
        assertLiveSnapshots(dom, 4);
      }
    },
    {
      /* 探针(#0) + 外部加载失败(#1) + 外部成功(#2) + 内联成功(#3) */
      file: 'load-failure.html', total: 4,
      verify(dom) {
        check('load: 最终结局=部分失败', /data-psb-status="partial-failure"/.test(dom));
        const order = attr(probeTag(dom), 'data-order');
        check('load: 顺序=探针成功→加载失败→外部成功→内联成功',
          order === '0:success,1:loadfailed,2:success,3:success', order);
        check('load: 失败数=1', roleText(dom, 'failed') === 1);
        check('load: 首个失败为加载失败且记录 src',
          /首个失败原因：脚本#1（加载失败）.*missing-script\.js/.test(dom));
        check('load: 正常外部脚本已加载执行', dom.includes('__okScript'));
        check('load: 外部脚本之后的内联脚本照常执行', dom.includes('after'));
        assertLiveSnapshots(dom, 4);
      }
    },
    {
      /* 2 个失败内联、无探针：真正的“全部失败”结局 */
      file: 'all-failed.html', total: 2, noProbe: true,
      verify(dom) {
        check('allfail: 最终结局=全部失败', /data-psb-status="all-failed"/.test(dom));
        check('allfail: 失败数=2', roleText(dom, 'failed') === 2);
        check('allfail: 已完成 2/2', roleText(dom, 'done') === 2 && roleText(dom, 'total') === 2);
        check('allfail: 两条均为执行异常日志',
          (dom.match(/data-psb-outcome="error"[^>]*>脚本#/g) || []).length === 2);
        check('allfail: 徽章文案=全部失败', dom.includes('全部失败'));
        check('allfail: 首个失败原因=#0 fail-a',
          /首个失败原因：脚本#0（执行异常）.*fail-a/.test(dom));
      }
    },
    {
      /* 探针(#0) + 数据块(#1,#2) + 空src(#3) + 空内联(#4) + 正常内联(#5) */
      file: 'skipped.html', total: 6,
      verify(dom) {
        check('skip: 最终结局=全部成功', /data-psb-status="all-success"/.test(dom));
        const order = attr(probeTag(dom), 'data-order');
        check('skip: 数据块与空 src 记跳过，空内联/正常脚本成功',
          order === '0:success,1:skipped,2:skipped,3:skipped,4:success,5:success', order);
        check('skip: 跳过数=3', roleText(dom, 'skipped') === 3);
        check('skip: 失败数=0', roleText(dom, 'failed') === 0);
        check('skip: 正常脚本不受跳过影响', /__skipRan/.test(dom));
        check('skip: 跳过日志给出原因', dom.includes('src 为空') && dom.includes('非可执行脚本类型'));
        assertLiveSnapshots(dom, 6);
      }
    }
  ];

  let profileN = 0;
  for (const c of CASES) {
    console.log('\n=== ' + c.file + ' ===');
    const profileDir = path.join(os.tmpdir(), 'psb-chrome-' + process.pid + '-' + (++profileN));
    const { out } = await dumpDom(browser, base + c.file, profileDir);
    if (!out.includes('psb-panel')) {
      check(c.file + ': 页面成功渲染状态区', false, 'no panel in dumped DOM');
      continue;
    }
    check(c.file + ': 页面成功渲染状态区', true);
    try { c.verify(out); } catch (e) { check(c.file + ': 断言执行无异常', false, e.message); }
  }

  /* 额外：原始 index.html（真实载荷）经本地 http 服务加载，应全部成功（同样离线）*/
  console.log('\n=== index.html (原始载荷, 本地 http) ===');
  const profileDir = path.join(os.tmpdir(), 'psb-chrome-' + process.pid + '-orig');
  const origUrl = base + 'repo/index.html';
  let orig;
  try {
    orig = await dumpDom(browser, origUrl, profileDir);
  } catch (e) {
    check('orig: 无头浏览器可加载（' + origUrl + '）', false, e.message);
    orig = { out: '' };
  }
  check('orig: 状态区渲染', orig.out.includes('psb-panel'));
  check('orig: 全部成功', /data-psb-status="all-success"/.test(orig.out));
  check('orig: 内嵌页面被还原（倒计时节点）', orig.out.includes('id="count"'));
  check('orig: 标题被还原', /<title>comix-streamer-helper<\/title>/.test(orig.out));

  server.kill();

  let failed = 0;
  for (const r of checks) {
    console.log((r.ok ? '  PASS ' : '  FAIL ') + r.name + (r.detail ? '  [' + r.detail + ']' : ''));
    if (!r.ok) failed++;
  }
  console.log('\n' + (checks.length - failed) + '/' + checks.length + ' checks passed');
  process.exit(failed ? 1 : 0);
}

/* 快照里必须出现“中途计数”（completed < total），证明状态区在过程中持续更新 */
function assertLiveSnapshots(dom, total) {
  const tag = probeTag(dom);
  const snaps = attr(tag, 'data-snapshots') || '';
  const frames = snaps.split('|').filter(Boolean);
  const mid = frames.find(f => {
    const m = f.match(/^(\d+)\//);
    return m && Number(m[1]) > 0 && Number(m[1]) < total;
  });
  check('live: 存在执行中途的状态快照(0<done<total)', !!mid, snaps.slice(0, 120));
  check('live: 最终快照到达 total=' + total, frames.some(f => f.indexOf(total + '/' + total) === 0),
    frames.slice(-2).join(','));
  check('live: psb:finished 事件触发', attr(tag, 'data-finished') === '1');
}

main().catch(e => { console.error(e); process.exit(1); });
