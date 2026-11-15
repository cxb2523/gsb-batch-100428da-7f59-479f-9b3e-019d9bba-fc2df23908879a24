// Xiaohongshu (小红书) 数据源服务
// 实现 IDataSourceService 接口，将 Xiaohongshu API 数据转换为系统通用格式

import {
  TikHubAPIClient,
  createTikHubClient
} from './tikhub-client';
import {
  IDataSourceService,
  DataSourceResult,
  DeepCrawlResult,
  DeepCrawlOptions,
  CommentFetchStatus
} from './data-source-interface';

/**
 * Xiaohongshu 服务选项
 */
export interface XiaohongshuServiceOptions {
  maxNotes?: number;
  maxCommentsPerNote?: number;
  enableCache?: boolean;
  requestDelay?: number; // 请求间隔（毫秒）
}

/**
 * Xiaohongshu 数据源服务
 */
export class XiaohongshuService implements IDataSourceService {
  private client: TikHubAPIClient;
  private defaultOptions: XiaohongshuServiceOptions;

  constructor(client?: TikHubAPIClient, options?: XiaohongshuServiceOptions) {
    this.client = client || createTikHubClient();
    this.defaultOptions = {
      maxNotes: 20,
      maxCommentsPerNote: 20,
      enableCache: true,
      requestDelay: 500
    };

    if (options) {
      Object.assign(this.defaultOptions, options);
    }
  }

  /**
   * 基础搜索（不含评论）
   */
  async searchAndFetch(keyword: string, limit: number): Promise<DataSourceResult> {
    console.log(`[Xiaohongshu Service] 开始搜索关键词: ${keyword}, 限制: ${limit}`);

    const rawTexts: string[] = [];
    const videos: any[] = [];
    let totalFetched = 0;

    try {
      // 分页搜索，直到获取足够的数据或没有更多结果
      while (totalFetched < limit) {
        console.log(`[Xiaohongshu Service] 搜索第 ${Math.floor(totalFetched / 20) + 1} 页`);

        const searchResult = await this.client['searchXiaohongshuNotes']({
          keyword,
          page: Math.floor(totalFetched / 20) + 1,
          sort: 'general',
          noteType: '_0'
        });

        console.log('[Xiaohongshu Service] API 响应 code:', searchResult.code);

        if (searchResult.code !== 200) {
          throw new Error(`Xiaohongshu API 搜索失败: ${searchResult.message}`);
        }

        // Xiaohongshu API 响应格式: { code, data: { data: { items: [...] } } }
        const itemsList = searchResult.data?.data?.items;

        if (!itemsList || !Array.isArray(itemsList)) {
          console.warn('[Xiaohongshu Service] 未找到有效的数据数组');
          break;
        }

        console.log(`[Xiaohongshu Service] 第 ${Math.floor(totalFetched / 20) + 1} 页获取到 ${itemsList.length} 个结果`);

        if (itemsList.length === 0) {
          console.warn('[Xiaohongshu Service] 没有更多结果，停止分页');
          break;
        }

        // 处理当前页的结果
        const remainingLimit = limit - totalFetched;
        const pageItems = itemsList.slice(0, remainingLimit);

        for (const item of pageItems) {
          const note = this.convertSearchResultToNote(item, keyword);
          if (note) {
            videos.push(note);

            // 提取文本内容
            if (note.title && note.title.length > 5) {
              rawTexts.push(note.title);
            }
            if (note.description && note.description.length > 10 && note.description !== note.title) {
              rawTexts.push(note.description);
            }
            totalFetched++;
          }
        }

        console.log(`[Xiaohongshu Service] 当前页处理结果: 累计笔记 ${videos.length}, 累计文本 ${rawTexts.length}`);

        // 如果当前页的结果少于请求数量，说明没有更多结果了
        if (itemsList.length < 20) {
          break;
        }

        // 避免请求过快
        await new Promise(resolve => setTimeout(resolve, 500));
      }

      // 去重
      const uniqueTexts = [...new Set(rawTexts)].filter(t => t.trim().length > 5);

      console.log(`[Xiaohongshu Service] 搜索完成: 获取 ${videos.length} 个笔记, ${uniqueTexts.length} 条文本`);

      return {
        rawTexts: uniqueTexts,
        videos,
        metadata: {
          source: 'xiaohongshu',
          keyword,
          totalResults: videos.length,
          returnedResults: videos.length,
          usage: this.client['getUsageStats']()
        }
      };
    } catch (error) {
      console.error('[Xiaohongshu Service] 搜索失败:', error);
      throw error;
    }
  }

  /**
   * 深度搜索（含评论）
   *
   * 设计原则：笔记必得、评论尽力而为。
   * - 搜索阶段失败会正常抛出（没有笔记就没有可分析的数据）
   * - 评论阶段逐篇隔离：单篇失败只记录失败原因，不影响其他笔记，也不丢弃已转换的笔记文本
   */
  async searchWithComments(keyword: string, options: DeepCrawlOptions): Promise<DeepCrawlResult> {
    console.log(`[Xiaohongshu Service] 开始深度搜索: ${keyword}`);

    // 合并选项
    const maxNotes = options.maxVideos || this.defaultOptions.maxNotes!;
    const maxCommentsPerNote = options.maxCommentsPerVideo || this.defaultOptions.maxCommentsPerNote!;

    // 先搜索笔记（失败则整体失败：笔记是必须的产出）
    const searchResult = await this.searchAndFetch(keyword, maxNotes);
    // videos 在 DeepCrawlResult 中是可选字段，这里统一成数组避免后续判空
    const noteList = searchResult.videos || [];

    if (noteList.length === 0) {
      return {
        rawTexts: [],
        videos: [],
        allComments: [],
        videoCount: 0,
        commentCount: 0,
        commentStatus: 'complete',
        failedNoteCount: 0,
        failureReasons: []
      };
    }

    console.log(`[Xiaohongshu Service] 开始获取 ${noteList.length} 个笔记的评论`);

    // 获取评论
    const allComments: any[] = [];
    const rawTexts: string[] = [];
    const commentTexts: string[] = [];

    // 从笔记结果中提取文本（无论评论是否成功，这部分都必须保留）
    for (const note of noteList) {
      if (note.title && note.title.length > 5) {
        rawTexts.push(note.title);
      }
      if (note.description && note.description.length > 10) {
        rawTexts.push(note.description);
      }
    }

    // 每篇笔记只请求一次评论：成功结果缓存，失败不重试
    const commentsByNote = new Map<string, any[]>();
    const failedNoteIds: string[] = [];
    const failureReasons: string[] = [];
    let failedNoteCount = 0;
    // 已取到部分评论但分页中途失败：计入失败，但保留已获取的评论
    let partialNoteCount = 0;

    const hasPerNoteApi = typeof this.client['getXiaohongshuNoteComments'] === 'function';

    if (hasPerNoteApi) {
      // 逐篇抓取：单篇异常被隔离，不影响后续笔记
      for (const note of noteList) {
        if (!note.id) {
          // 没有 id 的笔记无法查询评论，计入失败
          failedNoteCount++;
          failureReasons.push('笔记缺少 id，无法获取评论');
          continue;
        }

        try {
          const { comments, partialFailureReason } =
            await this.fetchCommentsForNote(note.id, maxCommentsPerNote);
          commentsByNote.set(note.id, comments);

          if (partialFailureReason) {
            failedNoteCount++;
            partialNoteCount++;
            failedNoteIds.push(note.id);
            failureReasons.push(partialFailureReason);
          }
        } catch (error) {
          const reason = this.describeError(error);
          console.error(`[Xiaohongshu Service] 笔记 ${note.id} 评论获取失败:`, reason);
          failedNoteCount++;
          failedNoteIds.push(note.id);
          failureReasons.push(reason);
        }

        // 添加请求延迟（失败不会重试，因此每篇至多请求一次）
        await new Promise(resolve => setTimeout(resolve, this.defaultOptions.requestDelay!));
      }
    } else {
      // 客户端没有单篇接口时，退回批量接口并只调用一次。
      // 批量调用整体失败时不逐篇重试，避免重复请求同一笔记的评论。
      for (const note of noteList) {
        if (!note.id) {
          failedNoteCount++;
          failureReasons.push('笔记缺少 id，无法获取评论');
        }
      }

      const noteIds = noteList
        .map(v => v.id)
        .filter((id): id is string => !!id);

      try {
        const commentsMap = await this.client['getXiaohongshuNoteCommentsBatch'](
          noteIds,
          maxCommentsPerNote
        );

        for (const noteId of noteIds) {
          // 键存在（即使为空数组）代表请求成功；键缺失才视为失败
          if (commentsMap && commentsMap.has(noteId)) {
            commentsByNote.set(noteId, (commentsMap.get(noteId) || []).slice(0, maxCommentsPerNote));
          } else {
            failedNoteCount++;
            failedNoteIds.push(noteId);
            failureReasons.push(`笔记 ${noteId} 未返回评论数据`);
          }
        }
      } catch (error) {
        const reason = this.describeError(error);
        console.error('[Xiaohongshu Service] 评论批量获取失败:', reason);
        failedNoteCount += noteIds.length;
        failedNoteIds.push(...noteIds);
        failureReasons.push(reason);
      }
    }

    // 处理评论数据（只使用已成功获取的数据，不重复请求）
    for (const note of noteList) {
      if (!note.id) continue;

      const comments = commentsByNote.get(note.id) || [];
      const limitedComments = comments.slice(0, maxCommentsPerNote);

      for (const comment of limitedComments) {
        const mappedComment = this.convertCommentToData(comment, note.title);
        allComments.push(mappedComment);

        if (comment.content && comment.content.length > 5) {
          commentTexts.push(comment.content);
        }
      }
    }

    // 合并所有文本并去重（笔记标题/描述始终保留）
    const allTexts = [...new Set([...rawTexts, ...commentTexts])];

    // 判定评论完整度：单篇评论为空（接口成功但无评论）不计入失败
    let commentStatus: CommentFetchStatus;
    if (failedNoteCount === 0) {
      commentStatus = 'complete';
    } else if (commentsByNote.size === 0) {
      commentStatus = 'missing';
    } else {
      commentStatus = 'partial';
    }

    const uniqueFailureReasons = [...new Set(failureReasons)].slice(0, 5);

    console.log(
      `[Xiaohongshu Service] 深度搜索完成: ${noteList.length} 个笔记, ${allComments.length} 条评论, ` +
      `${allTexts.length} 条文本, 评论状态: ${commentStatus}, 失败笔记: ${failedNoteCount}` +
      (partialNoteCount > 0 ? `（其中 ${partialNoteCount} 篇为分页中途失败，已保留已取到的评论）` : '')
    );

    if (commentStatus !== 'complete') {
      console.warn(
        `[Xiaohongshu Service] 评论数据不完整（${commentStatus}），${failedNoteCount} 个笔记评论获取失败: ` +
        uniqueFailureReasons.join('; ')
      );
    }

    return {
      rawTexts: allTexts,
      videos: noteList,
      allComments,
      videoCount: noteList.length,
      commentCount: allComments.length,
      commentStatus,
      failedNoteCount,
      failedNoteIds: failedNoteIds.filter(id => !!id),
      failureReasons: uniqueFailureReasons
    };
  }

  /**
   * 获取单篇笔记的评论（最多 maxCommentsPerNote 条）
   *
   * 第一页就失败时抛出异常；后续分页失败时保留已取到的评论，
   * 并通过 partialFailureReason 告知调用方该笔记评论不完整。
   */
  private async fetchCommentsForNote(
    noteId: string,
    maxCommentsPerNote: number
  ): Promise<{ comments: any[]; partialFailureReason?: string }> {
    const comments: any[] = [];
    let lastCursor = '';
    let hasMore = true;

    while (hasMore && comments.length < maxCommentsPerNote) {
      let response: any;
      try {
        response = await this.client['getXiaohongshuNoteComments'](noteId, lastCursor);
      } catch (error) {
        const reason = this.describeError(error);
        if (comments.length === 0) {
          throw new Error(reason);
        }
        return { comments, partialFailureReason: reason };
      }

      if (!response || response.code !== 200) {
        const reason =
          `笔记 ${noteId} 评论接口返回异常: code=${response?.code}, message=${response?.message || 'unknown'}`;
        if (comments.length === 0) {
          throw new Error(reason);
        }
        return { comments, partialFailureReason: reason };
      }

      if (!response.data || !Array.isArray(response.data.comments)) {
        const reason = `笔记 ${noteId} 评论接口响应缺少 comments 字段`;
        if (comments.length === 0) {
          throw new Error(reason);
        }
        return { comments, partialFailureReason: reason };
      }

      comments.push(...response.data.comments);

      hasMore = response.data.has_more === true;
      const nextCursor = response.data.cursor || '';

      // 游标没有推进时不继续翻页，避免重复请求同一页
      if (hasMore && !nextCursor) {
        break;
      }
      lastCursor = nextCursor;

      if (hasMore && comments.length < maxCommentsPerNote) {
        await new Promise(resolve => setTimeout(resolve, 300));
      }
    }

    return { comments };
  }

  /**
   * 将异常转换为可读的失败原因摘要
   */
  private describeError(error: unknown): string {
    if (error instanceof Error) {
      return error.message || error.name;
    }
    if (typeof error === 'string') {
      return error;
    }
    try {
      return JSON.stringify(error);
    } catch {
      return String(error);
    }
  }

  /**
   * 检查服务可用性
   */
  async checkAvailability(): Promise<boolean> {
    // 对于 Xiaohongshu，我们总是返回 true，让实际请求时再处理错误
    return true;
  }

  /**
   * 获取使用统计
   */
  getUsageStats() {
    return this.client['getUsageStats']();
  }

  /**
   * 清除缓存
   */
  clearCache(): void {
    this.client['clearCache']();
  }

  /**
   * 获取缓存统计
   */
  getCacheStats() {
    return this.client['getCacheStats']();
  }

  /**
   * 将 Xiaohongshu 搜索结果转换为笔记数据格式
   */
  private convertSearchResultToNote(item: any, sourceKeyword: string): any {
    if (!item.note || !item.note.id) {
      console.warn('[Xiaohongshu Service] convertSearchResultToNote: item 缺少 id 字段');
      return null;
    }

    const note = item.note;

    // 提取缩略图
    const imagesList = note.images_list || [];
    const firstImage = imagesList[0] || {};
    const thumbUrl = firstImage.url_size_large || firstImage.url || '';

    // 提取描述（截取前500字符）
    const desc = note.desc || '';
    const description = desc.length > 500 ? desc.substring(0, 500) : desc;

    // 构建小红书笔记链接
    const noteUrl = `https://www.xiaohongshu.com/explore/${note.id}`;

    // 转换时间戳（小红书使用秒级时间戳）
    const createTime = note.timestamp
      ? new Date(note.timestamp * 1000).toISOString()
      : new Date().toISOString();

    return {
      title: note.title || '',
      description: description,
      author: note.user?.nickname || '',
      video_url: noteUrl,
      publish_time: createTime,
      likes: note.liked_count?.toString() || '0',
      collected_at: new Date().toISOString(),
      comment_count: note.comments_count || 0,
      // 扩展字段
      id: note.id,
      type: note.type || 'normal',
      userid: note.user?.userid || '',
      nickname: note.user?.nickname || '',
      user_images: note.user?.images || '',
      // 媒体信息
      images_list: imagesList,
      thumb_url: thumbUrl,
      cover_image_index: note.cover_image_index || 0,
      // 互动数据
      liked_count: note.liked_count || 0,
      collected_count: note.collected_count || 0,
      share_count: note.shared_count || 0,
      // 来源
      source_keyword: sourceKeyword
    };
  }

  /**
   * 将 Xiaohongshu 评论转换为数据格式
   */
  private convertCommentToData(comment: any, noteTitle: string): any {
    // 转换时间戳
    const createTime = comment.time
      ? new Date(comment.time * 1000).toISOString()
      : new Date().toISOString();

    const content = comment.content || '';

    return {
      video_title: noteTitle,
      comment_text: content,
      username: comment.user?.nickname || '',
      likes: comment.like_count?.toString() || '0',
      // 扩展字段
      comment_id: comment.id,
      userid: comment.user?.userid || '',
      nickname: comment.user?.nickname || '',
      user_images: comment.user?.images || '',
      like_count: comment.like_count || 0,
      sub_comment_count: comment.sub_comment_count || 0,
      create_time: createTime,
      note_id: comment.note_id
    };
  }
}

/**
 * Xiaohongshu 服务适配器（用于工厂模式）
 */
export class XiaohongshuServiceAdapter implements IDataSourceService {
  private service: XiaohongshuService;

  constructor(options?: XiaohongshuServiceOptions) {
    this.service = new XiaohongshuService(undefined, options);
  }

  async searchAndFetch(keyword: string, limit: number): Promise<DataSourceResult> {
    return await this.service.searchAndFetch(keyword, limit);
  }

  async searchWithComments(keyword: string, options?: DeepCrawlOptions): Promise<DeepCrawlResult> {
    return await this.service.searchWithComments(keyword, options || {});
  }

  async checkAvailability(): Promise<boolean> {
    return await this.service.checkAvailability();
  }

  /**
   * 获取使用统计（Xiaohongshu 特有方法）
   */
  getUsageStats() {
    return this.service.getUsageStats();
  }

  /**
   * 清除缓存（Xiaohongshu 特有方法）
   */
  clearCache(): void {
    this.service.clearCache();
  }

  /**
   * 获取缓存统计（Xiaohongshu 特有方法）
   */
  getCacheStats() {
    return this.service.getCacheStats();
  }
}
