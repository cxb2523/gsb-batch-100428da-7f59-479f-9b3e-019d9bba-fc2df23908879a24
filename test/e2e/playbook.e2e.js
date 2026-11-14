'use strict'
/**
 * 剧本执行链路端到端验证 —— 跑真实 GuestRpc / guest preload / Locator / InputCodec / PlaybookRunner
 * 和真实页面(本地自造的静态页,不联网)。
 *
 * 为什么用一个 BrowserWindow 当"实例":剧本执行只依赖实例的三样东西
 * —— webContents、物理宽度、导航。view 的宿主形态不影响这条链路。
 * (WebContentsView 在无桌面合成的会话里布局会一直停在 1×1,那是宿主形态的问题,不是剧本的问题。)
 *
 * 覆盖四条验收:
 *   ① 会重排的按钮:定位后页面把按钮推开,剧本必须先重新定位再用新坐标注入,并且真的点中
 *   ② 断言始终不过的选项:trace 里逐次保留尝试记录(第几次 / 策略 / 偏差 / 断言原因)
 *   ③ 可选步骤失败:标记为跳过而不是成功,且后续步骤继续执行
 *   ④ 永不出现的按钮(定位失败)与注入失败必须能区分开
 *
 * 点击依然只走 sendInputEvent(页面记录到的 isTrusted 必须为 true),没有任何 element.click()。
 *
 * 跑法:npm run test:e2e:playbook
 */
const { app, BrowserWindow } = require('electron')
const path = require('path')

const { GuestRpc } = require('../../src/main/core/GuestRpc.js')
const { PlaybookRunner } = require('../../src/main/core/PlaybookRunner.js')

const GUEST_PRELOAD = path.join(__dirname, '../../src/preload/guest.js')

const results = []
const sleep = ms => new Promise(r => setTimeout(r, ms))
function check(name, cond, detail) {
  results.push({ name, pass: !!cond })
  console.log(`${cond ? '  ✔' : '  ✖'} ${name}${detail !== undefined ? '   ' + JSON.stringify(detail) : ''}`)
}
/** 轮询等到条件成立;超时返回 false,让用例继续把失败信息打印出来 */
async function waitFor(fn, timeoutMs, label) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    try { if (await fn()) return true } catch (e) { /* 页面还没就绪 */ }
    await sleep(120)
  }
  console.log(`  (等待超时: ${label || 'condition'} 超过 ${timeoutMs}ms)`)
  return false
}

/**
 * 自造页面。刻意做得很矮(不滚动),这样"重排"只体现为按钮纵向位移,
 * 不会掺进 scrollIntoView 的滚动量,偏差数字才好断言。
 */
function benchPage() {
  return `<!doctype html><html><head><meta charset="utf-8"><title>playbook-bench</title><style>
    body{margin:0;font:15px system-ui}
    #banner{height:0;background:#eef;overflow:hidden}
    .buy{padding:12px 32px;font-size:16px;background:#e11d48;color:#fff;border:0;border-radius:8px;cursor:pointer}
    .row{padding:18px}
  </style></head><body>
    <div id="banner">活动横幅(它一变高,下面的按钮就被推走)</div>
    <div class="row">
      <button id="drift-btn" class="buy" data-testid="drift-btn">立即抢购</button>
      <label style="display:flex;gap:8px;align-items:center;margin-top:26px;font-size:14px">
        <input type="radio" id="never-check" data-testid="never-check" name="opt">
        <span>永远不会被选中的选项</span>
      </label>
      <div style="margin-top:22px">
        <button id="continuation" data-testid="continuation">后续步骤</button>
      </div>
    </div>
    <script>
      window.__hits = []
      document.getElementById('drift-btn').addEventListener('click', e => {
        window.__hits.push({ id: 'drift-btn', trusted: e.isTrusted, x: e.clientX, y: e.clientY })
      })
      document.getElementById('continuation').addEventListener('click', e => {
        window.__hits.push({ id: 'continuation', trusted: e.isTrusted, x: e.clientX, y: e.clientY })
      })
      // 这一下永远选不中:捕获阶段掐掉默认行为,单选框的 checked 不会变
      document.getElementById('never-check').addEventListener('click', e => {
        e.preventDefault(); e.stopPropagation()
      }, true)

      window.__reflow = px => { document.getElementById('banner').style.height = px + 'px' }
      window.__pos = id => {
        const r = document.getElementById(id).getBoundingClientRect()
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 }
      }
      window.__hitsOf = id => window.__hits.filter(h => h.id === id)
    </script>
  </body></html>`
}

app.on('window-all-closed', () => { /* 用例自己控制退出 */ })

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1200, height: 900, show: true, backgroundColor: '#7A92A0',
    webPreferences: {
      preload: GUEST_PRELOAD,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      backgroundThrottling: false,
    },
  })
  const wc = win.webContents
  const rpc = new GuestRpc({ logger: { warn() {}, debug() {} } })
  const runner = new PlaybookRunner({ rpc, logger: { info() {}, warn() {}, error() {} } })

  await win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(benchPage()))
  // 缩放 ≠ 1:坐标必须按页面回报的 innerWidth 现算才能落对,否则这一步就会打偏
  wc.setZoomFactor(0.8)
  win.focus()
  await waitFor(async () => (await wc.executeJavaScript('document.readyState')) === 'complete', 8000, '页面就绪')
  await sleep(700)

  const js = code => wc.executeJavaScript(code)
  const contentW = win.getContentBounds().width
  const innerW = await js('innerWidth')
  const zoom = contentW / innerW

  // 剧本执行只认这三样:webContents、物理宽度、导航
  const inst = {
    id: 1,
    webContents: wc,
    bounds: { x: 0, y: 0, w: contentW, h: win.getContentBounds().height },
    actualZoomFactor: () => contentW / innerW,
    navigate() {},
  }

  check('页面已加载,缩放 ≠ 1(坐标换算必须走现算路径)',
    innerW > contentW && zoom > 0.7 && zoom < 0.9,
    { 物理宽: contentW, innerWidth: innerW, zoom: +zoom.toFixed(4) })

  const resetHits = () => js('window.__hits.length = 0')

  /* ════════════════ ① 漂移:定位后按钮被推走,重定位后必须点中 ════════════════ */
  await js('window.__reflow(0)')
  await resetHits()
  const posBefore = await js('window.__pos("drift-btn")')

  // 步开始后 25ms 把 banner 拉高 —— 这就是"定位成功之后页面才重排"的真实时序
  await js('setTimeout(() => window.__reflow(260), 25); 1')
  const driftRun = await runner.run({
    name: 'drift',
    steps: [{
      op: 'click', selector: '#drift-btn', label: '立即抢购',
      driftThresholdPx: 3, driftProbeMs: 60, relocateDelayMs: 40, settleMs: 60,
    }],
  }, [inst])

  const dr = driftRun.results[0]
  const drRec = dr.trace[0]
  const posAfter = await js('window.__pos("drift-btn")')
  const driftHits = await js('window.__hitsOf("drift-btn")')

  check('① 漂移场景剧本整体成功', driftRun.ok === 1 && dr.ok === true,
    { ok: driftRun.ok, total: driftRun.total })
  check('① 页面确实在步内重排了(按钮被推走)',
    Math.abs(posAfter.y - posBefore.y) > 200, { 之前Y: posBefore.y, 之后Y: posAfter.y })
  check('① trace 记录到超过阈值的偏差', drRec.driftPx != null && drRec.driftPx > 200,
    { driftPx: drRec.driftPx })
  check('① 偏差触发过重新定位', (drRec.attempts[0].relocations || 0) >= 1,
    { relocations: drRec.attempts[0].relocations })
  check('① 注入的是重定位后的新落点(不是旧坐标)',
    Math.abs(drRec.cssY - posAfter.y) < 2 && Math.abs(drRec.cssY - posBefore.y) > 200,
    { 注入Y: drRec.cssY, 旧Y: posBefore.y, 新Y: posAfter.y })
  check('① 真的点中了按钮', driftHits.length === 1, { hits: driftHits })
  check('① 注入事件是可信事件(isTrusted=true,没有 element.click())',
    driftHits.length === 1 && driftHits[0].trusted === true)
  check('① 命中位置就是重定位后的位置',
    driftHits.length === 1 && Math.abs(driftHits[0].y - posAfter.y) < 3,
    { 命中Y: driftHits[0] && driftHits[0].y, 落点Y: posAfter.y })

  /* ════════════ ② 断言始终不过:trace 逐次保留尝试记录 ════════════ */
  await resetHits()
  const assertRun = await runner.run({
    name: 'assert-fail',
    steps: [{
      op: 'click', selector: '#never-check', label: '选一个永远选不中的选项',
      expectChecked: true, driftProbeMs: 20, assertRetryDelayMs: 60, settleMs: 20,
    }],
  }, [inst])
  const ar = assertRun.results[0]
  const aRec = ar.trace[0]

  check('② 断言失败使剧本失败(没有假装成功)', assertRun.ok === 0 && ar.ok === false,
    { ok: assertRun.ok, reason: ar.reason })
  check('② trace 里保留了多次尝试', aRec.attempts && aRec.attempts.length === 3,
    { attempts: aRec.attempts && aRec.attempts.length })
  check('② 每次尝试都记了序号、策略、偏差、断言原因',
    aRec.attempts.every((x, i) =>
      x.attempt === i + 1 &&
      typeof x.strategy === 'string' &&
      (x.driftPx === null || typeof x.driftPx === 'number') &&
      x.assertReason === 'not-checked'),
    { 明细: aRec.attempts.map(x => ({ attempt: x.attempt, strategy: x.strategy, driftPx: x.driftPx, assertReason: x.assertReason })) })
  check('② 失败结果带回最后一次的定位策略与偏差',
    typeof ar.strategy === 'string' && 'driftPx' in ar && ar.phase === 'assert',
    { phase: ar.phase, strategy: ar.strategy, driftPx: ar.driftPx })
  check('② 单选框确实一直没被选中(断言不是误判)',
    (await js('!!document.getElementById("never-check").checked')) === false)

  /* ════════════ ③ 可选步骤失败 = 跳过,后续步骤继续 ════════════ */
  await resetHits()
  const optRun = await runner.run({
    name: 'optional',
    steps: [
      { op: 'click', selector: '#never-exists', label: '永不存在的按钮', optional: true, locateRetryDelayMs: 10 },
      { op: 'click', selector: '#continuation', label: '后续步骤', driftProbeMs: 0, settleMs: 30 },
    ],
  }, [inst])
  const or = optRun.results[0]
  const optTrace = or.trace[0]
  const contHits = await js('window.__hitsOf("continuation")')

  check('③ 可选步骤失败不终止剧本', optRun.ok === 1 && or.ok === true,
    { ok: optRun.ok, reason: or.reason })
  check('③ 可选步骤在 trace 里被标为跳过(不是成功)',
    optTrace.ok === false && optTrace.skipped === true && optTrace.status === 'skipped' &&
    Array.isArray(or.skipped) && or.skipped[0] === 0,
    { ok: optTrace.ok, skipped: optTrace.skipped, status: optTrace.status, skippedIndex: or.skipped })
  check('③ 后续步骤继续执行并点中', contHits.length === 1 && contHits[0].trusted === true,
    { hits: contHits })
  check('③ trace 保留可选步骤的失败原因',
    optTrace.reason === 'locate-failed:not-found' && optTrace.attempts.length === 1,
    { reason: optTrace.reason })

  /* ════════════ ④ 定位失败 vs 注入失败,原因必须分开 ════════════ */
  const locateRun = await runner.run({
    name: 'locate-missing',
    steps: [{ op: 'click', selector: '#never-exists', retries: 0, locateRetryDelayMs: 10 }],
  }, [inst])
  const lr = locateRun.results[0]
  check('④ 永不出现的按钮报「定位失败」而不是 click-failed',
    lr.ok === false && lr.phase === 'locate' && lr.reason === 'locate-failed:not-found',
    { phase: lr.phase, reason: lr.reason, attempts: lr.attempts && lr.attempts.length })

  // 注入失败:定位是成功的,只让 sendInputEvent 抛错(模拟注入通道坏掉)
  await resetHits()
  const origSend = wc.sendInputEvent.bind(wc)
  wc.sendInputEvent = () => { throw new Error('模拟注入通道故障') }
  let injectRun
  try {
    injectRun = await runner.run({
      name: 'inject-fail',
      steps: [{ op: 'click', selector: '#drift-btn', driftProbeMs: 0 }],
    }, [inst])
  } finally {
    wc.sendInputEvent = origSend
  }
  const ir = injectRun.results[0]
  check('④ 注入失败单独归类为 inject-failed(与定位失败区分)',
    ir.ok === false && ir.phase === 'inject' && ir.reason === 'inject-failed',
    { phase: ir.phase, reason: ir.reason })
  check('④ 注入失败时仍保留已经定位成功的信息',
    typeof ir.strategy === 'string' && ir.cssX != null && ir.cssY != null,
    { strategy: ir.strategy, cssX: ir.cssX, cssY: ir.cssY })
  check('④ 注入失败没有任何点击落到页面上',
    (await js('window.__hitsOf("drift-btn").length')) === 0)

  const failed = results.filter(r => !r.pass)
  console.log(`\n  ${results.length - failed.length}/${results.length} 通过`)
  app.exit(failed.length ? 1 : 0)
}).catch(e => { console.error('E2E FATAL', e && e.stack || e); app.exit(1) })
