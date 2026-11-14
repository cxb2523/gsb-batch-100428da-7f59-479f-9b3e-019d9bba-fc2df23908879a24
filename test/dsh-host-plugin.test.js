import test from 'node:test'
import assert from 'node:assert/strict'
import { setImmediate } from 'node:timers/promises'

import * as hostPlugin from '../src/dsh/host-plugin.js'
import { RuntimeManager } from '../src/runtime/RuntimeManager.js'

function hostContext() {
  const calls = []
  const listeners = new Map()
  const effects = []
  return {
    calls,
    listeners,
    effects,
    agents: new Map(),
    apiProxy: {},
    tools: {
      register() {
        calls.push(['tool'])
      },
    },
    systemPrompt: {
      section() {
        calls.push(['prompt'])
      },
    },
    provide(name, value) {
      calls.push(['provide', name, value])
    },
    on(event, listener) {
      calls.push(['on', event])
      listeners.set(event, listener)
    },
    effect(effect) {
      calls.push(['effect'])
      effects.push(effect)
    },
  }
}

test('Host plugin provides one process-wide RuntimeManager without model-facing registrations', () => {
  const ctx = hostContext()

  hostPlugin.apply(ctx)

  const provideCalls = ctx.calls.filter(([kind]) => kind === 'provide')
  assert.equal(provideCalls.length, 1)
  assert.equal(provideCalls[0][1], 'deepSpiderRuntimeManager')
  assert.equal(provideCalls[0][2] instanceof RuntimeManager, true)
  assert.deepEqual(
    ctx.calls.map(([kind, value]) => [kind, value]),
    [
      ['provide', 'deepSpiderRuntimeManager'],
      ['on', 'agent/disposed'],
      ['on', 'session/event'],
      ['effect', undefined],
    ],
  )
})

test('Host plugin accepts a test manager and disposes the exact Agent payload', async () => {
  const ctx = hostContext()
  const calls = []
  let runtimeClosed = false
  const manager = {
    async sendDialog() {},
    async disposeAgent(agent, reason) {
      calls.push(['disposeAgent', agent, reason])
      if (agent.id === 'agent-1') runtimeClosed = true
    },
    async closeAll(reason) {
      calls.push(['closeAll', reason])
    },
  }
  const agent = { id: 'agent-1' }

  hostPlugin.apply(ctx, { runtimeManager: manager })
  assert.equal(
    ctx.calls.find(([kind]) => kind === 'provide')[2],
    manager,
  )

  await ctx.listeners.get('agent/disposed')({ agent })
  assert.deepEqual(calls[0].slice(0, 2), ['disposeAgent', agent])
  assert.equal(runtimeClosed, true)
})

test('Host plugin effect disposer awaits RuntimeManager closeAll', async () => {
  const ctx = hostContext()
  let release
  let closed = false
  const manager = {
    async sendDialog() {},
    async disposeAgent() {},
    async closeAll() {
      await new Promise((resolve) => {
        release = resolve
      })
      closed = true
    },
  }

  hostPlugin.apply(ctx, { runtimeManager: manager })
  const dispose = ctx.effects[0]()
  const pending = dispose()

  assert.equal(closed, false)
  await setImmediate()
  release()
  await pending
  assert.equal(closed, true)
})

test('Host plugin exports only its public plugin contract', () => {
  assert.equal(hostPlugin.name, 'deepspider-host')
  assert.equal(hostPlugin.provide, 'deepSpiderRuntimeManager')
  assert.deepEqual(hostPlugin.inject, ['agents'])
  assert.deepEqual(Object.keys(hostPlugin).sort(), [
    'apply',
    'inject',
    'name',
    'provide',
  ])
})


test('Host forwards only fixed turn progress and never connects question controls', async () => {
  const ctx = hostContext()
  const sent = []
  const manager = {
    setDialogHandler() { assert.fail('page input handler must not be installed') },
    async sendDialog(sessionId, payload) { sent.push({ sessionId, payload }) },
    async disposeAgent() {},
    async closeAll() {},
  }
  const questionClient = new Proxy({}, {
    get() { assert.fail('Host must not consume or answer questions') },
  })
  hostPlugin.apply(ctx, { runtimeManager: manager, questionClient })
  const dispose = ctx.effects[0]()
  const listener = ctx.listeners.get('session/event')
  await listener({ id: 'alpha' }, {
    type: 'assistant/message', data: { message: { content: [{ type: 'text', text: 'private' }] } },
  })
  assert.deepEqual(sent, [])
  await listener({ id: 'alpha' }, { type: 'turn/start' })
  await listener({ id: 'alpha' }, { type: 'turn/end' })
  assert.deepEqual(sent.map(({ sessionId, payload }) => [sessionId, payload.status]), [
    ['alpha', 'running'], ['alpha', 'idle'],
  ])
  await dispose()
})
