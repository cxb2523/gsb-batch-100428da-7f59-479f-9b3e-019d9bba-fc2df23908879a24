'use strict';

/**
 * Repro + regression test: elements removed by a page re-render between
 * discovery and click must be marked attempted and skipped — recorded in the
 * step log (report.json / flow.md), not just logs.txt — while the exploration
 * continues with the next untried element in the same step. Element-level
 * failures must not count toward the consecutive-error abort.
 *
 * Run: node tests/stale-element.test.js
 */

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { pathToFileURL } = require('url');
const { chromium } = require('playwright');

const { explore } = require('../agent/explorer');
const { Screenshotter } = require('../agent/screenshotter');
const { Logger } = require('../utils/logger');
const { buildNarrative } = require('../agent/flowNarrator');

const FIXTURE = path.join(__dirname, 'fixtures', 'stale-element', 'index.html');

async function runCoverage(coverage) {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), `runsight-stale-${coverage}-`));
  const logger = new Logger(outDir, '.');
  const screenshotter = new Screenshotter(outDir);
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    const page = await context.newPage();
    const videoT0 = Date.now();
    await page.goto(pathToFileURL(FIXTURE).href, { waitUntil: 'networkidle', timeout: 15000 });

    const result = await explore(page, {
      maxSteps: 1, mode: 'heuristic', coverage, present: false, guide: null, videoT0
    }, logger, screenshotter);

    logger.save(result.summary);
    const narrative = buildNarrative(result, { mode: 'heuristic', coverage });
    narrative.write(outDir);

    return { result, logger, outDir };
  } finally {
    await browser.close();
  }
}

function checkCoverage(coverage, { result, logger, outDir }) {
  const skipped = result.steps.filter(s => s.kind === 'skipped');
  const actions = result.steps.filter(s => s.kind === 'action');

  // 1. The three vanishing buttons were each marked attempted and skipped,
  //    with a failure reason, in the step record.
  assert.strictEqual(skipped.length, 3, `${coverage}: expected 3 skipped steps, got ${skipped.length}`);
  for (const s of skipped) {
    assert.ok(s.action.includes('Skipped'), `${coverage}: skipped step action should say Skipped: ${s.action}`);
    assert.ok(s.error && s.error.length > 0, `${coverage}: skipped step must carry the failure reason`);
    assert.strictEqual(s.status, 'skipped', `${coverage}: skipped step status`);
    // Timeline fields still measured from the video origin.
    assert.ok(typeof s.tMs === 'number' && s.tMs >= 0, `${coverage}: skipped step tMs from video origin`);
    assert.ok(typeof s.tStartMs === 'number' && s.tStartMs >= 0, `${coverage}: skipped step tStartMs from video origin`);
    assert.ok(s.tMs >= s.tStartMs, `${coverage}: tMs should be at/after tStartMs`);
  }

  // 2. The run kept going within the same step and clicked the surviving link.
  assert.strictEqual(actions.length, 1, `${coverage}: expected 1 successful action, got ${actions.length}`);
  assert.ok(actions[0].action.includes('About'), `${coverage}: fallback action should be the About link: ${actions[0].action}`);
  assert.strictEqual(actions[0].navigated, true, `${coverage}: About click should navigate`);
  assert.ok(
    result.steps.indexOf(actions[0]) > result.steps.indexOf(skipped[2]),
    `${coverage}: the fallback action happens after the skips, in the same step`
  );
  assert.strictEqual(actions[0].step, skipped[0].step, `${coverage}: retry stays within the same step number`);

  // 3. Three consecutive element failures did NOT trigger the error abort —
  //    proven by the success above, and no page-level errors were logged.
  assert.deepStrictEqual(logger.errors, [], `${coverage}: no page-level errors expected, got ${JSON.stringify(logger.errors)}`);

  // 4. Summary truthfully includes the skipped attempts.
  assert.ok(result.summary.includes('Skipped'), `${coverage}: summary should mention skipped elements`);

  // 5. report.json steps + summary include the failures.
  const report = JSON.parse(fs.readFileSync(path.join(outDir, 'report.json'), 'utf8'));
  const reportSkipped = report.steps.filter(s => s.action.includes('Skipped'));
  assert.strictEqual(reportSkipped.length, 3, `${coverage}: report.json should list 3 skipped steps`);
  for (const s of reportSkipped) {
    assert.ok(s.detail && s.detail.length > 0, `${coverage}: report.json skipped step needs the reason in detail`);
  }
  assert.ok(report.steps.some(s => s.action.includes('About')), `${coverage}: report.json should list the About action`);
  assert.ok(report.summary.includes('Skipped'), `${coverage}: report.json summary should mention skips`);

  // 6. flow.md walkthrough + summary include the failures.
  const flow = fs.readFileSync(path.join(outDir, 'flow.md'), 'utf8');
  const skippedLines = flow.split('\n').filter(l => l.includes('⚠️') && l.includes('Skipped'));
  assert.strictEqual(skippedLines.length, 3, `${coverage}: flow.md walkthrough should list 3 skipped steps`);
  assert.ok(flow.includes('About'), `${coverage}: flow.md should include the About action`);
  assert.ok(flow.includes('Skipped'), `${coverage}: flow.md summary should mention skips`);

  // 7. Skipped steps produce no captions/zooms (nothing happened on camera),
  //    so the video timeline stays aligned with real actions only.
  const { captions, zooms } = buildNarrative(result, { mode: 'heuristic', coverage });
  assert.strictEqual(captions.length, 1, `${coverage}: only the real action gets a caption`);
  assert.strictEqual(zooms.length, 0, `${coverage}: no zooms without on-camera points`);

  // 8. logs.txt still shows the failures too (human-readable trail).
  const logs = fs.readFileSync(path.join(outDir, 'logs.txt'), 'utf8');
  assert.ok(logs.includes('Skipped'), `${coverage}: logs.txt should mention skipped elements`);
}

(async () => {
  for (const coverage of ['happy', 'full']) {
    const run = await runCoverage(coverage);
    checkCoverage(coverage, run);
    console.log(`✓ ${coverage} coverage: stale elements skipped, recorded, and exploration continued`);
    fs.rmSync(run.outDir, { recursive: true, force: true });
  }
  console.log('\nAll stale-element tests passed.');
})().catch((err) => {
  console.error('TEST FAILED:', err);
  process.exit(1);
});
