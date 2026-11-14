import { getAnalysisPanelScript } from './ui/analysisPanel.js'

const STAGE_STATUS = new Set(['pending', 'running', 'complete', 'blocked', 'skipped'])
const EVIDENCE_LEVELS = new Set(['observed', 'replayed', 'reproduced'])
const STRATEGIES = new Set(['semantic-runtime', 'algorithm-recovery', 'recovery-failed'])
const BLOCKER_KINDS = new Set(['environment', 'resource', 'program', 'validation'])
const BLOCKER_OPERATIONS = new Set([
  'algorithm-recovery',
  'cycle-tls-initialize',
  'cycle-tls-request',
  'recovery-failed',
  'validate-generated-output',
  'validate-output-kind',
])
const BLOCKER_REASONS = new Set([
  'algorithm-recovery-engine-not-implemented',
  'recovery-failed',
  'status-title-or-cookie-mismatch',
])
const NEXT_ACTIONS = new Set([
  'implement-algorithm-recovery-engine',
  'inspect-program-behavior',
  'inspect-session-artifacts',
  'provide-environment-value',
  'provide-resource',
  'refresh-request-contract',
  'repair-cycle-tls-runtime',
  'retry-network-request',
])
const SOLVER_ID = /^artifact-[a-f0-9]{64}$/

function ownedJson(value) {
  return JSON.parse(JSON.stringify(value))
}

function known(value, allowed, fallback) {
  return allowed.has(value) ? value : fallback
}

function compactStages(stages) {
  const value = {}
  for (const field of ['browserEvidence', 'artifactGraph', 'nodeGeneration', 'requestValidation']) {
    if (stages && field in stages) value[field] = known(stages[field], STAGE_STATUS, 'blocked')
  }
  return value
}

function compactEvidence(evidence) {
  const value = {}
  for (const field of ['browser', 'node', 'request']) {
    if (evidence && field in evidence) value[field] = known(evidence[field], EVIDENCE_LEVELS, null)
  }
  return value
}

function compactBlocker(blocker) {
  if (!blocker) return null
  return {
    kind: known(blocker.kind, BLOCKER_KINDS, 'program'),
    operation: known(blocker.operation, BLOCKER_OPERATIONS, 'recovery-failed'),
    reason: known(blocker.reason, BLOCKER_REASONS, 'recovery-failed'),
  }
}

function compactRecoveryPayload(payload) {
  if (!payload || typeof payload !== 'object') return null
  if (payload.type === 'status') {
    if (!['running', 'idle'].includes(payload.status)) return null
    return {
      type: 'status', status: payload.status,
      text: payload.status === 'running' ? 'Agent 正在分析' : 'Agent 已完成',
    }
  }
  if (payload.type === 'recovery/progress') {
    return {
      type: payload.type,
      stages: compactStages(payload.stages),
      evidenceLevels: compactEvidence(payload.evidenceLevels),
    }
  }
  if (payload.type === 'recovery/result') {
    return {
      type: payload.type,
      stages: compactStages(payload.stages),
      evidenceLevels: compactEvidence(payload.evidenceLevels),
      strategy: known(payload.strategy, STRATEGIES, 'recovery-failed'),
      blocker: compactBlocker(payload.blocker),
      solverId: SOLVER_ID.test(payload.solverId) ? payload.solverId : null,
      nextAction: known(payload.nextAction, NEXT_ACTIONS, payload.nextAction == null ? null : 'inspect-session-artifacts'),
    }
  }
  return null
}

export function emptyDialogSelection() {
  return {
    state: 'closed', contextLevel: 'frame-snapshot', pageUrl: null,
    targetId: null, attachmentId: null, documentId: null, executionContextUniqueId: null,
    observedAt: Date.now(), elements: [], issues: [],
  }
}

// Runs in the Frame: bound serialization before returning page-controlled data.
function readPageSelection() {
  const read = globalThis.__deepspider_dialog_selection__
  if (typeof read !== 'function') return { installed: false }
  const raw = read()
  if (!Array.isArray(raw)) return { installed: true, invalid: true, elements: [] }
  const elements = raw.slice(0, 20)
    .filter((item) => item && typeof item.text === 'string' && typeof item.xpath === 'string')
    .map((item) => ({ text: item.text.slice(0, 1000), xpath: item.xpath.slice(0, 2048) }))
  return {
    installed: true, elements, truncated: raw.length > 20,
    invalid: elements.length !== Math.min(raw.length, 20),
  }
}

export class DialogBridge {
  constructor() {
    this.page = null
    this.frames = []
  }

  async open({ page }) {
    if (!page) throw new TypeError('Dialog requires page')
    if (this.page && this.page !== page) await this.close()
    this.page = page
    this.frames = page.frames()
    try {
      const source = getAnalysisPanelScript()
      await Promise.all(this.frames.map((frame) => frame.evaluate(source)))
    } catch (error) {
      await this.close()
      throw error
    }
  }

  async send(payload) {
    if (!this.page) return false
    const compact = compactRecoveryPayload(payload)
    if (!compact) return false
    return this.page.mainFrame().evaluate((message) => {
      const receive = globalThis.__deepspider_dialog_receive__
      if (typeof receive !== 'function') return false
      receive(message)
      return true
    }, ownedJson(compact))
  }

  async readSelection({ page }) {
    const snapshot = emptyDialogSelection()
    if (!page || this.page !== page) return snapshot
    snapshot.pageUrl = page.url()
    const frames = page.frames()
    const issue = (frameIndex, reason) => {
      if (!snapshot.issues.some((item) => item.frameIndex === frameIndex && item.reason === reason)) {
        snapshot.issues.push({ frameIndex, reason })
      }
    }
    if (frames.length > 32) issue(null, 'FRAME_LIMIT')
    if (frames.length === 0) issue(null, 'FRAME_UNAVAILABLE')
    const results = await Promise.allSettled(frames.slice(0, 32).map(async (frame) => {
      const frameUrl = frame.url()
      const value = await frame.evaluate(readPageSelection)
      return { frameUrl, value, navigated: frame.url() !== frameUrl }
    }))
    if (this.page !== page) return emptyDialogSelection()
    let successful = 0
    for (const [frameIndex, result] of results.entries()) {
      if (result.status === 'rejected') {
        issue(frameIndex, 'FRAME_UNAVAILABLE')
        continue
      }
      const { frameUrl, value, navigated } = result.value
      if (navigated) {
        issue(frameIndex, 'NAVIGATED_DURING_READ')
        continue
      }
      if (value?.installed === false) {
        issue(frameIndex, 'DIALOG_NOT_INSTALLED')
        continue
      }
      if (value?.installed !== true || !Array.isArray(value.elements)) {
        issue(frameIndex, 'INVALID_SELECTION')
        continue
      }
      if (value.invalid) issue(frameIndex, 'INVALID_SELECTION')
      if (value.truncated || value.elements.length > 20) issue(frameIndex, 'ELEMENT_LIMIT')
      let valid = 0
      for (const item of value.elements.slice(0, 20)) {
        if (!item || typeof item.text !== 'string' || typeof item.xpath !== 'string') {
          issue(frameIndex, 'INVALID_SELECTION')
          continue
        }
        valid++
        if (snapshot.elements.length === 20) {
          issue(frameIndex, 'ELEMENT_LIMIT')
          continue
        }
        snapshot.elements.push({
          text: item.text.slice(0, 1000), xpath: item.xpath.slice(0, 2048),
          frameIndex, frameUrl,
        })
      }
      if (valid > 0 || (!value.invalid && value.elements.length === 0)) successful++
    }
    snapshot.state = snapshot.issues.length
      ? (successful ? 'partial' : 'unavailable')
      : (snapshot.elements.length ? 'selected' : 'empty')
    return snapshot
  }

  async close() {
    const frames = this.frames
    this.page = null
    this.frames = []
    await Promise.allSettled(frames.map((frame) => (
      frame.evaluate(() => globalThis.__deepspider_dialog_close__?.())
    )))
  }
}
