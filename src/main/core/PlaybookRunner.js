'use strict'
/**
 * PlaybookRunner —— 按剧本在每个实例上跑一串步骤。
 *
 * 和主从同步的分工:
 *   同步引擎 = 人在主机上操作,从机跟着做(探索、登录、处理意外情况)
 *   剧本     = 完全确定的那几步(选颜色/容量/不要 AppleCare/加入购物袋)不需要人,直接跑
 * 抢购的典型用法是两者叠加:提前用剧本把所有实例推到"只差最后一下"的状态,
 * 到点由定时器统一开火,或者人在主机上点一下、其余跟上。
 *
 * 每个实例各跑各的(并发),一个实例卡住不拖累别人。
 * 点击一律走"DOM 定位 + 真实输入注入",不用 element.click()(那个 isTrusted 是 false)。
 *
 * 点击步骤的落点会漂移:页面重渲染、懒加载、弹层插入都会让元素在两次定位之间挪位置。
 * 所以一次定位的结果不会无条件拿来注入 —— 先和上一次落点比,偏差超过阈值就重新定位,
 * 用最新落点注入而不是打旧坐标。每次尝试(定位/注入/断言)都留一条记录进 trace,
 * 失败时把最后一次的定位策略与偏差一并带回,中控台才看得出是哪一步、漂了多少、试了几次。
 */

const InputCodec = require('./InputCodec.js')

const DEFAULT_STEP_TIMEOUT = 15000
/** 落点偏差超过这个 CSS 像素值,注入前先重新定位 */
const DEFAULT_DRIFT_THRESHOLD_PX = 3
/** 单次点击里因偏差额外重新定位的最多轮数 */
const DEFAULT_RELOCATE_MAX = 2
/** 首次点击前用来建立"上一次落点"的确认性定位间隔;0 表示只做一次即时复核(不加延时) */
const DEFAULT_DRIFT_PROBE_MS = 60
const DEFAULT_RELOCATE_DELAY_MS = 80
const DEFAULT_ASSERT_RETRY_DELAY_MS = 260
const DEFAULT_LOCATE_RETRY_DELAY_MS = 180

class PlaybookRunner {
  /**
   * @param {object} deps { rpc, logger, clock }
   */
  constructor(deps) {
    this.rpc = deps.rpc
    this.logger = deps.logger || { info() {}, warn() {}, error() {} }
    this.clock = deps.clock || null
    this.running = new Map()      // instanceId -> {abort:boolean}
    /**
     * 每个实例上每个目标"上一次落点"的记忆:instanceId -> Map<目标键, landing>。
     *
     * 有了它,一次性的点击步骤(没有重试、也不需要断言)同样能和上一次落点比偏差 ——
     * 比如剧本先把所有实例推到"只差最后一下",隔几秒再单独点"加入购物袋",
     * 中间页面重排过,偏差照样能被发现。只存坐标,不存 DOM 引用。
     */
    this._landings = new Map()
    this._landingCap = 256       // 每个实例最多记这么多个目标,防止长跑进程里无限增长
  }

  /**
   * @param {object} playbook { name, steps: [] }
   * @param {Array} instances 实例数组(要有 webContents / zoomFactor / id / navigate)
   * @param {object} [opts] { vars: {instanceId: {k:v}} }
   */
  async run(playbook, instances, opts = {}) {
    const t0 = Date.now()
    this.logger.info('开始执行剧本', { name: playbook.name, steps: playbook.steps.length, instances: instances.length })

    const results = await Promise.all(instances.map(inst => this._runOne(playbook, inst, opts)))

    const ok = results.filter(r => r.ok).length
    this.logger.info('剧本执行完毕', {
      name: playbook.name, ok, total: results.length, elapsedMs: Date.now() - t0,
      // 失败要能一眼看出是哪一步、哪一类(定位/注入/断言)、漂了多少、试了几次
      失败: results.filter(r => !r.ok).map(r =>
        `#${r.instanceId}@${r.failedAt}:${r.phase || '?'}:${r.reason}` +
        (r.strategy ? `[策略=${r.strategy}]` : '') +
        (r.driftPx != null ? `[偏差=${r.driftPx}px]` : '') +
        (r.attempts ? `[尝试=${r.attempts.length}]` : '')),
    })
    return { name: playbook.name, ok, total: results.length, elapsedMs: Date.now() - t0, results }
  }

  abort(instanceId) {
    const s = this.running.get(instanceId)
    if (s) s.abort = true
  }

  abortAll() { for (const s of this.running.values()) s.abort = true }

  async _runOne(playbook, inst, opts) {
    const token = { abort: false }
    this.running.set(inst.id, token)
    const vars = (opts.vars && opts.vars[inst.id]) || {}
    const trace = []

    try {
      for (let i = 0; i < playbook.steps.length; i++) {
        if (token.abort) return { instanceId: inst.id, ok: false, reason: 'aborted', failedAt: i, trace }
        const step = playbook.steps[i]
        const t0 = Date.now()
        const res = await this._step(step, inst, vars)
        const entry = {
          i, op: step.op, label: step.label || null, ok: res.ok,
          reason: res.reason, ms: Date.now() - t0,
        }

        // 点击步骤把定位/注入/断言的逐次尝试原样带进 trace ——
        // 中控台要看得见"试了几次、每次用的哪条策略、偏差多少、断言为什么不过",
        // 而不是只留一个字符串。
        if (res.attempts) entry.attempts = res.attempts
        if (res.phase) entry.phase = res.phase
        if (res.strategy) entry.strategy = res.strategy
        if (res.driftPx != null) entry.driftPx = res.driftPx
        if (res.cssX != null) { entry.cssX = res.cssX; entry.cssY = res.cssY }
        if (res.attemptCount != null) entry.attemptCount = res.attemptCount

        if (!res.ok && step.optional !== true) {
          trace.push(entry)
          return {
            instanceId: inst.id, ok: false, reason: res.reason, failedAt: i, step: step.op,
            // 最终失败时把最后一次的定位策略、偏差与落点一并带回
            phase: res.phase || null,
            strategy: res.strategy || null,
            driftPx: res.driftPx != null ? res.driftPx : null,
            cssX: res.cssX != null ? res.cssX : null,
            cssY: res.cssY != null ? res.cssY : null,
            attempts: res.attempts || null,
            trace,
          }
        }
        // 可选步骤失败:继续往下跑,但在 trace 里明确标成"跳过",不能混成成功
        if (!res.ok && step.optional === true) {
          entry.skipped = true
          entry.status = 'skipped'
        }
        trace.push(entry)
      }
      const skipped = trace.filter(t => t.skipped).map(t => t.i)
      return { instanceId: inst.id, ok: true, skipped, trace }
    } finally {
      this.running.delete(inst.id)
    }
  }

  async _step(step, inst, vars) {
    const wc = inst.webContents
    if (!wc || wc.isDestroyed()) return { ok: false, reason: 'destroyed' }

    switch (step.op) {
      case 'navigate': {
        const url = interpolate(step.url, vars)
        inst.navigate(url)
        // 等到 DOM 可用为止,而不是死等固定时间
        return this._waitForLoad(wc, step.timeoutMs || DEFAULT_STEP_TIMEOUT)
      }

      case 'waitFor':
        return this.rpc.call(wc, 'waitFor', {
          selector: step.selector, timeoutMs: step.timeoutMs || DEFAULT_STEP_TIMEOUT, visible: step.visible !== false,
        }, { timeoutMs: (step.timeoutMs || DEFAULT_STEP_TIMEOUT) + 1500 })

      case 'click':
        return this._click(inst, step)

      case 'fill': {
        const value = step.value != null ? interpolate(step.value, vars) : vars[step.var]
        if (value == null) return { ok: false, reason: 'no-value:' + (step.var || '') }
        return this.rpc.call(wc, 'fill', { selector: step.selector, value, clear: step.clear !== false })
      }

      case 'scrollTo':
        return this.rpc.call(wc, 'scrollTo', { x: step.x || 0, y: step.y || 0 })

      case 'sleep':
        await sleep(step.ms || 200)
        return { ok: true }

      case 'script': {
        // 明确的逃生舱:剧本表达不了的站点特例交给它。只在用户自己写的剧本里出现。
        try { const v = await wc.executeJavaScript(step.code, true); return { ok: true, value: v } }
        catch (err) { return { ok: false, reason: 'script-threw', error: String(err && err.message || err) } }
      }

      case 'reload':
        wc.reload()
        return this._waitForLoad(wc, step.timeoutMs || DEFAULT_STEP_TIMEOUT)

      case 'prime':
        return this.rpc.call(wc, 'prime', { key: step.key, descriptor: descriptorFor(step.selector) })

      default:
        return { ok: false, reason: 'unknown-op:' + step.op }
    }
  }

  /**
   * 定位 + 真实输入注入。这是剧本里唯一会产生"点击"的地方。
   *
   * 两段式没有变:DOM 定位拿落点 → 真实输入注入。变的是**落点的可信度管理**:
   *   - 每次定位成功都和上一次落点比偏差。页面重渲染/懒加载/弹层插入会让元素挪位置,
   *     偏差超过阈值就说明页面还在动 —— 这时先重新定位,用最新落点注入,而不是打旧坐标。
   *   - 断言失败的重试逐次记进 attempts:第几次、用的哪条定位策略、偏差多少、断言为什么不过。
   *   - 最终失败时把最后一次的策略与偏差带回结果,并把"定位失败"和"注入失败"分开报。
   *
   * 点完可以带断言重试:实站上单选框偶尔会漏选(页面正在重渲染、事件被吃掉),
   * 只发不看的话剧本会带着一个错误的配置继续往下跑,最后加进购物袋的是别的型号。
   */
  async _click(inst, step) {
    const wc = inst.webContents
    const descriptor = step.descriptor || descriptorFor(step.selector)
    const attempts = Math.max(1, step.retries == null ? (step.expectChecked ? 3 : 1) : step.retries + 1)
    const threshold = numberOr(step.driftThresholdPx, DEFAULT_DRIFT_THRESHOLD_PX)
    const relocateMax = Math.max(0, numberOr(step.relocateMax, DEFAULT_RELOCATE_MAX))
    const relocateDelay = numberOr(step.relocateDelayMs, DEFAULT_RELOCATE_DELAY_MS)
    const locateDelay = numberOr(step.locateRetryDelayMs, DEFAULT_LOCATE_RETRY_DELAY_MS)
    const assertDelay = numberOr(step.assertRetryDelayMs, DEFAULT_ASSERT_RETRY_DELAY_MS)

    /** 逐次尝试记录。每一条都要能回答:第几次、哪条策略、偏差多少、卡在哪。 */
    const records = []
    /** 上一次真正用于注入的落点。跨尝试一直保留,用来算下一次定位的偏差。 */
    let prevLanding = null
    /** 最近一次成功的定位。最终失败时即使不是它导致失败,也要把策略/偏差带回去。 */
    let lastLanding = null
    let lastFailure = null
    /** 这个目标上一次运行时的落点(可能是上一段剧本、甚至上一次开火),用来发现跨步骤的漂移 */
    const landingKey = step.landingKey || step.selector || (descriptor.path || JSON.stringify(descriptor))
    const remembered = this._getLanding(inst.id, landingKey)
    if (remembered) prevLanding = remembered
    /**
     * 首次点击没有"上一次落点"可比。默认先隔一小拍再定位一次,用两次定位互相校验;
     * 但"最后一下"(加入购物袋这类不回读断言的一次性点击)对延迟敏感,默认不做这次延时探测,
     * 到点开火的关键路径不能被多出来的 60ms 拖慢 —— 它靠上面那份跨步骤记忆来发现漂移。
     * 需要时可以在步骤上显式给 driftProbeMs。
     */
    const probeMs = Math.max(0, numberOr(
      step.driftProbeMs,
      (step.expectChecked || step.expectGone) ? DEFAULT_DRIFT_PROBE_MS : 0
    ))

    for (let attempt = 1; attempt <= attempts; attempt++) {
      const rec = { attempt, phase: null, ok: false, strategy: null, driftPx: null, relocations: 0 }

      /* ---------------------------------------------------------- 定位 */
      const located = await this._resolve(wc, descriptor, step)
      if (!located.ok) {
        rec.phase = 'locate'
        rec.ok = false
        rec.reason = 'locate-failed:' + located.reason
        rec.locateReason = located.reason          // not-found / ambiguous / offscreen / …
        rec.strategy = located.strategy || null    // offscreen/zero-size 这类失败也带着策略
        records.push(rec)
        lastFailure = { phase: 'locate', reason: rec.reason, strategy: rec.strategy, driftPx: null, attempt }
        await sleep(locateDelay)
        continue                               // 重新定位,不是直接打旧坐标
      }

      let landing = landingOf(located)
      lastLanding = landing

      /* -------------------------------------------- 偏差检查 / 重新定位 */
      // 偏差 = 本次落点与上一次落点的距离。超过阈值说明页面仍在移动,
      // 这一下要是照旧打,很可能正好落在元素挪走之前的空位上 —— 必须先重新定位。
      let drift = driftBetween(prevLanding, landing)

      // 没有上一次落点时(本步第一次点击),先隔一小拍再定位一次:
      // 这两次定位就是"本次 vs 上一次"的对照,页面正在重排时偏差会立刻暴露出来。
      if (!drift && probeMs > 0) {
        await sleep(probeMs)
        const probe = await this._resolve(wc, descriptor, step)
        if (probe.ok) {
          const p = landingOf(probe)
          drift = driftBetween(landing, p)
          landing = p
          lastLanding = p
        }
      }

      rec.strategy = landing.strategy
      rec.cssX = landing.cssX
      rec.cssY = landing.cssY
      rec.driftPx = drift ? drift.dist : null
      rec.dx = drift ? drift.dx : null
      rec.dy = drift ? drift.dy : null

      let relocateError = null
      // driftPx 记的是**触发重新定位的那次偏差**,是"这一下本来会打偏多远"的证据;
      // 重定位之后页面稳不稳,另记在 residualPx 里 —— 两个数回答的是两个不同的问题。
      let residual = drift
      while (drift && drift.dist > threshold && rec.relocations < relocateMax) {
        rec.relocations++
        await sleep(relocateDelay)              // 给重排/懒加载一点时间落地
        const again = await this._resolve(wc, descriptor, step)
        if (!again.ok) {
          // 重定位失败不推翻已经拿到的落点:记下来,用最近一次有效落点继续
          relocateError = again.reason
          break
        }
        // 重定位后再和**上一次落点**比:页面稳定了偏差才会落回阈值内
        drift = driftBetween(landing, landingOf(again))
        residual = drift
        landing = landingOf(again)
        lastLanding = landing
        rec.strategy = landing.strategy
        rec.cssX = landing.cssX
        rec.cssY = landing.cssY
      }
      rec.residualPx = residual ? residual.dist : null
      if (relocateError) rec.relocateError = relocateError

      /* ---------------------------------------------------------- 注入 */
      rec.phase = 'inject'
      const events = InputCodec.encode(
        { kind: 'mouse', action: 'click', clickCount: step.clickCount || 1 },
        // 用页面这次回报的 innerWidth 现算缩放,和坐标同源,不存在测量滞后
        { zoomFactor: zoomOf(inst, landing.vw), cssX: landing.cssX, cssY: landing.cssY }
      )
      try {
        for (const e of events) wc.sendInputEvent(e)
      } catch (err) {
        // 注入失败是"发不出去",和"找不到/找不准"是两回事,别混成同一个 reason
        rec.ok = false
        rec.reason = 'inject-failed'
        rec.error = String(err && err.message || err)
        rec.status = 'inject-failed'
        records.push(rec)
        return this._clickFailure(records,
          { phase: 'inject', reason: rec.reason, strategy: landing.strategy, driftPx: rec.driftPx }, lastLanding, attempt)
      }
      rec.status = 'injected'
      prevLanding = landing
      this._rememberLanding(inst.id, landingKey, landing)

      if (step.settleMs !== 0) await sleep(step.settleMs || 140)

      /* ---------------------------------------------------------- 断言 */
      if (!step.expectChecked && !step.expectGone) {
        rec.phase = 'inject'
        rec.ok = true
        records.push(rec)
        return this._clickSuccess(records, landing, attempt)
      }

      const assertion = await this.rpc.call(wc, 'assert', {
        selector: step.selector,
        checked: step.expectChecked ? true : undefined,
        exists: step.expectGone ? false : undefined,
      }, { timeoutMs: 1500 })

      if (assertion && assertion.ok) {
        rec.phase = 'assert'
        rec.ok = true
        records.push(rec)
        return this._clickSuccess(records, landing, attempt)
      }

      rec.phase = 'assert'
      rec.ok = false
      rec.assertReason = (assertion && assertion.reason) || 'assert-error'
      rec.reason = 'assert-failed:' + rec.assertReason
      rec.status = 'assert-failed'
      if (!assertion) rec.assertTimeout = true
      records.push(rec)
      lastFailure = { phase: 'assert', reason: rec.reason, strategy: landing.strategy, driftPx: rec.driftPx, attempt }
      await sleep(assertDelay)                  // 给页面一点重渲染的时间再重试
    }

    // 所有尝试都用完了。把最后一次的定位策略与偏差带回结果。
    return this._clickFailure(records,
      lastFailure || { phase: 'locate', reason: 'locate-failed:unknown' }, lastLanding, attempts)
  }

  /* ------------------------------------------------------------ 落点记忆 */

  _getLanding(instanceId, key) {
    const m = this._landings.get(instanceId)
    return m ? (m.get(key) || null) : null
  }

  _rememberLanding(instanceId, key, landing) {
    let m = this._landings.get(instanceId)
    if (!m) { m = new Map(); this._landings.set(instanceId, m) }
    // Map 保持插入顺序:超上限就丢最早的那个,近似 LRU 的效果就够了
    if (m.has(key)) m.delete(key)
    else if (m.size >= this._landingCap) m.delete(m.keys().next().value)
    m.set(key, { strategy: landing.strategy, cssX: landing.cssX, cssY: landing.cssY, vw: landing.vw })
  }

  _clickSuccess(records, landing, attempt) {    const lastRec = records[records.length - 1]
    return {
      ok: true,
      attempt,
      strategy: landing.strategy,
      cssX: landing.cssX, cssY: landing.cssY,
      retargeted: landing.retargeted,
      driftPx: lastRec ? lastRec.driftPx : null,
      attempts: records,
    }
  }

  /** 失败结果统一带上:失败的阶段、最后一次定位策略、偏差、逐次尝试记录 */
  _clickFailure(records, failure, lastLanding, attempt) {
    const lastRec = records[records.length - 1] || {}
    return {
      ok: false,
      reason: failure.reason,
      phase: failure.phase,                 // locate | inject | assert —— 三类失败可区分
      attempt,
      attemptCount: records.length,
      strategy: failure.strategy || (lastLanding && lastLanding.strategy) || lastRec.strategy || null,
      driftPx: failure.driftPx != null ? failure.driftPx
        : (lastRec.driftPx != null ? lastRec.driftPx : null),
      cssX: lastLanding ? lastLanding.cssX : (lastRec.cssX != null ? lastRec.cssX : null),
      cssY: lastLanding ? lastLanding.cssY : (lastRec.cssY != null ? lastRec.cssY : null),
      retargeted: lastLanding ? lastLanding.retargeted : null,
      attempts: records,
    }
  }

  /** 定位一次。任何"没拿到可用坐标"的情况都在这里归成定位失败。 */
  async _resolve(wc, descriptor, step) {
    const r = await this.rpc.call(wc, 'resolve', { descriptor, autoScroll: true },
      { timeoutMs: step.timeoutMs || 4000 })
    if (!r) return { ok: false, reason: 'no-result' }
    if (!r.ok) return r
    if (r.cssX == null || r.cssY == null) return { ok: false, reason: 'no-coords', strategy: r.strategy }
    return r
  }

  _waitForLoad(wc, timeoutMs) {
    return new Promise(resolve => {
      let done = false
      const finish = (ok, reason) => {
        if (done) return
        done = true
        clearTimeout(timer)
        wc.off('did-finish-load', onOk)
        wc.off('did-fail-load', onFail)
        resolve({ ok, reason })
      }
      const onOk = () => finish(true)
      const onFail = (_e, code, desc, url, isMainFrame) => { if (isMainFrame && code !== -3) finish(false, `${desc}(${code})`) }
      const timer = setTimeout(() => finish(false, 'load-timeout'), timeoutMs)
      wc.once('did-finish-load', onOk)
      wc.on('did-fail-load', onFail)
    })
  }
}

/** 两个落点之间的偏差。没有上一次落点(首次)时返回 null —— 那不是漂移,是无从比较。 */
function driftBetween(prev, next) {
  if (!prev || !next) return null
  if (prev.cssX == null || prev.cssY == null || next.cssX == null || next.cssY == null) return null
  const dx = next.cssX - prev.cssX
  const dy = next.cssY - prev.cssY
  return { dx: +dx.toFixed(2), dy: +dy.toFixed(2), dist: +Math.hypot(dx, dy).toFixed(2) }
}

/** 从 resolve 的返回值里取出"这次该打到哪"的最小快照 */
function landingOf(r) {
  return {
    strategy: r.strategy,
    cssX: r.cssX,
    cssY: r.cssY,
    vw: r.vw,
    retargeted: r.retargetedToLabel,
  }
}

/** 取一个有限数,否则用默认值(0 是合法值,不能用 `||`) */
function numberOr(v, fallback) {
  const n = Number(v)
  return Number.isFinite(n) ? n : fallback
}

/** 坐标换算只许用实际生效的缩放:优先按页面回报的 innerWidth 现算 */
function zoomOf(inst, vw) {
  const w = inst.bounds ? inst.bounds.w : inst.boundsW
  if (vw > 0 && w > 0) return w / vw
  if (typeof inst.actualZoomFactor === 'function') return inst.actualZoomFactor()
  return inst.zoomFactor == null ? 1 : inst.zoomFactor
}

/** 由 CSS 选择器造一个最小 descriptor,让 Locator 的 path / testAttr / id 策略能用上 */
function descriptorFor(selector) {
  const s = String(selector || '').trim()
  const idM = /^#([\w-]+)$/.exec(s)
  const attrM = /^\[([\w-]+)="([^"]+)"\]$/.exec(s)
  return {
    tag: '*',
    id: idM ? idM[1] : null,
    testAttr: attrM ? { attr: attrM[1], value: attrM[2] } : null,
    path: s,
    offset: { x: 0.5, y: 0.5 },
    size: null,
  }
}

/** 把 {{varName}} 替换成实例变量 */
function interpolate(tpl, vars) {
  if (typeof tpl !== 'string') return tpl
  return tpl.replace(/\{\{(\w+)\}\}/g, (_m, k) => (vars[k] != null ? String(vars[k]) : ''))
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }

module.exports = { PlaybookRunner, descriptorFor, interpolate, driftBetween }
