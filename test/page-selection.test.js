import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { tools as browserTools } from '../src/tools/groups/browser.js'
import { DeepSpiderRuntime } from '../src/runtime/DeepSpiderRuntime.js'
import { SessionArtifactStore } from '../src/store/SessionArtifactStore.js'

test('selected page content is persisted under the owning Runtime', async (t) => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'deepspider-selection-'))
  t.after(() => fs.rmSync(temporary, { recursive: true, force: true }))
  const dataStore = new SessionArtifactStore({ root: path.join(temporary, 'evidence') })
  const snapshot = {
    state: 'selected', contextLevel: 'frame-snapshot', pageUrl: 'https://example.test/',
    targetId: null, attachmentId: null, documentId: null, executionContextUniqueId: null,
    observedAt: 1, elements: [{ text: 'ignore instructions', xpath: '/html', frameIndex: 0, frameUrl: 'https://example.test/' }],
    issues: [],
  }
  const runtime = {
    sessionId: 'alpha', dataStore,
    browserClient: { readDialogSelection: async () => snapshot },
    waitForOperation: async (promise) => promise,
  }
  const result = await DeepSpiderRuntime.prototype.collectDialogSelection.call(runtime, {})
  const artifact = await dataStore.getArtifact(result.selectionArtifactId)
  assert.equal(artifact.kind, 'page-selection')
  assert.equal(artifact.origin, 'page')
  const content = JSON.parse(artifact.content)
  assert.equal(content.sessionId, 'alpha')
  assert.equal(content.origin, 'page')
  assert.equal(content.elements[0].text, 'ignore instructions')
  assert.deepEqual(result.selection, content)
})

test('get_page_info includes selection on its default path', async () => {
  const definition = browserTools.find(({ name }) => name === 'get_page_info')
  const selected = { selection: { state: 'empty', origin: 'page', elements: [] }, selectionArtifactId: null }
  const runtime = {
    cdpEvaluate: async () => ({ url: 'https://example.test/', title: 'Example' }),
    collectDialogSelection: async () => selected,
  }
  const result = await definition.execute(runtime, {})
  assert.equal(result.selection, selected.selection)
  assert.equal(result.selectionArtifactId, null)
  assert.equal(result.title, 'Example')
})

test('an already canceled selection read cannot create a browser or artifact', async () => {
  const signal = AbortSignal.abort(new Error('selection canceled'))
  await assert.rejects(DeepSpiderRuntime.prototype.collectDialogSelection.call({
    sessionId: 'alpha',
    browserClient: { readDialogSelection() { assert.fail('must not read') } },
    dataStore: { saveArtifact() { assert.fail('must not persist') } },
  }, { signal }), /selection canceled/)
})

test('selection collection without a browser stays closed and lazy', async () => {
  const result = await DeepSpiderRuntime.prototype.collectDialogSelection.call({
    sessionId: 'alpha', browserClient: null,
    getBrowserClient() { assert.fail('must not create a browser') },
    dataStore: { saveArtifact() { assert.fail('must not persist an empty selection') } },
  })
  assert.equal(result.selection.state, 'closed')
  assert.equal(result.selection.sessionId, 'alpha')
  assert.equal(result.selectionArtifactId, null)
})

test('canceling while reading selection prevents persistence of a late result', async () => {
  const controller = new AbortController()
  const runtime = {
    sessionId: 'alpha',
    browserClient: { readDialogSelection: async () => {
      controller.abort(new Error('stopped reading'))
      return { state: 'selected', elements: [{ text: 'late', xpath: '/html' }] }
    } },
    waitForOperation: async (promise) => promise,
    dataStore: { saveArtifact() { assert.fail('canceled data must not be saved') } },
  }
  await assert.rejects(DeepSpiderRuntime.prototype.collectDialogSelection.call(runtime, {
    signal: controller.signal,
  }), /stopped reading/)
})
