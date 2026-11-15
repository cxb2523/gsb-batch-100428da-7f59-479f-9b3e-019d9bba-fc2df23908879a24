'use strict';

/**
 * 新帖监控
 * 轮询版块,发现新主题(或命中关键词)时推送通知
 *
 * 增量发现规则:
 *  - 首次运行(状态中没有任何已见主题)只建立基线,把当前主题全部记为已见,不推送
 *  - 之后每轮:未见过的主题一律记为已见(关键词只影响推送,不影响标记),
 *    命中关键词且未推送过的主题才推送,并记入已推送集合
 *  - 「已见」与「已推送」分开落盘:进程重启后停机期间出现的新帖仍会被补推,
 *    而已推送过的主题不会重复推送
 */

const { listThreads } = require('./reply');
const { sleep } = require('./client');

/**
 * 执行一轮监控扫描
 *
 * @param {T66yClient} client
 * @param {object} monitorConfig 配置中的 monitor 段
 * @param {State} state
 * @param {object} [hooks] { onLog(msg), onNotify(title, body) }
 * @returns {Promise<{firstRun:boolean, total:number, pushed:number}>}
 *   抓取或解析失败时抛出异常,由调用方决定如何记录与继续
 */
async function runMonitorRound(client, monitorConfig, state, hooks = {}) {
  const log = hooks.onLog || (() => {});
  const { boardFid, keywords } = monitorConfig;

  // 以落盘状态判断是否首次运行:重启后 seen 非空,不会重新建立基线
  const firstRun = state.isSeenEmpty();
  const threads = await listThreads(client, boardFid);
  let pushed = 0;

  for (const thread of threads) {
    // 已见主题直接跳过,避免重复扫描与重复推送
    if (state.hasSeen(thread.id)) continue;

    // 首次运行:全部记为已见建立基线,不推送
    if (firstRun) {
      state.markSeen(thread.id);
      continue;
    }

    const hit =
      !keywords.length || keywords.some((kw) => thread.title.toLowerCase().includes(kw.toLowerCase()));

    if (!hit) {
      // 关键词只影响推送、不影响已见标记:未命中也记为已见,避免后续反复扫描
      state.markSeen(thread.id);
      continue;
    }

    // 已推送过的不再推送(重启补推场景下的去重保障),并补齐已见标记
    if (state.hasPushed(thread.id)) {
      state.markSeen(thread.id);
      continue;
    }

    // 推送成功后才同时记入「已见 + 已推送」。
    // 若 onNotify 抛错,本帖不会被标记,下一轮自动重试,不会静默丢失。
    const url = thread.url.startsWith('http') ? thread.url : `${client.baseUrl}/${thread.url}`;
    log(`新帖:《${thread.title}》 ${url}`);
    if (hooks.onNotify) {
      await hooks.onNotify('t66y 新帖提醒', `《${thread.title}》\n${url}`);
    }
    state.markSeenAndPushed(thread.id);
    pushed += 1;
  }

  if (firstRun) {
    log(`基线建立完成,已记录 ${threads.length} 个现有主题`);
  } else if (pushed > 0) {
    log(`本轮发现并推送 ${pushed} 个新帖`);
  }

  return { firstRun, total: threads.length, pushed };
}

/**
 * 启动监控循环(长驻进程)
 * @param {T66yClient} client
 * @param {object} monitorConfig 配置中的 monitor 段
 * @param {State} state
 * @param {object} [hooks] { onLog(msg), onNotify(title, body), sleep(ms) }
 */
async function runMonitor(client, monitorConfig, state, hooks = {}) {
  const log = hooks.onLog || (() => {});
  const doSleep = hooks.sleep || sleep;
  const { boardFid, intervalSeconds } = monitorConfig;

  log(`开始监控版块 fid=${boardFid},每 ${intervalSeconds} 秒检查一次`);
  if (monitorConfig.keywords.length) log(`关键词过滤:${monitorConfig.keywords.join(' / ')}`);

  for (;;) {
    try {
      await runMonitorRound(client, monitorConfig, state, hooks);
    } catch (err) {
      // 轮询出错只记录日志,继续下一轮,不让长驻进程退出
      log(`监控轮询出错:${err.message}`);
    }

    await doSleep(intervalSeconds * 1000);
  }
}

module.exports = { runMonitor, runMonitorRound };
