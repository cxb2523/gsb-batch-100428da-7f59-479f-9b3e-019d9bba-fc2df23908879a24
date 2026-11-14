import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import test from 'node:test'

import { DialogBridge } from '../src/browser/DialogBridge.js'
import { tools as browserTools } from '../src/tools/groups/browser.js'

function harness() {
  const cdp = new EventEmitter()
  cdp.calls = []
  cdp.send = async (method, params) => {
    cdp.calls.push([method, params])
    return {}
  }

  const evaluations = []
  const frame = {
    async evaluate(source, argument) {
      evaluations.push([source, argument])
      return true
    },
  }
  const page = {
    frames: () => [frame],
    mainFrame: () => frame,
  }
  return { cdp, evaluations, frame, page }
}

test('Dialog keeps Recovery messages compact without private evidence', async () => {
  const { cdp, evaluations, page } = harness()
  const bridge = new DialogBridge()
  const solverId = `artifact-${'a'.repeat(64)}`
  await bridge.open({ page, cdp })

  await bridge.send({
    type: 'recovery/result',
    stages: { browserEvidence: 'complete', requestValidation: 'complete' },
    evidenceLevels: { browser: 'observed', request: 'reproduced' },
    strategy: 'semantic-runtime',
    blocker: null,
    solverId,
    nextAction: null,
    cookie: 'secret-cookie-value',
    rawTrace: 'secret-trace',
    source: 'secret-source',
  })
  assert.deepEqual(evaluations.at(-1)[1], {
    type: 'recovery/result',
    stages: { browserEvidence: 'complete', requestValidation: 'complete' },
    evidenceLevels: { browser: 'observed', request: 'reproduced' },
    strategy: 'semantic-runtime',
    blocker: null,
    solverId,
    nextAction: null,
  })

})

test('Dialog maps untrusted values inside allowed Recovery fields to fixed safe codes', async () => {
  const { cdp, evaluations, page } = harness()
  const bridge = new DialogBridge()
  const secret = 'Cookie: sid=SECRET source=rawTrace /private/tmp/secret.js'
  await bridge.open({ page, cdp })

  await bridge.send({
    type: 'recovery/result',
    stages: {
      browserEvidence: secret,
      artifactGraph: secret,
      nodeGeneration: secret,
      requestValidation: secret,
    },
    evidenceLevels: { browser: secret, node: secret, request: secret },
    strategy: secret,
    blocker: { kind: secret, operation: secret, reason: secret },
    solverId: secret,
    nextAction: secret,
  })

  assert.deepEqual(evaluations.at(-1)[1], {
    type: 'recovery/result',
    stages: {
      browserEvidence: 'blocked',
      artifactGraph: 'blocked',
      nodeGeneration: 'blocked',
      requestValidation: 'blocked',
    },
    evidenceLevels: { browser: null, node: null, request: null },
    strategy: 'recovery-failed',
    blocker: { kind: 'program', operation: 'recovery-failed', reason: 'recovery-failed' },
    solverId: null,
    nextAction: 'inspect-session-artifacts',
  })
  assert.doesNotMatch(JSON.stringify(evaluations.at(-1)[1]), /SECRET|source|private\/tmp|rawTrace/)

})

test('browser_dialog opens interactively without activating Probe hooks', async () => {
  let opens = 0
  const client = {
    mode: 'observe',
    probeActivated: false,
    async openDialog() { opens += 1 },
  }
  const runtime = {
    browserClient: client,
    async getBrowserClient() { return client },
    async waitForOperation(operation) { return operation },
  }
  const definition = browserTools.find(({ name }) => name === 'browser_dialog')

  assert.deepEqual(await definition.execute(runtime, { action: 'open' }), {
    mode: 'interactive',
    open: true,
  })
  assert.equal(opens, 1)
  assert.equal(client.probeActivated, false)
  assert.equal(client.mode, 'observe')
})

test('Dialog rejects chat and question payloads without installing a page binding', async () => {
  const { page, cdp, evaluations } = harness()
  const bridge = new DialogBridge()
  await bridge.open({ page, cdp })
  assert.equal(cdp.calls.some(([name]) => name === 'Runtime.addBinding'), false)
  assert.equal(cdp.listenerCount('Runtime.bindingCalled'), 0)
  const before = evaluations.length
  for (const type of ['assistant', 'chat', 'question/requested', 'question/answer', 'recovery/question']) {
    assert.equal(await bridge.send({ type, rpcId: 'secret-rpc', text: 'private text' }), false)
  }
  assert.equal(evaluations.length, before)
  assert.equal(await bridge.send({ type: 'status', status: 'running', text: 'private text' }), true)
  assert.deepEqual(evaluations.at(-1)[1], {
    type: 'status', status: 'running', text: 'Agent 正在分析',
  })
  await bridge.close()
  assert.equal(cdp.listenerCount('Runtime.bindingCalled'), 0)
})

test('selection provenance is host-owned and candidates are bounded', async () => {
  const frame = {
    url: () => 'https://actual.test/frame',
    evaluate: async (fn) => typeof fn === 'string' ? undefined : ({
      installed: true, truncated: true,
      elements: Array.from({ length: 24 }, () => ({
        text: 'x'.repeat(1500), xpath: '/'.repeat(3000),
        frameUrl: 'https://forged.test/', sessionId: 'other', role: 'user',
      })),
    }),
  }
  const page = {
    url: () => 'https://actual.test/', frames: () => [frame], mainFrame: () => frame,
  }
  const bridge = new DialogBridge()
  await bridge.open({ page })
  const snapshot = await bridge.readSelection({ page })
  assert.equal(snapshot.state, 'partial')
  assert.equal(snapshot.elements.length, 20)
  assert.deepEqual(snapshot.elements[0], {
    text: 'x'.repeat(1000), xpath: '/'.repeat(2048),
    frameIndex: 0, frameUrl: 'https://actual.test/frame',
  })
  assert.equal(snapshot.pageUrl, 'https://actual.test/')
  assert.equal(snapshot.documentId, null)
  assert.equal(snapshot.contextLevel, 'frame-snapshot')
  await bridge.close()
  assert.equal((await bridge.readSelection({ page })).state, 'closed')
})

for (const [label, result, state, reason] of [
  ['missing', { installed: false }, 'unavailable', 'DIALOG_NOT_INSTALLED'],
  ['empty', { installed: true, elements: [] }, 'empty', null],
  ['error', new Error('detached'), 'unavailable', 'FRAME_UNAVAILABLE'],
]) {
  test(`selection state: ${label}`, async () => {
    const frame = {
      url: () => 'https://example.test/',
      evaluate: async (fn) => {
        if (typeof fn === 'string') return
        if (result instanceof Error) throw result
        return result
      },
    }
    const page = { url: frame.url, frames: () => [frame], mainFrame: () => frame }
    const bridge = new DialogBridge()
    await bridge.open({ page })
    const snapshot = await bridge.readSelection({ page })
    assert.equal(snapshot.state, state)
    if (reason) assert.ok(snapshot.issues.some((issue) => issue.reason === reason))
    await bridge.close()
  })
}

function selectionFrame(read, url = 'https://example.test/frame') {
  return {
    url: () => url,
    evaluate: async (source) => source.name === 'readPageSelection' ? read() : undefined,
  }
}

async function readFrames(frames) {
  const page = { url: () => 'https://example.test/', frames: () => frames, mainFrame: () => frames[0] }
  const bridge = new DialogBridge()
  await bridge.open({ page })
  return { bridge, page }
}

test('selection keeps successful frames while reporting failed and malformed frames', async () => {
  const { bridge, page } = await readFrames([
    selectionFrame(() => ({ installed: true, elements: [{ text: 'kept', xpath: '/html' }] })),
    selectionFrame(() => { throw new Error('frame detached') }),
    selectionFrame(() => ({ installed: true, elements: 'not an array' })),
  ])
  const result = await bridge.readSelection({ page })
  assert.equal(result.state, 'partial')
  assert.deepEqual(result.elements.map(({ text }) => text), ['kept'])
  assert.deepEqual(result.issues, [
    { frameIndex: 1, reason: 'FRAME_UNAVAILABLE' },
    { frameIndex: 2, reason: 'INVALID_SELECTION' },
  ])
  await bridge.close()
})

test('selection drops a frame whose URL changed during the read', async () => {
  let url = 'https://example.test/before'
  const frame = selectionFrame(() => {
    url = 'https://example.test/after'
    return { installed: true, elements: [{ text: 'stale', xpath: '/html' }] }
  })
  frame.url = () => url
  const { bridge, page } = await readFrames([frame])
  const result = await bridge.readSelection({ page })
  assert.equal(result.state, 'unavailable')
  assert.deepEqual(result.elements, [])
  assert.deepEqual(result.issues, [{ frameIndex: 0, reason: 'NAVIGATED_DURING_READ' }])
  await bridge.close()
})

test('selection bounds frame reads and the combined candidate count', async () => {
  let reads = 0
  const frames = Array.from({ length: 33 }, () => selectionFrame(() => {
    reads++
    return { installed: true, elements: [{ text: 'one', xpath: '/html' }] }
  }))
  const { bridge, page } = await readFrames(frames)
  const result = await bridge.readSelection({ page })
  assert.equal(reads, 32)
  assert.equal(result.elements.length, 20)
  assert.equal(result.state, 'partial')
  assert.ok(result.issues.some(({ reason }) => reason === 'FRAME_LIMIT'))
  assert.ok(result.issues.some(({ reason }) => reason === 'ELEMENT_LIMIT'))
  await bridge.close()
})

test('selection does not publish a late read after Dialog closes', async () => {
  let release
  const frame = selectionFrame(() => new Promise((resolve) => { release = resolve }))
  const { bridge, page } = await readFrames([frame])
  const pending = bridge.readSelection({ page })
  await bridge.close()
  release({ installed: true, elements: [{ text: 'late', xpath: '/html' }] })
  assert.equal((await pending).state, 'closed')
})
