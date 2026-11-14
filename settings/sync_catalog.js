/** Sync the focused Apify and CoreClaw catalogs used by this repository. */
const fs = require('fs');
const path = require('path');
const https = require('https');

const ROOT = process.env.SYNC_ROOT || path.join(__dirname, '..');
const config = JSON.parse(fs.readFileSync(path.join(__dirname, 'repo.config.json'), 'utf8'));
const REPORT_PATH = path.join(ROOT, 'catalog', 'sync-report.json');

function fetchText(url) {
  return new Promise((resolve, reject) => {
    https.get(url, { headers: { 'User-Agent': 'API-directory-sync/1.0' } }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        if (res.statusCode !== 200) return reject(new Error(`${res.statusCode} while fetching ${url}`));
        resolve(body);
      });
    }).on('error', reject);
  });
}

function writeIfChanged(filePath, content) {
  const previous = fs.existsSync(filePath) ? fs.readFileSync(filePath, 'utf8') : null;
  if (previous === content) return false;
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content, 'utf8');
  return true;
}

async function syncOne(url, destination, heading) {
  const record = { catalog: heading, source: url, destination, outcome: null, error: null };
  const filePath = path.join(ROOT, destination);
  try {
    const content = await fetchText(url);
    const stamped = `<!-- Synced from ${url} -->\n\n${content.trim()}\n`;
    const changed = writeIfChanged(filePath, stamped);
    record.outcome = changed ? 'updated' : 'unchanged';
    console.log(`${heading}: ${record.outcome}`);
  } catch (error) {
    record.error = error.message;
    if (fs.existsSync(filePath)) {
      record.outcome = 'kept-existing';
      console.warn(`${heading}: ${error.message}; keeping the existing catalog`);
    } else {
      record.outcome = 'failed';
      console.error(`${heading}: ${error.message}; no existing catalog to keep`);
    }
  }
  return record;
}

function writeReport(records) {
  const failed = records.filter((r) => r.outcome === 'failed' || r.outcome === 'kept-existing');
  const report = {
    generatedAt: new Date().toISOString(),
    status: failed.length === 0 ? 'ok' : 'failed',
    catalogs: records,
  };
  fs.mkdirSync(path.dirname(REPORT_PATH), { recursive: true });
  fs.writeFileSync(REPORT_PATH, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  console.log(`Sync report written to ${path.relative(ROOT, REPORT_PATH)} (status: ${report.status})`);
  return report;
}

async function main() {
  const records = [];
  records.push(await syncOne(config.megaListSource, 'catalog/README.md', 'Apify catalog'));
  records.push(await syncOne(config.coreclawSource, 'catalog/coreclaw.md', 'CoreClaw catalog'));
  const report = writeReport(records);
  if (report.status !== 'ok') {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
