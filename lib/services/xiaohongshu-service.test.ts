// Xiaohongshu 深度搜索容错测试
// 运行方式（不依赖真实 TikHub 凭据）:
//   npx ts-node --compiler-options '{"module":"commonjs","moduleResolution":"node"}' lib/services/xiaohongshu-service.test.ts
// 或使用 package.json 中的脚本: npm run test:xiaohongshu

import { XiaohongshuService, XiaohongshuServiceOptions } from './xiaohongshu-service';
import { DeepCrawlResult } from './data-source-interface';

// ==================== 假客户端 ====================

interface FakeNoteSpec {
  id: string;
  title: string;
  desc: string;
  /**
   * 该笔记的评论分页内容；不提供表示接口成功但没有评论。
   * cursor 为“请求下一页时应传入的游标”。
   */
  commentPages?: Array<{ comments: any[]; hasMore: boolean; cursor: string }>;
  /** 模拟该笔记评论请求抛错 */
  failWith?: string;
  /** 模拟该笔记第 N 次翻页（从 0 开始）时抛错，用于测试分页中途失败 */
  failOnPageIndex?: number;
  /** 模拟该笔记评论接口返回非 200 */
  code?: number;
  /** 模拟该笔记评论接口响应缺少 comments 字段 */
  malformed?: boolean;
}

interface FakeClientOptions {
  notes: FakeNoteSpec[];
  /** 是否提供单篇评论接口（false 时只能走批量接口） */
  withPerNoteApi?: boolean;
  /** 批量接口是否整体抛错 */
  batchFails?: boolean;
}

/**
 * 假 TikHub 客户端：只实现 XiaohongshuService 用到的接口，记录每篇笔记的请求次数
 */
class FakeXiaohongshuClient {
  readonly commentCalls = new Map<string, number>();
  readonly searchCalls: Array<Record<string, any>> = [];
  private readonly options: FakeClientOptions;

  constructor(options: FakeClientOptions) {
    this.options = { withPerNoteApi: true, ...options };
    if (!this.options.withPerNoteApi) {
      // 模拟只提供批量接口的客户端：移除单篇接口
      (this as any).getXiaohongshuNoteComments = undefined;
    }
  }

  private note(id: string): FakeNoteSpec {
    const found = this.options.notes.find(n => n.id === id);
    if (!found) throw new Error(`未知笔记: ${id}`);
    return found;
  }

  private recordCommentCall(noteId: string) {
    this.commentCalls.set(noteId, (this.commentCalls.get(noteId) || 0) + 1);
  }

  async searchXiaohongshuNotes(params: { keyword: string; page?: number }) {
    this.searchCalls.push(params);
    const page = params.page || 1;
    // 只提供一页数据，且数量少于 20，触发 searchAndFetch 的分页终止
    const items = page === 1
      ? this.options.notes.map(n => ({
          note: {
            id: n.id,
            title: n.title,
            desc: n.desc,
            user: { nickname: `作者-${n.id}`, userid: `uid-${n.id}` },
            liked_count: 10,
            comments_count: 3,
            timestamp: 1700000000
          }
        }))
      : [];

    return { code: 200, message: 'success', data: { data: { items } } };
  }

  async getXiaohongshuNoteComments(noteId: string, lastCursor?: string) {
    this.recordCommentCall(noteId);
    const note = this.note(noteId);

    if (note.failWith) {
      throw new Error(note.failWith);
    }
    if (note.code && note.code !== 200) {
      return { code: note.code, message: 'api error' };
    }
    if (note.malformed) {
      return { code: 200, message: 'success', data: {} };
    }

    const pages = note.commentPages || [];
    // 第一页（未传游标）对应 pages[0]；之后按“上一页返回的 cursor”定位下一页
    let pageIndex = 0;
    if (lastCursor) {
      const previousIndex = pages.findIndex(p => p.cursor === lastCursor);
      pageIndex = previousIndex >= 0 ? previousIndex + 1 : -1;
    }

    if (note.failOnPageIndex !== undefined && pageIndex === note.failOnPageIndex) {
      throw new Error(`笔记 ${noteId} 第 ${pageIndex + 1} 页评论请求失败: 503 Service Unavailable`);
    }

    if (pageIndex < 0 || pageIndex >= pages.length) {
      // 没有更多评论：接口成功但 comments 为空
      return { code: 200, message: 'success', data: { comments: [], has_more: false, cursor: '' } };
    }

    const page = pages[pageIndex];
    return {
      code: 200,
      message: 'success',
      data: { comments: page.comments, has_more: page.hasMore, cursor: page.cursor }
    };
  }

  async getXiaohongshuNoteCommentsBatch(noteIds: string[], maxCommentsPerNote: number) {
    const result = new Map<string, any[]>();

    if (this.options.batchFails) {
      for (const noteId of noteIds) {
        this.recordCommentCall(noteId);
      }
      throw new Error(`批量评论接口失败（影响 ${noteIds.length} 篇笔记）`);
    }

    for (const noteId of noteIds) {
      this.recordCommentCall(noteId);
      const note = this.note(noteId);
      if (note.failWith) {
        // 模拟批量接口中该笔记被跳过：结果 Map 中没有该键
        continue;
      }
      result.set(noteId, (note.commentPages?.[0]?.comments || []).slice(0, maxCommentsPerNote));
    }
    return result;
  }

  getUsageStats() {
    return { requestCount: this.searchCalls.length, commentsRequests: this.totalCommentCalls() };
  }

  totalCommentCalls(): number {
    let total = 0;
    for (const count of this.commentCalls.values()) total += count;
    return total;
  }
}

// ==================== 测试辅助 ====================

const TEST_OPTIONS: XiaohongshuServiceOptions = {
  maxNotes: 10,
  maxCommentsPerNote: 10,
  requestDelay: 0
};

let passed = 0;
let failed = 0;

function check(name: string, condition: boolean, detail?: any) {
  if (condition) {
    passed++;
    console.log(`  ✅ ${name}`);
  } else {
    failed++;
    console.error(`  ❌ ${name}`, detail === undefined ? '' : detail);
  }
}

function assertEqual(name: string, actual: any, expected: any) {
  check(name, actual === expected, { actual, expected });
}

function makeNote(id: string, extra: Partial<FakeNoteSpec> = {}): FakeNoteSpec {
  return {
    id,
    title: `标题内容足够长的笔记 ${id}`,
    desc: `这是 ${id} 的正文描述，长度超过十个字符以保证被收集。`,
    ...extra
  };
}

function comment(id: string, content: string, noteId: string) {
  return {
    id,
    content,
    note_id: noteId,
    like_count: 1,
    user: { nickname: `评论者-${id}`, userid: `cuid-${id}` },
    time: 1700000000
  };
}

function assertNoteTextsPreserved(result: DeepCrawlResult, notes: FakeNoteSpec[]) {
  check(
    `保留了全部 ${notes.length} 篇笔记`,
    (result.videos || []).length === notes.length,
    (result.videos || []).map(v => v.id)
  );
  assertEqual('videoCount 与笔记数一致', result.videoCount, notes.length);

  for (const note of notes) {
    check(
      `保留了笔记标题: ${note.id}`,
      result.rawTexts.includes(note.title),
      result.rawTexts.slice(0, 10)
    );
    check(
      `保留了笔记描述: ${note.id}`,
      result.rawTexts.includes(note.desc),
      result.rawTexts.slice(0, 10)
    );
  }
}

async function runScenario(
  title: string,
  notes: FakeNoteSpec[],
  clientOptions: Partial<FakeClientOptions>,
  deepOptions: { maxVideos?: number; maxCommentsPerVideo?: number } = {}
): Promise<{ result: DeepCrawlResult; client: FakeXiaohongshuClient }> {
  console.log(`\n=== ${title} ===`);
  const client = new FakeXiaohongshuClient({ notes, ...clientOptions });
  const service = new XiaohongshuService(
    client as any,
    { ...TEST_OPTIONS }
  );
  const result = await service.searchWithComments('测试关键词', deepOptions);
  return { result, client };
}

// ==================== 测试 1：正常路径 ====================

async function testAllCommentsComplete() {
  const notes: FakeNoteSpec[] = [
    makeNote('n1', {
      commentPages: [
        { comments: [comment('c1', '这个功能真的很好用，帮我省了不少时间', 'n1')], hasMore: false, cursor: '' }
      ]
    }),
    // 接口成功但没有任何评论 —— 单篇为空不算失败
    makeNote('n2'),
    makeNote('n3', {
      commentPages: [
        { comments: [comment('c3', '价格有点贵，希望能有学生优惠', 'n3')], hasMore: false, cursor: '' }
      ]
    })
  ];

  const { result, client } = await runScenario('测试 1：评论完整（含单篇评论为空）', notes, {});

  assertEqual('commentStatus 为 complete', result.commentStatus, 'complete');
  assertEqual('failedNoteCount 为 0', result.failedNoteCount, 0);
  assertEqual('failureReasons 为空', (result.failureReasons || []).length, 0);
  assertNoteTextsPreserved(result, notes);
  assertEqual('收集到 2 条评论', result.commentCount, 2);
  check('评论内容与笔记标题均进入 rawTexts', result.rawTexts.includes('这个功能真的很好用，帮我省了不少时间'));
  check(
    '每篇笔记只请求一次评论',
    client.commentCalls.get('n1') === 1 && client.commentCalls.get('n2') === 1 && client.commentCalls.get('n3') === 1,
    Object.fromEntries(client.commentCalls)
  );
}

// ==================== 测试 2：部分失败 ====================

async function testPartialFailure() {
  const notes: FakeNoteSpec[] = [
    makeNote('n1', {
      commentPages: [
        { comments: [comment('c1', '用了一周，确实解决了我的问题', 'n1')], hasMore: false, cursor: '' }
      ]
    }),
    // 抛错：模拟评论接口 500
    makeNote('n2', { failWith: 'Xiaohongshu API 评论获取失败: 500 Internal Server Error' }),
    makeNote('n3', {
      commentPages: [
        { comments: [comment('c3', '界面还可以再简洁一点，现在有点乱', 'n3')], hasMore: false, cursor: '' }
      ]
    })
  ];

  const { result, client } = await runScenario('测试 2：部分笔记评论失败', notes, {});

  assertEqual('commentStatus 为 partial', result.commentStatus, 'partial');
  assertEqual('failedNoteCount 为 1', result.failedNoteCount, 1);
  check('failedNoteIds 指向 n2', (result.failedNoteIds || []).join(',') === 'n2', result.failedNoteIds);
  check('failureReasons 包含失败原因', (result.failureReasons || []).some(r => r.includes('500')));
  assertNoteTextsPreserved(result, notes);
  assertEqual('只保留成功笔记的 2 条评论', result.commentCount, 2);
  check(
    '失败笔记的评论没有进入结果',
    !(result.allComments || []).some(c => c.note_id === 'n2'),
    result.allComments
  );
  assertEqual('后续笔记 n3 的评论仍被处理', (result.allComments || []).filter(c => c.note_id === 'n3').length, 1);
  check(
    '每篇笔记只请求一次评论（失败不重试）',
    client.commentCalls.get('n1') === 1 && client.commentCalls.get('n2') === 1 && client.commentCalls.get('n3') === 1,
    Object.fromEntries(client.commentCalls)
  );
}

// ==================== 测试 3：全部失败 ====================

async function testAllCommentsFailed() {
  const notes: FakeNoteSpec[] = [
    makeNote('n1', { failWith: 'Xiaohongshu API 评论获取失败: 401 Unauthorized' }),
    makeNote('n2'),
  ];
  // n2 使用非 200 响应模拟接口整体异常
  notes[1] = makeNote('n2', { code: 500 });

  const { result, client } = await runScenario('测试 3：全部笔记评论失败', notes, {});

  assertEqual('commentStatus 为 missing', result.commentStatus, 'missing');
  assertEqual('failedNoteCount 为 2', result.failedNoteCount, 2);
  assertEqual('allComments 为空', (result.allComments || []).length, 0);
  assertEqual('commentCount 为 0', result.commentCount, 0);
  assertNoteTextsPreserved(result, notes);
  check('failureReasons 有内容', (result.failureReasons || []).length > 0, result.failureReasons);
  assertEqual('Promise 未拒绝：返回了结果对象而非异常', typeof result.rawTexts, 'object');
  check(
    '每篇笔记只请求一次评论（失败不重试）',
    client.commentCalls.get('n1') === 1 && client.commentCalls.get('n2') === 1,
    Object.fromEntries(client.commentCalls)
  );
}

// ==================== 测试 4：maxVideos / maxCommentsPerVideo 仍然生效 ====================

async function testLimitsStillApply() {
  const notes: FakeNoteSpec[] = Array.from({ length: 5 }, (_, i) =>
    makeNote(`n${i + 1}`, {
      commentPages: [
        {
          comments: Array.from({ length: 5 }, (_, j) =>
            comment(`c${i + 1}-${j + 1}`, `这是第 ${i + 1} 篇笔记的第 ${j + 1} 条足够长的评论内容`, `n${i + 1}`)
          ),
          hasMore: false,
          cursor: ''
        }
      ]
    })
  );

  const { result } = await runScenario('测试 4：笔记数 / 每篇评论上限生效', notes, {}, {
    maxVideos: 2,
    maxCommentsPerVideo: 3
  });

  assertEqual('笔记数按 maxVideos 截断', result.videoCount, 2);
  assertEqual('每篇评论按 maxCommentsPerVideo 截断', result.commentCount, 6);
  assertEqual('commentStatus 仍为 complete', result.commentStatus, 'complete');
}

// ==================== 测试 5：多页评论分页与游标不推进 ====================

async function testCommentPagination() {
  const notes: FakeNoteSpec[] = [
    makeNote('n1', {
      commentPages: [
        { comments: [comment('p1-1', '第一页的第一条足够长的评论', 'n1'), comment('p1-2', '第一页的第二条足够长的评论', 'n1')], hasMore: true, cursor: 'cursor-2' },
        { comments: [comment('p2-1', '第二页的第一条足够长的评论', 'n1')], hasMore: false, cursor: '' }
      ]
    }),
    // has_more 为 true 但游标为空：不应重复请求同一页
    makeNote('n2', {
      commentPages: [
        { comments: [comment('q1', '游标异常时的第一条足够长的评论', 'n2')], hasMore: true, cursor: '' }
      ]
    })
  ];

  const { result, client } = await runScenario('测试 5：评论分页与游标异常保护', notes, {}, {
    maxVideos: 2,
    maxCommentsPerVideo: 10
  });

  assertEqual('分页后收集到 4 条评论', result.commentCount, 4);
  assertEqual('commentStatus 为 complete', result.commentStatus, 'complete');
  assertEqual('n1 请求了两页', client.commentCalls.get('n1'), 2);
  assertEqual('n2 游标为空时只请求一次', client.commentCalls.get('n2'), 1);
}

// ==================== 测试 6：只有批量接口的客户端 ====================

async function testBatchOnlyClientPartial() {
  const notes: FakeNoteSpec[] = [
    makeNote('n1', { commentPages: [{ comments: [comment('b1', '批量接口成功返回的足够长评论', 'n1')], hasMore: false, cursor: '' }] }),
    makeNote('n2', { failWith: '批量评论接口失败' })
  ];

  const client = new FakeXiaohongshuClient({ notes, withPerNoteApi: false });
  const service = new XiaohongshuService(client as any, { ...TEST_OPTIONS });

  console.log('\n=== 测试 6：只有批量接口的客户端（部分失败） ===');
  const result = await service.searchWithComments('测试关键词', { maxVideos: 2, maxCommentsPerVideo: 10 });

  assertEqual('commentStatus 为 partial', result.commentStatus, 'partial');
  assertEqual('failedNoteCount 为 1', result.failedNoteCount, 1);
  assertEqual('成功笔记的评论被保留', result.commentCount, 1);
  assertNoteTextsPreserved(result, notes);
  check(
    '每篇笔记只被批量接口请求一次',
    client.commentCalls.get('n1') === 1 && client.commentCalls.get('n2') === 1,
    Object.fromEntries(client.commentCalls)
  );
}

// ==================== 测试 7：批量接口整体抛错 ====================

async function testBatchOnlyClientAllFail() {
  const notes = [makeNote('n1'), makeNote('n2')];
  const client = new FakeXiaohongshuClient({ notes, withPerNoteApi: false, batchFails: true });
  const service = new XiaohongshuService(client as any, { ...TEST_OPTIONS });

  console.log('\n=== 测试 7：批量接口整体抛错（笔记仍保留） ===');
  const result = await service.searchWithComments('测试关键词', { maxVideos: 2, maxCommentsPerVideo: 10 });

  assertEqual('commentStatus 为 missing', result.commentStatus, 'missing');
  assertEqual('failedNoteCount 为 2', result.failedNoteCount, 2);
  assertEqual('allComments 为空', (result.allComments || []).length, 0);
  assertNoteTextsPreserved(result, notes);
  check('failureReasons 记录了批量失败原因', (result.failureReasons || []).some(r => r.includes('批量')));
}

// ==================== 测试 8：搜索为空 ====================

async function testNoNotes() {
  const { result } = await runScenario('测试 8：搜索无笔记', [], {});
  assertEqual('笔记数为 0', result.videoCount, 0);
  assertEqual('commentStatus 为 complete（没有失败）', result.commentStatus, 'complete');
  assertEqual('failedNoteCount 为 0', result.failedNoteCount, 0);
}

// ==================== 测试 9：分页中途失败保留已取到的评论 ====================

async function testPartialPaginationFailure() {
  const notes: FakeNoteSpec[] = [
    makeNote('n1', {
      commentPages: [
        { comments: [comment('pp1', '第一页已经拿到的足够长评论', 'n1')], hasMore: true, cursor: 'cursor-2' },
        { comments: [comment('pp2', '第二页永远拿不到的评论内容', 'n1')], hasMore: false, cursor: '' }
      ],
      // 第二页失败
      failOnPageIndex: 1
    }),
    makeNote('n2', {
      commentPages: [
        { comments: [comment('q1', '另一篇笔记正常返回的足够长评论', 'n2')], hasMore: false, cursor: '' }
      ]
    })
  ];

  const { result, client } = await runScenario('测试 9：分页中途失败保留已取到的评论', notes, {}, {
    maxVideos: 2,
    maxCommentsPerVideo: 10
  });

  assertEqual('commentStatus 为 partial', result.commentStatus, 'partial');
  assertEqual('failedNoteCount 为 1', result.failedNoteCount, 1);
  check('failedNoteIds 指向 n1', (result.failedNoteIds || []).join(',') === 'n1', result.failedNoteIds);
  check('failureReasons 记录了分页失败', (result.failureReasons || []).some(r => r.includes('503')));
  assertEqual('保留了第一页的评论 + 另一篇笔记的评论', result.commentCount, 2);
  check(
    '第一页已取到的评论没有丢失',
    (result.allComments || []).some(c => c.comment_id === 'pp1')
  );
  assertEqual('失败页面之后没有重复请求第一页', client.commentCalls.get('n1'), 2);
}

// ==================== 入口 ====================

async function main() {
  console.log('\n🧪 Xiaohongshu 深度搜索容错测试\n');

  await testAllCommentsComplete();
  await testPartialFailure();
  await testAllCommentsFailed();
  await testLimitsStillApply();
  await testCommentPagination();
  await testBatchOnlyClientPartial();
  await testBatchOnlyClientAllFail();
  await testNoNotes();
  await testPartialPaginationFailure();

  console.log(`\n===== 结果: ${passed} 通过, ${failed} 失败 =====\n`);
  if (failed > 0) {
    process.exit(1);
  }
}

main().catch(error => {
  console.error('测试执行失败:', error);
  process.exit(1);
});
