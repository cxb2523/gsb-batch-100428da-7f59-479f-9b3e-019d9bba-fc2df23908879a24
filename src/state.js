'use strict';

/**
 * 本地状态持久化
 * 记录每日回帖计数、已回帖主题、已见主题与已推送主题,避免重复操作
 *
 * 两组主题集合相互独立:
 *  - seenThreads:版块里出现过的所有主题(无论是否命中关键词、是否推送)
 *  - pushedThreads:实际推送过通知的主题,用于重启后补推且不重复推送
 */

const fs = require('fs');
const path = require('path');

const STATE_FILE = '.t66y-state.json';

function today() {
  return new Date().toISOString().slice(0, 10);
}

class State {
  constructor(filePath) {
    this.filePath = filePath || path.join(process.cwd(), STATE_FILE);
    this.data = { days: {}, repliedThreads: [], seenThreads: [], pushedThreads: [] };
    this.load();
  }

  load() {
    if (fs.existsSync(this.filePath)) {
      try {
        const loaded = JSON.parse(fs.readFileSync(this.filePath, 'utf8'));
        this.data = { ...this.data, ...loaded };

        // 兼容旧版本:旧状态文件只有 seenThreads,没有 pushedThreads。
        // 必须以文件自身是否含该字段为准——默认值里的 pushedThreads
        // 会在展开合并后保留,直接判断 Array.isArray 永远为真。
        // 历史 seen 主题都按「已推送」处理,避免升级后把历史帖当新帖重推。
        if (!Object.prototype.hasOwnProperty.call(loaded, 'pushedThreads')) {
          this.data.pushedThreads = [...this.data.seenThreads];
        } else if (!Array.isArray(this.data.pushedThreads)) {
          this.data.pushedThreads = [];
        }
        if (!Array.isArray(this.data.seenThreads)) {
          this.data.seenThreads = [];
        }
      } catch {
        // 状态文件损坏时从零开始,不影响主流程
      }
    }
  }

  save() {
    // 裁剪历史,防止文件无限膨胀
    this.data.repliedThreads = this.data.repliedThreads.slice(-5000);
    this.data.seenThreads = this.data.seenThreads.slice(-5000);
    this.data.pushedThreads = this.data.pushedThreads.slice(-5000);
    const days = Object.keys(this.data.days).sort().slice(-30);
    this.data.days = Object.fromEntries(days.map((d) => [d, this.data.days[d]]));
    fs.writeFileSync(this.filePath, JSON.stringify(this.data, null, 2));
  }

  /** 今日某账号已回帖数量 */
  replyCountToday(accountName) {
    const day = this.data.days[today()] || {};
    return day[`reply:${accountName}`] || 0;
  }

  incrReplyCount(accountName) {
    const day = this.data.days[today()] || {};
    day[`reply:${accountName}`] = (day[`reply:${accountName}`] || 0) + 1;
    this.data.days[today()] = day;
    this.save();
  }

  hasReplied(threadId) {
    return this.data.repliedThreads.includes(String(threadId));
  }

  markReplied(threadId) {
    this.data.repliedThreads.push(String(threadId));
    this.save();
  }

  hasSeen(threadId) {
    return this.data.seenThreads.includes(String(threadId));
  }

  markSeen(threadId) {
    this.data.seenThreads.push(String(threadId));
    this.save();
  }

  hasPushed(threadId) {
    return this.data.pushedThreads.includes(String(threadId));
  }

  markPushed(threadId) {
    this.data.pushedThreads.push(String(threadId));
    this.save();
  }

  /**
   * 一次性把主题记为「已见 + 已推送」,只落盘一次。
   * 用于成功推送的路径,保证两个集合在同一次写入里同步更新。
   */
  markSeenAndPushed(threadId) {
    const id = String(threadId);
    this.data.seenThreads.push(id);
    this.data.pushedThreads.push(id);
    this.save();
  }

  /**
   * 是否从未建立过基线(seen 集合为空)。
   * 监控据此判断「首次运行」,进程重启后因状态已落盘而返回 false,
   * 从而不会重新建立基线、漏掉停机期间的新帖。
   */
  isSeenEmpty() {
    return this.data.seenThreads.length === 0;
  }
}

module.exports = { State, STATE_FILE };
