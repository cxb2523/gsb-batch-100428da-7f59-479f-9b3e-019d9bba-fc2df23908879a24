/**
 * DeepSpider - collect_property 统一采集契约
 *
 * 主框架（EnvCollector）与跨域 iframe（CDP Runtime.evaluate）两条采集路径
 * 共用同一套成功/失败结果结构：
 *
 * 成功：{ success:true, path, data, descriptor, ownerDepth, brand,
 *         constructorName, prototypeChain, functionSource }
 * 失败：{ success:false, path, errorCategory, error, hint? }
 *
 * errorCategory 至少区分：
 *  - PROPERTY_NOT_FOUND        属性确实不存在（求值完成但值为 undefined/null）
 *  - EVALUATION_TIMEOUT        求值超时，调用未在时限内返回
 *  - CONTEXT_INVALIDATED       iframe 执行上下文已失效，需要重新 select_frame
 *  - EVALUATION_FAILED         其它求值异常
 *
 * 注意：in-page 的 collectPropertyInRealm 是被 toString 后注入页面执行的，
 * 无法 import 本模块，因此它内部用同值的字符串字面量，修改常量时需同步。
 */

export const COLLECT_ERROR_CATEGORY = Object.freeze({
  PROPERTY_NOT_FOUND: 'property_not_found',
  EVALUATION_TIMEOUT: 'evaluation_timeout',
  CONTEXT_INVALIDATED: 'context_invalidated',
  EVALUATION_FAILED: 'evaluation_failed',
})

/** collect_property 单条属性采集的超时上限（毫秒），iframe 路径同样受此约束 */
export const COLLECT_TIMEOUT_MS = 5000

const MISSING_VALUE_PATTERN = /undefined|null/i
const TIMEOUT_PATTERN = /timeout|timed out|超时/i
const CONTEXT_INVALIDATED_PATTERN = /cannot find context|context.*(?:destroyed|invalidated|released)|execution context was destroyed|isolated world/i

/**
 * 把求值阶段抛出的错误归类为可判别的 errorCategory。
 * @param {unknown} error
 * @returns {string}
 */
export function classifyCollectError(error) {
  const message = error instanceof Error ? error.message : String(error ?? '')
  if (CONTEXT_INVALIDATED_PATTERN.test(message)) return COLLECT_ERROR_CATEGORY.CONTEXT_INVALIDATED
  if (TIMEOUT_PATTERN.test(message)) return COLLECT_ERROR_CATEGORY.EVALUATION_TIMEOUT
  return COLLECT_ERROR_CATEGORY.EVALUATION_FAILED
}

/**
 * 构造统一的失败结果。失败结果永远不会被写入 propertyFacts。
 * @param {string} path
 * @param {string} errorCategory
 * @param {string} error
 */
export function collectFailure(path, errorCategory, error) {
  return {
    success: false,
    path,
    errorCategory,
    error: typeof error === 'string' ? error : String(error ?? 'collection failed'),
  }
}

/**
 * 判断一个采集结果是否属于“属性不存在”类失败（用于决定是否附加缺失值提示）。
 * @param {unknown} result
 */
export function isPropertyNotFoundResult(result) {
  if (result?.success !== false) return false
  if (result.errorCategory) {
    return result.errorCategory === COLLECT_ERROR_CATEGORY.PROPERTY_NOT_FOUND
  }
  // 兼容旧版 in-page 结果只带 error 文本的情况
  return MISSING_VALUE_PATTERN.test(result.error || '')
}

/**
 * 为采集求值增加超时上限，保证底层 Promise 永远挂起时也不会卡住整个采集调用。
 * 超时后返回 EVALUATION_TIMEOUT 失败，而不是向外抛出。
 *
 * @param {Promise<unknown>} pending - 已经启动的采集 Promise
 * @param {object} options
 * @param {string} options.path
 * @param {number} [options.timeout]
 * @param {AbortSignal} [options.signal]
 */
export async function withCollectTimeout(pending, { path, timeout = COLLECT_TIMEOUT_MS, signal } = {}) {
  if (signal?.aborted) throw signal.reason instanceof Error ? signal.reason : new Error('Runtime aborted')

  let timer
  const timeoutReached = new Promise((resolve) => {
    timer = setTimeout(
      () => resolve(collectFailure(path, COLLECT_ERROR_CATEGORY.EVALUATION_TIMEOUT, `collect_property timeout after ${timeout}ms`)),
      timeout,
    )
  })

  const onAbort = () => timer && clearTimeout(timer)
  signal?.addEventListener('abort', onAbort, { once: true })

  const tracked = Promise.resolve(pending)
  // 超时先落定时，pending 之后再 reject 也不能变成 unhandled rejection。
  // 单独挂一个兜底分支；正常路径上的 reject 仍由下方 Promise.race 捕获并上抛。
  tracked.catch(() => {})

  try {
    return await Promise.race([
      tracked.finally(() => clearTimeout(timer)),
      timeoutReached,
    ])
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onAbort)
  }
}
