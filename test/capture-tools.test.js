import assert from 'node:assert/strict'
import test from 'node:test'
import vm from 'node:vm'

import { tools as captureTools } from '../src/tools/groups/capture.js'
import {
  COLLECT_ERROR_CATEGORY,
  COLLECT_TIMEOUT_MS,
  collectFailure,
  withCollectTimeout,
} from '../src/browser/collect-contract.js'

function definition(name) {
  return captureTools.find((tool) => tool.name === name)
}

/**
 * 构造一个模拟 iframe 执行上下文的 vm realm，并让 cdpEvaluate 真正执行注入表达式，
 * 从而端到端验证 collectPropertyInRealm 被 toString 注入后的契约。
 */
function iframeRuntime({ window, cdpEvaluate, captures = { propertyFacts: [] } } = {}) {
  const context = vm.createContext({})
  if (window) new vm.Script(window).runInContext(context)
  return {
    captures,
    browserClient: { mode: 'observe' },
    getActiveFrameContext: () => ({ contextId: 7, frameId: 'frame-7' }),
    cdpEvaluate: cdpEvaluate ?? (async (expression) => vm.runInContext(expression, context)),
  }
}

const FULL_FACT_FIELDS = [
  'path',
  'data',
  'descriptor',
  'ownerDepth',
  'brand',
  'constructorName',
  'prototypeChain',
  'functionSource',
]

test('main frame collection returns the full fact schema and records it in propertyFacts', async () => {
  const successPayload = {
    success: true,
    path: 'navigator.language',
    data: { type: 'string', value: 'en-US' },
    descriptor: null,
    ownerDepth: 1,
    brand: '[object String]',
    constructorName: 'String',
    prototypeChain: ['String', 'Object'],
    functionSource: null,
  }
  const runtime = {
    captures: { propertyFacts: [] },
    browserClient: { mode: 'observe' },
    getActiveFrameContext: () => ({ contextId: null, frameId: null }),
    getPage: async () => ({ evaluate: async () => successPayload }),
  }

  const result = await definition('collect_property').execute(runtime, {
    path: 'navigator.language',
    depth: 2,
  }, undefined)

  assert.equal(result.success, true)
  for (const field of FULL_FACT_FIELDS) assert.ok(field in result, `main frame result must carry ${field}`)
  assert.equal(runtime.captures.propertyFacts.length, 1)
  assert.deepEqual(runtime.captures.propertyFacts[0], {
    source: 'patchright-session',
    mode: 'observe',
    ...result,
  })
})

test('iframe property collection returns the same browser fact schema as the main frame', async () => {
  const runtime = iframeRuntime({
    window: `
      function Navigator() {}
      Object.defineProperty(Navigator.prototype, 'language', {
        get() { return 'en-US'; },
        configurable: true,
        enumerable: true,
      });
      globalThis.window = { navigator: new Navigator() };
    `,
  })

  const result = await definition('collect_property').execute(runtime, {
    path: 'navigator.language',
    depth: 2,
  }, undefined)

  assert.equal(result.success, true)
  assert.equal(result.frameId, 'frame-7')
  for (const field of FULL_FACT_FIELDS) assert.ok(field in result, `iframe result must carry ${field}`)
  assert.deepEqual(JSON.parse(JSON.stringify(result.data)), { type: 'string', value: 'en-US' })
  assert.deepEqual(JSON.parse(JSON.stringify(result.descriptor)), {
    configurable: true,
    enumerable: true,
    hasGetter: true,
    hasSetter: false,
  })
  assert.equal(result.ownerDepth, 1)
  assert.equal(result.brand, '[object String]')
  assert.equal(result.constructorName, 'String')
  assert.deepEqual(JSON.parse(JSON.stringify(result.prototypeChain.slice(0, 2))), ['String', 'Object'])
  assert.equal(result.functionSource, null)

  // 成功事实仍然写入 propertyFacts，并保留 source 与 mode
  assert.equal(runtime.captures.propertyFacts.length, 1)
  const fact = runtime.captures.propertyFacts[0]
  assert.equal(fact.source, 'patchright-session')
  assert.equal(fact.mode, 'observe')
  assert.equal(fact.path, 'navigator.language')
  assert.equal(fact.frameId, 'frame-7')
})

test('iframe missing property is a categorized failure with a breakpoint hint and is not recorded', async () => {
  const runtime = iframeRuntime({ window: 'globalThis.window = { navigator: {} };' })

  const result = await definition('collect_property').execute(runtime, {
    path: 'navigator.language',
    depth: 2,
  }, undefined)

  assert.equal(result.success, false)
  assert.equal(result.errorCategory, COLLECT_ERROR_CATEGORY.PROPERTY_NOT_FOUND)
  assert.match(result.error, /navigator\.language is undefined/)
  assert.equal(result.frameId, 'frame-7')
  assert.match(result.hint, /set_breakpoint \+ evaluate_on_callframe/)
  // 失败不得改写成成功，也不得写入 propertyFacts
  assert.equal(runtime.captures.propertyFacts.length, 0)
})

test('iframe storage miss lists the frame keys in the hint while staying a failure', async () => {
  const missContext = vm.createContext({})
  new vm.Script('globalThis.window = { localStorage: { token: "abc" } };').runInContext(missContext)
  const runtime = {
    captures: { propertyFacts: [] },
    browserClient: { mode: 'observe' },
    getActiveFrameContext: () => ({ contextId: 7, frameId: 'frame-7' }),
    cdpEvaluate: async (expression) => {
      // 键名枚举走独立表达式；属性采集表达式放进 vm realm 执行
      if (expression.includes('Object.keys(storage)')) return ['token']
      return vm.runInContext(expression, missContext)
    },
  }

  const result = await definition('collect_property').execute(runtime, {
    path: 'localStorage.missingKey',
    depth: 1,
  }, undefined)

  assert.equal(result.success, false)
  assert.equal(result.errorCategory, COLLECT_ERROR_CATEGORY.PROPERTY_NOT_FOUND)
  assert.match(result.hint, /localStorage has 1 keys: \[token\]/)
  assert.equal(runtime.captures.propertyFacts.length, 0)
})

test('iframe evaluation timeout is bounded, categorized, and is not recorded as a fact', async () => {
  // cdpEvaluate 永不 resolve，模拟求值卡住；工具层超时上限必须让调用按时返回
  const runtime = iframeRuntime({
    cdpEvaluate: () => new Promise(() => {}),
  })
  const startedAt = Date.now()
  const result = await definition('collect_property').execute(runtime, {
    path: 'navigator.language',
  }, undefined)
  const elapsed = Date.now() - startedAt

  assert.equal(result.success, false)
  assert.equal(result.errorCategory, COLLECT_ERROR_CATEGORY.EVALUATION_TIMEOUT)
  assert.match(result.error, /timeout/)
  assert.equal(result.frameId, 'frame-7')
  assert.equal(runtime.captures.propertyFacts.length, 0)
  // 必须在超时上限附近返回，而不是挂起整个采集调用
  assert.ok(elapsed < COLLECT_TIMEOUT_MS + 1500, `returned in ${elapsed}ms`)
})

test('iframe invalidated execution context is categorized distinctly from a missing property', async () => {
  const runtime = iframeRuntime({
    cdpEvaluate: async () => {
      throw new Error('Cannot find context with specified id — active frame context was invalidated, call select_frame again')
    },
  })

  const result = await definition('collect_property').execute(runtime, {
    path: 'navigator.language',
  }, undefined)

  assert.equal(result.success, false)
  assert.equal(result.errorCategory, COLLECT_ERROR_CATEGORY.CONTEXT_INVALIDATED)
  assert.equal(result.hint, undefined)
  assert.equal(runtime.captures.propertyFacts.length, 0)
})

test('collect_property stores successful browser facts in the owning Runtime', async () => {
  const runtime = {
    captures: { propertyFacts: [] },
    browserClient: { mode: 'observe' },
    getActiveFrameContext: () => ({ contextId: null, frameId: null }),
    getPage: async () => ({
      evaluate: async () => ({
        success: true,
        path: 'navigator.language',
        data: { type: 'string', value: 'en-US' },
        descriptor: null,
        ownerDepth: 1,
        brand: '[object String]',
        constructorName: 'String',
        prototypeChain: ['String', 'Object'],
        functionSource: null,
      }),
    }),
  }

  const result = await definition('collect_property').execute(runtime, {
    path: 'navigator.language',
    depth: 2,
  }, undefined)

  assert.equal(result.success, true)
  assert.deepEqual(runtime.captures.propertyFacts, [{
    source: 'patchright-session',
    mode: 'observe',
    ...result,
  }])
})

test('main frame missing property keeps the breakpoint hint and never writes a property fact', async () => {
  const missing = {
    success: false,
    path: 'window.__onlyDuringCall__',
    errorCategory: COLLECT_ERROR_CATEGORY.PROPERTY_NOT_FOUND,
    error: 'window.__onlyDuringCall__ is undefined',
  }
  const runtime = {
    captures: { propertyFacts: [] },
    browserClient: { mode: 'observe' },
    getActiveFrameContext: () => ({ contextId: null, frameId: null }),
    getPage: async () => ({ evaluate: async () => missing }),
  }

  const result = await definition('collect_property').execute(runtime, {
    path: 'window.__onlyDuringCall__',
  }, undefined)

  assert.equal(result.success, false)
  assert.match(result.hint, /set_breakpoint \+ evaluate_on_callframe/)
  assert.equal(runtime.captures.propertyFacts.length, 0)
})

test('withCollectTimeout resolves a pending result and surfaces timeout failures without hanging', async () => {
  const ok = await withCollectTimeout(Promise.resolve({ success: true, path: 'a' }), { path: 'a', timeout: 30 })
  assert.deepEqual(ok, { success: true, path: 'a' })

  const timedOut = await withCollectTimeout(new Promise(() => {}), { path: 'a', timeout: 20 })
  assert.equal(timedOut.success, false)
  assert.equal(timedOut.errorCategory, COLLECT_ERROR_CATEGORY.EVALUATION_TIMEOUT)

  // 超时落定后底层 Promise 再 reject，不得触发 unhandledRejection
  const unhandled = []
  const onUnhandled = (reason) => unhandled.push(reason)
  process.on('unhandledRejection', onUnhandled)
  let lateReject
  const late = new Promise((_, reject) => { lateReject = reject })
  const raced = await withCollectTimeout(late, { path: 'a', timeout: 20 })
  assert.equal(raced.errorCategory, COLLECT_ERROR_CATEGORY.EVALUATION_TIMEOUT)
  lateReject(new Error('late evaluation failure'))
  await new Promise((resolve) => setTimeout(resolve, 30))
  process.off('unhandledRejection', onUnhandled)
  assert.deepEqual(unhandled, [])

  // 契约模块自身的失败构造器形状
  assert.deepEqual(
    collectFailure('b', COLLECT_ERROR_CATEGORY.CONTEXT_INVALIDATED, 'boom'),
    { success: false, path: 'b', errorCategory: 'context_invalidated', error: 'boom' },
  )
})

test('main frame storage miss lists storage keys in the hint without recording a fact', async () => {
  const missing = {
    success: false,
    path: 'localStorage.absentKey',
    errorCategory: COLLECT_ERROR_CATEGORY.PROPERTY_NOT_FOUND,
    error: 'localStorage.absentKey is undefined',
  }
  const storageSnapshot = {
    success: true,
    data: { type: 'object', properties: { token: { type: 'string', value: 'abc' } } },
  }
  const runtime = {
    captures: { propertyFacts: [] },
    browserClient: { mode: 'observe' },
    getActiveFrameContext: () => ({ contextId: null, frameId: null }),
    getPage: async () => ({
      evaluate: async (_fn, arg) => (arg?.path === 'localStorage' ? storageSnapshot : missing),
    }),
  }

  const result = await definition('collect_property').execute(runtime, {
    path: 'localStorage.absentKey',
  }, undefined)

  assert.equal(result.success, false)
  assert.match(result.hint, /localStorage has 1 keys: \[token\]/)
  assert.equal(runtime.captures.propertyFacts.length, 0)
})

test('collect_env returns page evidence with explicit source and mode', async () => {
  const result = await definition('collect_env').execute({
    getPage: async () => ({
      url: () => 'https://example.com/',
      title: async () => 'Example',
      content: async () => '<!doctype html>',
      context: () => ({ cookies: async () => [] }),
      evaluate: async () => ({ referrer: '', local: {}, session: {} }),
    }),
  }, {}, undefined)

  assert.equal(result.source, 'patchright-session')
  assert.equal(result.mode, 'observe')
  assert.equal('navigator' in result, false)
})
