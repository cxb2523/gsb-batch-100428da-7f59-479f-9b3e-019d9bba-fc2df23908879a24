/*
 * 本地离线验证夹具生成器（不依赖任何外部网络/真实站点）
 * 用与 index.html 完全相同的加密算法把自造的内嵌页面写进 7 个隐藏输入框，
 * 生成结构与 index.html 一致的测试页，供无头浏览器加载验证容错执行链。
 */
const fs = require('fs');
const path = require('path');

const REPO = path.join(__dirname, '..', '..');
const OUT = path.join(__dirname, 'fixtures');
fs.mkdirSync(OUT, { recursive: true });

/* ---- 与 index.html 完全一致的解密密钥 / 加密算法（XOR + base64，逆过程）---- */
const KEY = Buffer.from('/SqdvvqyRVvGP/weOSDgGg==', 'base64'); // atob('/SqdvvqyRVvGP/weOSDgGg==')

function encrypt(plainUtf8) {
  const raw = Buffer.from(plainUtf8, 'utf8');
  const xor = Buffer.alloc(raw.length);
  for (let i = 0; i < raw.length; i++) xor[i] = raw[i] ^ KEY[i % KEY.length];
  return xor.toString('base64');
}

/* 拆进 7 个隐藏输入框：前 6 个固定长度，余量进第 7 个（与手工载荷的分片方式一致）*/
function splitFields(blob) {
  const parts = ['', '', '', '', '', '', ''];
  const chunk = Math.max(1, Math.ceil(blob.length / 7));
  let p = 0;
  for (let i = 0; i < 6 && p < blob.length; i++) {
    parts[i] = blob.slice(p, p + chunk);
    p += chunk;
  }
  parts[6] = blob.slice(p);
  return parts;
}

/* 取 index.html 的外壳（head 里的 runner + 样式 + body 里的隐藏框），替换隐藏框的值 */
function wrapPayload(embeddedHtml) {
  const shell = fs.readFileSync(path.join(REPO, 'index.html'), 'utf8');
  const fields = splitFields(encrypt(embeddedHtml));
  let out = shell;
  for (let i = 0; i < 7; i++) {
    out = out.replace(
      new RegExp('(<input type="hidden" id="gqsyljpsnak' + i + '" value=")[^"]*(")'),
      (m, pre, post) => pre + fields[i] + post
    );
  }
  return out;
}

/* 每个夹具的内联探针：body 会被 runner 用 innerHTML 整体替换，因此探针在替换后
   重新挂载一个持久节点；每次 runner 重绘状态区时把当前计数快照写入该节点，
   用于证明状态区在“执行过程中”持续更新，而不是最后一次性写入。 */
const PROBE =
  '<script>(function(){' +
  'window.__snaps=[];' +
  'function mount(){var p=document.getElementById("__probe");if(!p){p=document.createElement("div");' +
  'p.id="__probe";p.style.display="none";document.body.appendChild(p);}return p;}' +
  'function snap(){if(!window.__psbRunner)return;var s=window.__psbRunner.state;' +
  'window.__snaps.push(s.completed+"/"+s.total+"/f"+s.failed+"/s"+s.skipped);' +
  'var p=mount();p.setAttribute("data-snapshots",window.__snaps.join("|"));' +
  'var r=window.__psbRunner.getResults();' +
  'p.setAttribute("data-order",r.map(function(x){return x.index+":"+x.outcome;}).join(","));}' +
  'setInterval(snap,4);' +
  'document.addEventListener("psb:finished",function(){snap();mount().setAttribute("data-finished","1");});' +
  '})();</script>';

const FIXTURES = {
  /* 1) 部分失败：成功 → 内联执行异常 → 成功；要求链不断、后续照跑、首个失败=#2、部分失败。
        探针在最前面（脚本#0），其 interval 才能覆盖整个执行过程，抓到中途快照。 */
  'partial-failure.html': [
    '<!DOCTYPE html><html><head><title>夹具-部分失败</title></head><body>',
    '<div id="m0">start</div>',
    PROBE,
    '<script>document.getElementById("m0").textContent="s0-ran";window.__m0=true;</script>',
    '<script>throw new Error("boom-inline-1");</script>',
    '<script>document.title="s2-ran";window.__m2=true;</script>',
    '</body></html>'
  ].join(''),

  /* 2) 加载失败：外部脚本 404（本地服务器返回 404，不走外网）→ 后续内联仍执行；
        再放一个能正常加载的外部脚本，验证顺序加载与成功结算。 */
  'load-failure.html': [
    '<!DOCTYPE html><html><head><title>夹具-加载失败</title></head><body>',
    '<div id="m0">start</div>',
    PROBE,
    '<script src="./missing-script.js"></script>',
    '<script src="./ok-script.js"></script>',
    '<script>window.__afterLoad=!!(window.__okScript);document.getElementById("m0").textContent="after";</script>',
    '</body></html>'
  ].join(''),

  /* 3) 全部失败：两个内联脚本都抛错，且不注入探针（探针本身会成功）→ 全部失败；
        用一个自挂节点在失败后仍记录 runner 状态（节点由失败脚本之后的逻辑无法创建，
        因此这里改为监听型探针，但不产生可执行 <script>——通过外部测试直接读面板 DOM）。 */
  'all-failed.html': [
    '<!DOCTYPE html><html><head><title>夹具-全部失败</title></head><body>',
    '<div id="m0">start</div>',
    '<script>throw new Error("fail-a");</script>',
    '<script>throw new Error("fail-b");</script>',
    '</body></html>'
  ].join(''),

  /* 4) 跳过：JSON-LD 数据块、JSON 数据块、空 src 外部脚本、空内联、正常内联；
        要求跳过被记录、正常脚本照跑、全部成功。探针仍在最前。 */
  'skipped.html': [
    '<!DOCTYPE html><html><head><title>夹具-跳过</title></head><body>',
    PROBE,
    '<script type="application/ld+json">{"@context":"x","name":"data-block"}</script>',
    '<script type="application/json">{"a":1}</script>',
    '<script src=""></script>',
    '<script></script>',
    '<script>window.__skipRan=true;</script>',
    '</body></html>'
  ].join('')
};

for (const [name, embedded] of Object.entries(FIXTURES)) {
  fs.writeFileSync(path.join(OUT, name), wrapPayload(embedded), 'utf8');
  console.log('generated', name, '(' + embedded.length + ' bytes embedded)');
}

/* 本地服务器用的静态外部脚本（仅本机 loopback，无外部网络）*/
fs.writeFileSync(path.join(OUT, 'ok-script.js'), 'window.__okScript="ok-loaded";\n', 'utf8');
console.log('generated ok-script.js');
