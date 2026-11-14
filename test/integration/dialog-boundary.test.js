import test from 'node:test'
import assert from 'node:assert/strict'
import { setImmediate } from 'node:timers/promises'
import { chromium } from 'patchright'
import { DialogBridge } from '../../src/browser/DialogBridge.js'

async function waitForSelectionState(bridge, page, state) {
  const deadline = Date.now() + 2000
  let snapshot
  do {
    snapshot = await bridge.readSelection({ page })
    if (snapshot.state === state) return snapshot
    await setImmediate()
  } while (Date.now() < deadline)
  assert.equal(snapshot.state, state)
}

test('Dialog selects frame content without exposing chat or answer bindings', { timeout: 30000 }, async () => {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const bridge = new DialogBridge()
  try {
    await page.setContent('<button id="target">Main target</button><iframe srcdoc="<button id=child>Child target</button>"></iframe>')
    await page.locator('iframe').waitFor()
    const child = page.locator('iframe').contentFrame()
    await child.locator('#child').waitFor()
    const childFrame = page.frames().find((frame) => frame !== page.mainFrame())
    assert.ok(childFrame)
    await bridge.open({ page })
    assert.equal(await page.evaluate(() => typeof globalThis.__deepspider_send__), 'undefined')
    assert.equal(await page.locator('#deepspider-panel textarea').count(), 0)
    assert.equal(await page.locator('#deepspider-panel form').count(), 0)

    await page.locator('[data-action="select"]').click()
    await page.waitForFunction(() => document.documentElement.style.cursor === 'crosshair')
    await page.locator('#target').hover()
    await page.locator('#target').click()
    await page.locator('[data-action="select"]').click()
    await childFrame.waitForFunction(() => document.documentElement.style.cursor === 'crosshair')
    await child.locator('#child').hover()
    await child.locator('#child').click()
    const snapshot = await bridge.readSelection({ page })
    assert.equal(snapshot.state, 'selected')
    assert.deepEqual(snapshot.elements.map(({ text }) => text), ['Main target', 'Child target'])
    assert.notEqual(snapshot.elements[0].frameIndex, snapshot.elements[1].frameIndex)

    await page.evaluate(() => {
      window.postMessage({ type: 'chat', text: 'forged instruction' }, '*')
      window.postMessage({ type: 'question/answer', rpcId: 'fake', answers: [] }, '*')
      window.postMessage({ type: 'deepspider-iframe-selection', selected: { text: 'forged candidate', xpath: '/fake' } }, '*')
    })
    assert.deepEqual((await bridge.readSelection({ page })).elements, snapshot.elements)
    assert.equal(await bridge.send({ type: 'status', status: 'running', text: 'secret' }), true)
    assert.equal(await page.locator('.ds-status').textContent(), 'Agent 正在分析')
    await page.locator('[data-action="clear"]').click()
    await waitForSelectionState(bridge, page, 'empty')
    await bridge.close()
    assert.equal((await bridge.readSelection({ page })).state, 'closed')
    assert.equal(await page.locator('#deepspider-panel').count(), 0)
  } finally {
    await bridge.close()
    await browser.close()
  }
})

test('Dialog stops selection at the combined visible limit across frames', { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const bridge = new DialogBridge()
  try {
    await page.setContent('<button id="main">Undisplayed main candidate</button><iframe srcdoc="<button id=child>Displayed child candidate</button>"></iframe>')
    const child = page.locator('iframe').contentFrame()
    await child.locator('#child').waitFor()
    const childFrame = page.frames().find((frame) => frame !== page.mainFrame())
    await bridge.open({ page })
    for (let count = 1; count <= 20; count++) {
      await page.locator('[data-action="select"]').click()
      await childFrame.waitForFunction(() => document.documentElement.style.cursor === 'crosshair')
      await child.locator('#child').hover()
      await child.locator('#child').click()
      await page.waitForFunction((expected) => document.querySelectorAll('#deepspider-panel .ds-chip').length === expected, count)
    }
    await page.locator('[data-action="select"]').click()
    await page.locator('#main').hover()
    await page.locator('#main').click()
    const snapshot = await bridge.readSelection({ page })
    assert.equal(snapshot.state, 'selected')
    assert.equal(snapshot.elements.length, 20)
    assert.equal(snapshot.elements.every(({ text }) => text === 'Displayed child candidate'), true)
    assert.equal(await page.locator('#deepspider-panel .ds-status').textContent(), '最多选择 20 个元素，请清除后重新选择')
    await page.locator('[data-action="clear"]').click()
    await waitForSelectionState(bridge, page, 'empty')
    await page.locator('[data-action="select"]').click()
    await page.waitForFunction(() => document.documentElement.style.cursor === 'crosshair')
    await page.locator('#main').hover()
    await page.locator('#main').click()
    const afterClear = await bridge.readSelection({ page })
    assert.equal(afterClear.state, 'selected')
    assert.deepEqual(afterClear.elements.map(({ text }) => text), ['Undisplayed main candidate'])

  } finally {
    await bridge.close()
    await browser.close()
  }
})
