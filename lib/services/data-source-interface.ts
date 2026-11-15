// 通用数据源接口定义

export type DataSourceType = 'tikhub' | 'tiktok' | 'bilibili' | 'wechat' | 'youtube' | 'xiaohongshu';

export interface DataSourceResult {
  rawTexts: string[];
  videos?: any[];
  metadata?: any;
}

/**
 * 评论获取状态
 * - complete: 所有笔记的评论均获取成功（单篇评论为空也视为成功）
 * - partial:  部分笔记评论获取失败，其余笔记的评论正常保留
 * - missing:  所有笔记的评论均获取失败（笔记本身仍然可用）
 */
export type CommentFetchStatus = 'complete' | 'partial' | 'missing';

/**
 * 汇总多个关键词/多次深层抓取的评论完整度：
 * - 全部 complete → complete
 * - 全部 missing  → missing
 * - 其余混合情况 → partial
 * - 没有任何状态（数据源不返回该字段）→ undefined
 */
export function aggregateCommentStatus(
  statuses: Array<CommentFetchStatus | undefined>
): CommentFetchStatus | undefined {
  const known = statuses.filter((s): s is CommentFetchStatus => !!s);
  if (known.length === 0) {
    return undefined;
  }
  if (known.every(s => s === 'complete')) {
    return 'complete';
  }
  if (known.every(s => s === 'missing')) {
    return 'missing';
  }
  return 'partial';
}

export interface DeepCrawlResult {
  rawTexts: string[];
  videos?: any[];
  allComments?: any[];
  videoCount?: number;
  commentCount?: number;
  /**
   * 评论获取状态，用于上层页面提示数据是否完整。
   * 不支持深度评论的数据源可以不返回该字段。
   */
  commentStatus?: CommentFetchStatus;
  /**
   * 评论获取失败的笔记数量（含分页中途失败但保留了部分评论的笔记）。
   * 注意：单篇评论为空（接口成功返回但没有任何评论）不计入失败。
   */
  failedNoteCount?: number;
  /**
   * 评论获取失败的笔记 id 列表（可选，便于上层定位或重试）。
   */
  failedNoteIds?: string[];
  /**
   * 失败原因摘要（去重后的错误信息，最多保留若干条）。
   */
  failureReasons?: string[];
}

export interface DeepCrawlOptions {
  maxVideos?: number;
  maxCommentsPerVideo?: number;
}

// TikHub/TikTok 爬虫配置选项
export interface TikTokCrawlOptions {
  enableComments: boolean;        // 是否爬取评论
  maxVideos: number;              // 视频数量 (TikTok: 5-50, TikHub: 5-30)
  maxCommentsPerVideo: number;    // 每视频评论数 (10-100)
  enableSubComments: boolean;     // 是否爬取二级评论（仅部分平台支持）
}

export interface IDataSourceService {
  /**
   * 搜索并获取数据
   * @param keywords 关键词
   * @param limit 限制数量
   * @returns 包含原始文本的结果
   */
  searchAndFetch(keywords: string, limit: number): Promise<DataSourceResult>;

  /**
   * 深度抓取（含评论）
   * @param keywords 关键词
   * @param options 深度抓取选项
   * @returns 包含视频和评论的完整结果
   */
  searchWithComments?(keywords: string, options?: DeepCrawlOptions): Promise<DeepCrawlResult>;

  /**
   * 检查数据源是否可用（可选）
   */
  checkAvailability?(): Promise<boolean>;
}

