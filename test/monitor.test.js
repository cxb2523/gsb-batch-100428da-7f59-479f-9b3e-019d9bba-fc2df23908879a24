'use strict';

/**
 * 新帖监控增量发现测试
 *
 * 用本地构造的版块 HTML 与临时状态文件驱动 listThreads 与监控循环,
 * 不访问真实论坛。覆盖:
 *  1. listThreads 解析本地 HTML
 *  2. 首次运行只建立基线、不推送
 *  3. 增量推送 + 关键词只影响推送不影响已见标记
 *  4. 重启后补推停机期间新帖,且不重复推送
 *  5. 旧版状态文件(仅 seenThreads)兼容:不报错、不重推历史帖
 *  6. 监控轮询出错记录日志后继续下一轮
 *  7. 推送失败时不标记已推送,下一轮可重试且不重复
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const os = require('os');

const { listThreads } = require('../src/reply');
const { runMonitor, runMonitorRound } = require('../src/monitor');
const { State } = require('../src/state');

const BASE_URL = 'https://example-t66y.test';

/** 构造版块列表 HTML */
function boardHtml(titles) {
  const links = titles
    .map(
      (title, i) =>
        `<a href="htm_data/2026/8/${1000 + i}.html">${title}</a>`
    )
    .join('\n');
  return `<html><body><div id="threadlist">${links}</div></body></html>`;
}

/** 最小化假客户端:按调用次序返回预设 HTML */
function fakeClient(pages) {
  let cursor = 0;
  return {
    baseUrl: BASE_URL,
    async get() {
      const html = pages[Math.min(cursor, pages.length - 1)];
      cursor += 1;
      return { status: 200, html, url: `${BASE_URL}/thread0806.php` };
    },
  };
}

function tempStatePath() {
  return path.join(os.tmpdir(), `t66y-monitor-state-${process.pid}-${Math.random().toString(36).slice(2)}.json`);
}

function monitorConfig(overrides = {}) {
  return { boardFid: 8, intervalSeconds: 300, keywords: [], ...overrides };
}

function collectNotifies() {
  const events = [];
  return {
    events,
    onNotify(title, body) {
      events.push({ title, body });
    },
  };
}

test('listThreads 可解析本地构造的版块 HTML 并按 tid 去重', async () => {
  // 同一 tid 出现两次(不同链接形式),应只保留一条
  const html = [
    '<a href="htm_data/2026/8/1000.html">第一个主题标题够长</a>',
    '<a href="read.php?tid=1001">第二个主题标题也够长</a>',
    '<a href="read.php?tid=1000">第一个主题标题够长</a>',
  ].join('\n');
  const client = fakeClient([html]);
  const threads = await listThreads(client, 8);

  assert.strictEqual(threads.length, 2);
  assert.strictEqual(threads[0].id, '1000');
  assert.strictEqual(threads[0].title, '第一个主题标题够长');
  assert.ok(threads[0].url.includes('1000.html'));
  assert.strictEqual(threads[1].id, '1001');
});

test('首次运行只建立基线:全部记为已见、不推送,重载状态后仍视为已建立基线', async () => {
  const stateFile = tempStatePath();
  try {
    const state = new State(stateFile);
    const client = fakeClient([boardHtml(['历史主题甲标题', '历史主题乙标题'])]);
    const notifier = collectNotifies();
    const logs = [];

    const result = await runMonitorRound(
      client,
      monitorConfig(),
      state,
      { onLog: (m) => logs.push(m), onNotify: notifier.onNotify }
    );

    assert.strictEqual(result.firstRun, true);
    assert.strictEqual(result.pushed, 0);
    assert.strictEqual(notifier.events.length, 0);
    assert.strictEqual(state.hasSeen('1000'), true);
    assert.strictEqual(state.hasSeen('1001'), true);
    assert.strictEqual(state.hasPushed('1000'), false);
    assert.ok(logs.some((m) => m.includes('基线建立完成')));

    // 用同一状态文件重新加载,模拟进程重启:seen 非空,不再是首次运行
    const reloaded = new State(stateFile);
    assert.strictEqual(reloaded.isSeenEmpty(), false);
  } finally {
    if (fs.existsSync(stateFile)) fs.unlinkSync(stateFile);
  }
});

test('增量推送:新帖推送;未命中关键词的新帖只标记已见不推送;无新帖时不重复推送', async () => {
  const stateFile = tempStatePath();
  try {
    const state = new State(stateFile);
    const config = monitorConfig({ keywords: ['合集'] });
    const hooks = (notifier) => ({ onLog: () => {}, onNotify: notifier.onNotify });

    // 第一轮:基线(含一个与新帖同关键词的历史帖,确保历史帖不被推送)
    const client1 = fakeClient([boardHtml(['历史普通主题标题', '历史合集主题标题'])]);
    await runMonitorRound(client1, config, state, hooks(collectNotifies()));

    // 第二轮:新增两帖,一个命中关键词、一个不命中
    const notifier2 = collectNotifies();
    const client2 = fakeClient([
      boardHtml(['历史普通主题标题', '历史合集主题标题', '全新合集资源发布', '普通闲聊水帖标题']),
    ]);
    const round2 = await runMonitorRound(client2, config, state, hooks(notifier2));

    assert.strictEqual(round2.firstRun, false);
    assert.strictEqual(round2.pushed, 1);
    assert.strictEqual(notifier2.events.length, 1);
    assert.ok(notifier2.events[0].body.includes('全新合集资源发布'));
    assert.ok(notifier2.events[0].body.startsWith('《全新合集资源发布》'));

    // 未命中关键词的新帖同样记为已见,但不在已推送集合中
    assert.strictEqual(state.hasSeen('1003'), true);
    assert.strictEqual(state.hasPushed('1003'), false);
    assert.strictEqual(state.hasPushed('1002'), true);

    // 第三轮:版块无变化,不应再推送任何主题
    const notifier3 = collectNotifies();
    const client3 = fakeClient([
      boardHtml(['历史普通主题标题', '历史合集主题标题', '全新合集资源发布', '普通闲聊水帖标题']),
    ]);
    const round3 = await runMonitorRound(client3, config, state, hooks(notifier3));
    assert.strictEqual(round3.pushed, 0);
    assert.strictEqual(notifier3.events.length, 0);
  } finally {
    if (fs.existsSync(stateFile)) fs.unlinkSync(stateFile);
  }
});

test('重启补推:停机期间出现的新帖在重启后推送,且同一帖不会重复推送', async () => {
  const stateFile = tempStatePath();
  try {
    // 停机前:建立基线
    const stateBefore = new State(stateFile);
    const beforeClient = fakeClient([boardHtml(['停机前的主题标题甲', '停机前的主题标题乙'])]);
    await runMonitorRound(beforeClient, monitorConfig(), stateBefore, { onLog: () => {} });
    assert.strictEqual(stateBefore.hasSeen('1000'), true);

    // 进程重启:停机期间新增 1002,恢复后第一轮即补推
    const stateAfter = new State(stateFile);
    assert.strictEqual(stateAfter.isSeenEmpty(), false);
    const notifier = collectNotifies();
    const afterClient = fakeClient([
      boardHtml(['停机前的主题标题甲', '停机前的主题标题乙', '停机期间冒出的新帖']),
    ]);
    const round = await runMonitorRound(
      afterClient,
      monitorConfig(),
      stateAfter,
      { onLog: () => {}, onNotify: notifier.onNotify }
    );

    assert.strictEqual(round.firstRun, false);
    assert.strictEqual(round.pushed, 1);
    assert.strictEqual(notifier.events.length, 1);
    assert.ok(notifier.events[0].body.includes('停机期间冒出的新帖'));
    assert.strictEqual(stateAfter.hasPushed('1002'), true);

    // 再次重启:同一帖已在 pushed 集合,不重复推送
    const stateAgain = new State(stateFile);
    const notifierAgain = collectNotifies();
    const againClient = fakeClient([
      boardHtml(['停机前的主题标题甲', '停机前的主题标题乙', '停机期间冒出的新帖']),
    ]);
    const roundAgain = await runMonitorRound(
      againClient,
      monitorConfig(),
      stateAgain,
      { onLog: () => {}, onNotify: notifierAgain.onNotify }
    );
    assert.strictEqual(roundAgain.pushed, 0);
    assert.strictEqual(notifierAgain.events.length, 0);
  } finally {
    if (fs.existsSync(stateFile)) fs.unlinkSync(stateFile);
  }
});

test('旧版状态文件兼容:只有 seenThreads 时加载不报错,历史帖不被当新帖重推', async () => {
  const stateFile = tempStatePath();
  try {
    // 手工写入旧版本结构(无 pushedThreads 字段)
    const legacy = {
      days: {},
      repliedThreads: [],
      seenThreads: ['1000', '1001'],
    };
    fs.writeFileSync(stateFile, JSON.stringify(legacy));

    const state = new State(stateFile); // 不应抛错
    assert.deepStrictEqual(state.data.seenThreads, ['1000', '1001']);
    // 旧 seen 主题迁移为已推送,避免重推
    assert.deepStrictEqual(state.data.pushedThreads, ['1000', '1001']);
    assert.strictEqual(state.hasPushed('1000'), true);
    assert.strictEqual(state.isSeenEmpty(), false);

    const notifier = collectNotifies();
    const client = fakeClient([
      boardHtml(['旧版历史主题标题甲', '旧版历史主题标题乙', '升级后才出现的新主题']),
    ]);
    const round = await runMonitorRound(
      client,
      monitorConfig(),
      state,
      { onLog: () => {}, onNotify: notifier.onNotify }
    );

    // 只推送升级后新增的 1002,两个历史帖不重推
    assert.strictEqual(round.pushed, 1);
    assert.strictEqual(notifier.events.length, 1);
    assert.ok(notifier.events[0].body.includes('升级后才出现的新主题'));

    // 落盘后已升级为新结构
    const persisted = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
    assert.ok(Array.isArray(persisted.pushedThreads));
    assert.ok(persisted.pushedThreads.includes('1002'));
  } finally {
    if (fs.existsSync(stateFile)) fs.unlinkSync(stateFile);
  }
});

test('监控轮询出错时记录日志并继续下一轮', async () => {
  const stateFile = tempStatePath();
  try {
    const state = new State(stateFile);
    const logs = [];
    const notifier = collectNotifies();

    let call = 0;
    const flakyClient = {
      baseUrl: BASE_URL,
      async get() {
        call += 1;
        if (call === 1) throw new Error('network down');
        return { status: 200, html: boardHtml(['恢复之后抓到的新帖标题']) };
      },
    };

    let sleepCalls = 0;
    const sleepHook = () => {
      sleepCalls += 1;
      // 第二轮跑完后结束长驻循环
      if (sleepCalls >= 2) throw new Error('stop-loop');
      return Promise.resolve();
    };

    await assert.rejects(
      runMonitor(flakyClient, monitorConfig({ intervalSeconds: 0 }), state, {
        onLog: (m) => logs.push(m),
        onNotify: notifier.onNotify,
        sleep: sleepHook,
      }),
      /stop-loop/
    );

    // 第一轮失败被捕获并记录,第二轮成功建立基线
    assert.ok(logs.some((m) => m.includes('监控轮询出错') && m.includes('network down')));
    assert.strictEqual(call, 2);
    assert.strictEqual(state.hasSeen('1000'), true);
    assert.strictEqual(notifier.events.length, 0); // 成功的那轮是首次基线,不推送
  } finally {
    if (fs.existsSync(stateFile)) fs.unlinkSync(stateFile);
  }
});

test('推送失败时不标记已推送,下一轮重试且不会产生重复通知', async () => {
  const stateFile = tempStatePath();
  try {
    const state = new State(stateFile);
    const config = monitorConfig();

    await runMonitorRound(fakeClient([boardHtml(['基线主题标题内容'])]), config, state, { onLog: () => {} });

    // 第二轮出现新帖,但 onNotify 抛错
    const failingNotifier = {
      events: [],
      async onNotify() {
        throw new Error('telegram unavailable');
      },
    };
    const client1 = fakeClient([boardHtml(['基线主题标题内容', '需要重试推送的新帖'])]);
    await assert.rejects(
      runMonitorRound(client1, config, state, { onLog: () => {}, onNotify: failingNotifier.onNotify }),
      /telegram unavailable/
    );
    // 推送失败时既不标记已见也不标记已推送,下一轮可重试,不会静默丢帖
    assert.strictEqual(state.hasSeen('1001'), false);
    assert.strictEqual(state.hasPushed('1001'), false);

    // 下一轮推送恢复:补推一次且仅一次
    const notifier = collectNotifies();
    const client2 = fakeClient([boardHtml(['基线主题标题内容', '需要重试推送的新帖'])]);
    const round = await runMonitorRound(client2, config, state, {
      onLog: () => {},
      onNotify: notifier.onNotify,
    });
    assert.strictEqual(round.pushed, 1);
    assert.strictEqual(notifier.events.length, 1);
    assert.ok(notifier.events[0].body.includes('需要重试推送的新帖'));
    assert.strictEqual(state.hasPushed('1001'), true);
  } finally {
    if (fs.existsSync(stateFile)) fs.unlinkSync(stateFile);
  }
});

test('状态文件损坏时静默从零开始,不抛错且两个集合均为空数组', () => {
  const stateFile = tempStatePath();
  try {
    fs.writeFileSync(stateFile, '{ this is not valid json');
    const state = new State(stateFile);
    assert.deepStrictEqual(state.data.seenThreads, []);
    assert.deepStrictEqual(state.data.pushedThreads, []);
    assert.strictEqual(state.isSeenEmpty(), true);
  } finally {
    if (fs.existsSync(stateFile)) fs.unlinkSync(stateFile);
  }
});

test('全新状态文件(无历史)首次轮询即基线,空版块不会误判为已建立基线', async () => {
  const stateFile = tempStatePath();
  try {
    const state = new State(stateFile);
    assert.strictEqual(state.isSeenEmpty(), true);

    // 第一轮版块为空:没有任何主题可记录,仍属首次运行
    const emptyClient = fakeClient([boardHtml([])]);
    const r1 = await runMonitorRound(emptyClient, monitorConfig(), state, { onLog: () => {} });
    assert.strictEqual(r1.firstRun, true);
    assert.strictEqual(state.isSeenEmpty(), true);

    // 版块稍后出现主题:依旧按首次基线处理,不推送
    const notifier = collectNotifies();
    const client = fakeClient([boardHtml(['空版块之后才出现的主题'])]);
    const r2 = await runMonitorRound(client, monitorConfig(), state, {
      onLog: () => {},
      onNotify: notifier.onNotify,
    });
    assert.strictEqual(r2.firstRun, true);
    assert.strictEqual(notifier.events.length, 0);
    assert.strictEqual(state.hasSeen('1000'), true);
    assert.strictEqual(state.hasPushed('1000'), false);
  } finally {
    if (fs.existsSync(stateFile)) fs.unlinkSync(stateFile);
  }
});

test('save 会同时裁剪 pushedThreads,防止状态文件无限膨胀', () => {
  const stateFile = tempStatePath();
  try {
    const state = new State(stateFile);
    for (let i = 0; i < 5005; i += 1) {
      state.data.pushedThreads.push(String(i));
      state.data.seenThreads.push(String(i));
    }
    state.save();
    const reloaded = new State(stateFile);
    assert.strictEqual(reloaded.data.pushedThreads.length, 5000);
    assert.strictEqual(reloaded.data.seenThreads.length, 5000);
    // 保留的是最新的一批
    assert.strictEqual(reloaded.data.pushedThreads[reloaded.data.pushedThreads.length - 1], '5004');
  } finally {
    if (fs.existsSync(stateFile)) fs.unlinkSync(stateFile);
  }
});
