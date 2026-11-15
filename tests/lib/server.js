/* Minimal zero-dependency static file server for the local test pages. */
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
};

function createServer(rootDir) {
  return http.createServer((req, res) => {
    const urlPath = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const rel = urlPath === '/' ? '/index.html' : urlPath;
    const filePath = path.normalize(path.join(rootDir, rel));
    if (!filePath.startsWith(path.normalize(rootDir))) {
      res.writeHead(403);
      res.end('forbidden');
      return;
    }
    fs.readFile(filePath, (err, data) => {
      if (err) {
        res.writeHead(404, { 'Content-Type': 'text/plain' });
        res.end('not found: ' + rel);
        return;
      }
      res.writeHead(200, { 'Content-Type': MIME[path.extname(filePath)] || 'application/octet-stream' });
      res.end(data);
    });
  });
}

function start(rootDir) {
  return new Promise((resolve) => {
    const server = createServer(rootDir);
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}

module.exports = { createServer, start };

if (require.main === module) {
  const rootDir = path.resolve(process.argv[2] || path.join(__dirname, '..', 'www-root'));
  start(rootDir).then(({ server, port }) => {
    console.log('serving ' + rootDir + ' at http://127.0.0.1:' + port);
    process.on('SIGINT', () => server.close(() => process.exit(0)));
  });
}
