import { RuntimeManager } from '../runtime/RuntimeManager.js'

export const name = 'deepspider-host'
export const provide = 'deepSpiderRuntimeManager'
export const inject = ['agents']

export function apply(ctx, config = {}) {
  const manager = config.runtimeManager ?? new RuntimeManager()
  ctx.provide(provide, manager)
  ctx.on('agent/disposed', ({ agent }) => (
    manager.disposeAgent(agent, 'DSH Agent disposed')
  ))
  ctx.on('session/event', (session, event) => {
    if (event.type !== 'turn/start' && event.type !== 'turn/end') return
    const running = event.type === 'turn/start'
    return manager.sendDialog(String(session.id), {
      type: 'status',
      status: running ? 'running' : 'idle',
      text: running ? 'Agent 正在分析' : 'Agent 已完成',
    })
  })
  ctx.effect(() => async () => {
    await manager.closeAll('DSH Host disposed')
  })
}
