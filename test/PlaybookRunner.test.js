'use strict'
/**
 * PlaybookRunner 点击链路的单元测试(纯 Node,不需要 Electron)。
 *
 * 用假的 rpc 精确编排 resolve / assert 的返回,验证:
 *   - 落点漂移超过阈值时先重新定位,再用新坐标注入(而不是打旧坐标)
 *   - 断言失败的重试逐次留痕(第几次、策略、偏差、断言原因)
 *   - 可选步骤失败标为跳过后继续执行
 *   - 定位失败与注入失败的原因分开报
 */
const { test } = require('node:test')
const a = require('node:assert')

const { PlaybookRunner, driftBetween, descriptorFor } = require('../src/main/core/PlaybookRunner.js')

/** 只实现 rpc.call 的假通道。responses[method] 是返回序列,用尽后重复最后一条。 */
function makeRpc(responses) {
  const idx = {}
  const calls = []
  return {
    calls,
    async call(wc, method, params) {
      calls.push({ method, params })
      const seq = responses[method]
      if (!seq || !seq.length) return { ok: true }
      const i = idx[method] == null ? 0 : idx[method]
      idx[method] = Math.min(i + 1, seq.length - 1)
      const v = seq[i]
      return typeof v === 'function' ? v(params) : v
    },
    count(method) { return calls.filter(c => c.method === method).length },
  }
}

function makeInst(overrides = {}) {
  const events = []
  const wc = {
    isDestroyed: () => false,
    sendInputEvent: e => events.push(e),
  }
  const inst = {
    id: 7,
    bounds: { x: 0, y: 0, w: 660, h: 880 },
    actualZoomFactor: () => 1,
    webContents: wc,
    navigate() {},
    ...overrides,
  }
  inst.events = events
  return inst
}

const resolveAt = (cssX, cssY, strategy = 'testAttr', vw = 660) =>
  ({ ok: true, strategy, cssX, cssY, vw, retargetedToLabel: false })

test('落点偏差按欧氏距离计算;没有上一次落点时不算漂移', () => {
  a.strictEqual(driftBetween(null, resolveAt(1, 1)), null, '首次定位没有可比对象')
  const d = driftBetween({ cssX: 100, cssY: 100 }, { cssX: 103, cssY: 104 })
  a.strictEqual(d.dx, 3)
  a.strictEqual(d.dy, 4)
  a.strictEqual(d.dist, 5)
})

test('漂移超过阈值:先重新定位,再用新落点注入', async () => {
  // 第一次定位 A,确认性重定位时页面已经重排到 B —— 必须重新定位到 B 再打
  const rpc = makeRpc({
    resolve: [resolveAt(100, 200), resolveAt(260, 200), resolveAt(260, 200)],
    assert: [{ ok: true }],
  })
  const inst = makeInst()
  const runner = new PlaybookRunner({ rpc })
  const step = {
    op: 'click', selector: '#buy', expectChecked: true,
    driftThresholdPx: 3, driftProbeMs: 10, relocateDelayMs: 1, assertRetryDelayMs: 1,
  }

  const res = await runner._step(step, inst, {})

  a.strictEqual(res.ok, true)
  a.strictEqual(res.strategy, 'testAttr')
  a.strictEqual(res.cssX, 260, '注入用的是重定位后的新坐标,不是旧坐标 A')
  a.strictEqual(res.attempts.length, 1)
  a.strictEqual(res.attempts[0].driftPx, 160)
  a.ok(res.attempts[0].relocations >= 1, '记录发生过重定位')
  // 注入坐标 = 260 * zoom(1)
  const dbl = inst.events.filter(e => e.type === 'mouseDown')
  a.strictEqual(dbl.length, 1)
  a.strictEqual(dbl[0].x, 260)
})

test('偏差在阈值内不重定位:一次定位一次注入', async () => {
  const rpc = makeRpc({
    resolve: [resolveAt(100, 100), resolveAt(101, 100)],
    assert: [{ ok: true }],
  })
  const inst = makeInst()
  const runner = new PlaybookRunner({ rpc })
  const res = await runner._step({
    op: 'click', selector: '#buy', expectChecked: true, driftProbeMs: 5, locateRetryDelayMs: 1,
  }, inst, {})

  a.strictEqual(res.ok, true)
  a.strictEqual(res.attempts[0].relocations, 0)
  a.strictEqual(rpc.count('resolve'), 2, '只有初始定位 + 确认性重定位')
  a.strictEqual(inst.events.filter(e => e.type === 'mouseDown').length, 1)
})

test('断言失败的重试逐次留痕,最终失败带回最后一次策略与偏差', async () => {
  const rpc = makeRpc({
    // 每次定位都挪一点,让偏差有值;断言始终不过
    resolve: [resolveAt(100, 100, 'id'), resolveAt(100, 100, 'id'),
      resolveAt(105, 100, 'path'), resolveAt(105, 100, 'path'),
      resolveAt(110, 100, 'vote'), resolveAt(110, 100, 'vote')],
    assert: [{ ok: false, reason: 'not-checked' }],
  })
  const inst = makeInst()
  const runner = new PlaybookRunner({ rpc })
  const step = {
    op: 'click', selector: '#radio', expectChecked: true,
    driftProbeMs: 1, relocateDelayMs: 1, assertRetryDelayMs: 1, driftThresholdPx: 3,
  }

  const res = await runner._step(step, inst, {})

  a.strictEqual(res.ok, false)
  a.strictEqual(res.reason, 'assert-failed:not-checked')
  a.strictEqual(res.phase, 'assert', '失败阶段是断言,不是笼统的 click-failed')
  a.strictEqual(res.attempts.length, 3, '三次尝试全部保留')
  a.deepStrictEqual(res.attempts.map(x => x.attempt), [1, 2, 3], '记录第几次')
  a.deepStrictEqual(res.attempts.map(x => x.strategy), ['id', 'path', 'vote'], '记录每次用的定位策略')
  a.deepStrictEqual(res.attempts.map(x => x.assertReason), ['not-checked', 'not-checked', 'not-checked'])
  a.deepStrictEqual(res.attempts.map(x => x.driftPx), [0, 5, 5], '记录每次相对上一次的偏差')
  a.ok(res.attempts.every(x => x.phase === 'assert' && x.status === 'assert-failed'))
  a.strictEqual(res.strategy, 'vote', '最终失败带回最后一次定位策略')
  a.strictEqual(res.driftPx, 5, '最终失败带回最后一次偏差')
  a.strictEqual(inst.events.filter(e => e.type === 'mouseDown').length, 3, '每次重试都真的重新注入')
})

test('定位失败与注入失败可区分,且不会被归成 click-failed', async () => {
  const runner1 = new PlaybookRunner({
    rpc: makeRpc({ resolve: [{ ok: false, reason: 'not-found' }] }),
  })
  const r1 = await runner1._step({ op: 'click', selector: '#never', locateRetryDelayMs: 1 }, makeInst(), {})
  a.strictEqual(r1.ok, false)
  a.strictEqual(r1.phase, 'locate')
  a.strictEqual(r1.reason, 'locate-failed:not-found')
  a.strictEqual(r1.attempts[0].locateReason, 'not-found')

  const runner2 = new PlaybookRunner({
    rpc: makeRpc({ resolve: [resolveAt(10, 10)], assert: [{ ok: true }] }),
  })
  const bad = makeInst()
  bad.webContents.sendInputEvent = () => { throw new Error('注入通道坏了') }
  const r2 = await runner2._step({ op: 'click', selector: '#buy', driftProbeMs: 0 }, bad, {})
  a.strictEqual(r2.ok, false)
  a.strictEqual(r2.phase, 'inject')
  a.strictEqual(r2.reason, 'inject-failed')
  a.match(r2.attempts[0].error, /注入通道坏了/)
})

test('ambiguous / offscreen 也归为定位失败并保留原因', async () => {
  const runner = new PlaybookRunner({
    rpc: makeRpc({ resolve: [{ ok: false, reason: 'ambiguous', candidates: 3 }] }),
  })
  const res = await runner._step({ op: 'click', selector: '#buy', locateRetryDelayMs: 1 }, makeInst(), {})
  a.strictEqual(res.phase, 'locate')
  a.strictEqual(res.reason, 'locate-failed:ambiguous')
})

test('可选步骤失败:继续执行,但在 trace 里标为跳过而非成功', async () => {
  const rpc = makeRpc({
    resolve: [{ ok: false, reason: 'not-found' }],
    assert: [{ ok: true }],
  })
  const runner = new PlaybookRunner({ rpc })
  const playbook = {
    name: 'optional-demo',
    steps: [
      { op: 'click', selector: '#never', optional: true, locateRetryDelayMs: 1 },
      { op: 'sleep', ms: 1 },
      { op: 'click', selector: '#buy', optional: false, driftProbeMs: 0 },
    ],
  }
  // 第二、三次 resolve 给成功(第一次已经用掉了 not-found)
  rpc.call = (() => {
    const base = rpc.call
    let n = 0
    return async (wc, method, params) => {
      if (method === 'resolve') {
        n++
        return n === 1 ? { ok: false, reason: 'not-found' } : resolveAt(50, 50)
      }
      if (method === 'assert') return { ok: true }
      return base(wc, method, params)
    }
  })()

  const out = await runner.run(playbook, [makeInst()])
  const r = out.results[0]

  a.strictEqual(r.ok, true, '可选步骤失败不终止剧本')
  a.deepStrictEqual(r.skipped, [0])
  const e0 = r.trace[0]
  a.strictEqual(e0.ok, false, '跳过不等于成功')
  a.strictEqual(e0.skipped, true)
  a.strictEqual(e0.status, 'skipped')
  a.strictEqual(e0.reason, 'locate-failed:not-found')
  a.strictEqual(r.trace[1].ok, true, '后续步骤继续执行')
  a.strictEqual(r.trace[2].ok, true)
  a.strictEqual(r.trace[2].skipped, undefined)
})

test('非可选步骤失败:终止执行,trace 保留尝试明细并带上失败策略/偏差', async () => {
  const rpc = makeRpc({
    resolve: [resolveAt(10, 10), resolveAt(10, 10), resolveAt(10, 10),
      resolveAt(10, 10), resolveAt(10, 10), resolveAt(10, 10)],
    assert: [{ ok: false, reason: 'not-checked' }],
  })
  const runner = new PlaybookRunner({ rpc })
  const playbook = {
    name: 'fail-demo',
    steps: [
      { op: 'click', selector: '#radio', expectChecked: true, retries: 1, driftProbeMs: 0, assertRetryDelayMs: 1 },
      { op: 'sleep', ms: 1 },
    ],
  }
  const out = await runner.run(playbook, [makeInst()])
  const r = out.results[0]

  a.strictEqual(out.ok, 0)
  a.strictEqual(r.ok, false)
  a.strictEqual(r.failedAt, 0)
  a.strictEqual(r.phase, 'assert')
  a.strictEqual(r.strategy, 'testAttr')
  a.strictEqual(r.attempts.length, 2)
  a.strictEqual(r.trace.length, 1, '失败后不再执行后续步骤')
  a.strictEqual(r.trace[0].attempts.length, 2)
  a.strictEqual(r.trace[0].phase, 'assert')
})

test('一次性的点击步骤不做延时探测,但跨步骤记忆上一次落点来发现漂移', async () => {
  // 第一段剧本:点一次 → 记住落点。第二段剧本(同一个 runner):页面已重排 → 必须重定位
  const rpc = makeRpc({ resolve: [resolveAt(100, 100), resolveAt(100, 320), resolveAt(100, 320)] })
  const inst = makeInst()
  const runner = new PlaybookRunner({ rpc })

  const first = await runner._step({ op: 'click', selector: '#buy' }, inst, {})
  a.strictEqual(first.ok, true)
  a.strictEqual(first.cssX, 100)
  a.strictEqual(rpc.count('resolve'), 1, '一次性点击不做确认性延时探测,只定位一次')
  const probeCountAfterFirst = rpc.count('resolve')

  const second = await runner._step({ op: 'click', selector: '#buy', relocateDelayMs: 1 }, inst, {})
  a.strictEqual(second.ok, true)
  a.strictEqual(second.attempts[0].driftPx, 220, '跨步骤记忆让 220px 的漂移被发现')
  a.ok(second.attempts[0].relocations >= 1, '发现跨步骤漂移后先重新定位')
  a.strictEqual(second.cssY, 320, '注入用的是重新定位后的落点')
  a.strictEqual(rpc.count('resolve') - probeCountAfterFirst, 2, '一次定位 + 一次重定位')
  const dbl = inst.events.filter(e => e.type === 'mouseDown')
  a.deepStrictEqual(dbl.map(e => e.y), [100, 320], '第一段打旧位置(当时是准的),第二段打新位置')
})

test('落点记忆按实例隔离,副本之间互不干扰', async () => {
  const rpc = makeRpc({ resolve: [resolveAt(100, 100), resolveAt(100, 100)] })
  const runner = new PlaybookRunner({ rpc })
  const a1 = makeInst({ id: 1 })
  const a2 = makeInst({ id: 2 })
  await runner._step({ op: 'click', selector: '#buy' }, a1, {})
  a.strictEqual(runner._getLanding(2, '#buy'), null, '别的实例没有这条记忆')
  a.deepStrictEqual(runner._getLanding(1, '#buy').cssX, 100)
})

test('descriptorFor 保持原来的最小 descriptor 语义', () => {
  a.deepStrictEqual(descriptorFor('#buy-now').id, 'buy-now')
  a.deepStrictEqual(descriptorFor('[data-autom="add-to-cart"]').testAttr,
    { attr: 'data-autom', value: 'add-to-cart' })
})
