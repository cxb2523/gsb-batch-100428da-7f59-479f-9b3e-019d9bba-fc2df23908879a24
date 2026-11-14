import { EnvCollector, collectPropertyInRealm } from '../../browser/collector.js'
import {
  COLLECT_ERROR_CATEGORY,
  COLLECT_TIMEOUT_MS,
  classifyCollectError,
  collectFailure,
  isPropertyNotFoundResult,
  withCollectTimeout,
} from '../../browser/collect-contract.js'
import { SessionEvidenceCollector } from '../../browser/SessionEvidenceCollector.js'
import { defineDeepSpiderTool } from '../catalog.js'
import { DeepSpiderToolError } from '../errors.js'

function failure(error) {
  if (error instanceof DeepSpiderToolError) throw error
  throw new DeepSpiderToolError('CAPTURE_OPERATION_FAILED', error.message)
}

const BREAKPOINT_HINT = (path) =>
  `Variable ${path} is undefined. It may only exist during specific function execution. Use set_breakpoint + evaluate_on_callframe to capture it at runtime.`

/**
 * 属性缺失时的提示：
 * - storage 前缀路径：列出当前上下文里实际存在的键名
 * - 其它路径：提示用断点在运行时捕获
 * 提示只附加在 PROPERTY_NOT_FOUND 失败上，不会把失败改写成成功。
 */
async function missingValueHint(result, path, listStorageKeys) {
  if (!isPropertyNotFoundResult(result)) return null
  const storageMatch = path.match(/^(localStorage|sessionStorage)\./)
  if (!storageMatch) return BREAKPOINT_HINT(path)

  let keys
  try {
    keys = await listStorageKeys(storageMatch[1])
  } catch {
    keys = []
  }
  return `${storageMatch[1]} has ${keys.length} keys: [${keys.slice(0, 30).join(', ')}]. Check key name or trigger the write operation first.`
}

function recordPropertyFact(runtime, result) {
  if (!result?.success || !runtime.captures) return result
  if (!Array.isArray(runtime.captures.propertyFacts)) runtime.captures.propertyFacts = []
  runtime.captures.propertyFacts.push({
    source: 'patchright-session',
    mode: runtime.browserClient?.mode || 'observe',
    ...result,
  })
  return result
}

export const tools = Object.freeze([
  defineDeepSpiderTool({
    name: 'collect_env',
    description: 'Collect a full browser environment snapshot',
    parameters: {},
    async execute(runtime, _args, signal) {
      try {
        const page = await runtime.getPage({ signal })
        return await new SessionEvidenceCollector(page).collect()
      } catch (error) {
        failure(error)
      }
    },
  }),
  defineDeepSpiderTool({
    name: 'collect_property',
    description: 'Collect a browser property value and its descriptor facts',
    parameters: {
      path: { type: 'string', required: true, description: 'Property path, e.g. navigator.connection.effectiveType' },
      depth: { type: 'number', default: 2, description: 'Collection depth' },
    },
    async execute(runtime, { path, depth = 2 }, signal) {
      try {
        const frameContext = runtime.getActiveFrameContext()

        // === 跨域 iframe 路径：CDP Runtime.evaluate，带超时上限与可判别错误类别 ===
        if (frameContext.contextId != null) {
          const args = JSON.stringify({ path, depth })
          const expression = `(() => ({ ...(${collectPropertyInRealm.toString()})(${args}), frameId: ${JSON.stringify(frameContext.frameId)} }))()`

          const listStorageKeys = async (storageType) => {
            const keysExpression = `(() => {
              const storage = ${JSON.stringify(storageType)} === 'sessionStorage' ? sessionStorage : localStorage
              return Object.keys(storage)
            })()`
            const keys = await runtime.cdpEvaluate(keysExpression, { signal, timeout: COLLECT_TIMEOUT_MS })
            return Array.isArray(keys) ? keys : []
          }

          let result
          try {
            result = await withCollectTimeout(runtime.cdpEvaluate(expression, {
              signal,
              timeout: COLLECT_TIMEOUT_MS,
            }), { path, signal, timeout: COLLECT_TIMEOUT_MS })
          } catch (error) {
            // 上下文失效 / 超时 / 其它求值异常统一为结构化失败，不向上抛出工具错误
            if (signal?.aborted) throw error
            result = collectFailure(path, classifyCollectError(error), error.message)
          }

          // 防御：底层返回了非契约值（例如 CDP 给出 undefined）时按求值失败处理
          if (!result || typeof result !== 'object' || typeof result.success !== 'boolean') {
            result = collectFailure(
              path,
              COLLECT_ERROR_CATEGORY.EVALUATION_FAILED,
              'CDP evaluate returned no collection result',
            )
          }

          result = { ...result, path: result.path || path, frameId: frameContext.frameId }
          const hint = await missingValueHint(result, path, listStorageKeys)
          return recordPropertyFact(runtime, hint ? { ...result, hint } : result)
        }

        // === 主框架路径：EnvCollector（page.evaluate，自带超时）===
        const page = await runtime.getPage({ signal })
        const collector = new EnvCollector(page)
        const data = await collector.collect(path, { depth })
        const listStorageKeys = async (storageType) => {
          const keysData = await collector.collect(storageType, { depth: 1 })
          return keysData?.success ? Object.keys(keysData.data?.properties || {}) : []
        }
        const hint = await missingValueHint(data, path, listStorageKeys)
        return recordPropertyFact(runtime, hint ? { ...data, hint } : data)
      } catch (error) {
        failure(error)
      }
    },
  }),
])
