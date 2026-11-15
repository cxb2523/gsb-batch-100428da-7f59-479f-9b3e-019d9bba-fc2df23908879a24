/* 极简本地静态服务器：
   - /          → tests/local/fixtures（加密测试夹具、ok-script.js）
   - /repo/...  → 仓库根（供原始 index.html 走 http 加载，避免 file:// 差异）
   只监听 127.0.0.1，缺失文件返回 404，不访问任何外部网络。 */
const http = require('http');
const fs = require('fs');
const path = require('path');

const FIXTURE_ROOT = path.join(__dirname, 'fixtures');
const REPO_ROOT = path.join(__dirname, '..', '..');
const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.json': 'application/json'
};

function safeJoin(root, urlPath) {
  const target = path.join(root, urlPath);
  return target.startsWith(root) ? target : null;
}

const server = http.createServer((req, res) => {
  const urlPath = decodeURIComponent(req.url.split('?')[0]);
  let filePath;
  if (urlPath.startsWith('/repo/')) {
    filePath = safeJoin(REPO_ROOT, urlPath.slice('/repo/'.length));
  } else {
    filePath = safeJoin(FIXTURE_ROOT, urlPath);
  }
  if (!filePath) { res.writeHead(403); res.end('forbidden'); return; }
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('not found: ' + urlPath);
      return;
    }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(filePath)] || 'application/octet-stream' });
    res.end(data);
  });
});

server.listen(process.env.PSB_PORT ? Number(process.env.PSB_PORT) : 0, '127.0.0.1', () => {
  process.stdout.write(String(server.address().port));
});
