"use client";

import { useTranslations } from 'next-intl';
import { AlertTriangle, Info } from 'lucide-react';

export interface CrawlStats {
  videoCount?: number;
  commentCount?: number;
  textCount?: number;
  commentStatus?: 'complete' | 'partial' | 'missing';
  failedNoteCount?: number;
  failureReasons?: string[];
}

interface CommentDataNoticeProps {
  crawlStats?: CrawlStats;
}

/**
 * 评论数据不完整提示
 * 当深度抓取的评论部分或全部缺失时，提示用户本次分析基于不完整的评论数据。
 * 笔记标题/描述文本仍然可用，因此分析结果依旧可以产出。
 */
export default function CommentDataNotice({ crawlStats }: CommentDataNoticeProps) {
  const t = useTranslations('commentDataNotice');

  const status = crawlStats?.commentStatus;
  if (!status || status === 'complete') {
    return null;
  }

  const failedNoteCount = crawlStats?.failedNoteCount || 0;
  const reasons = (crawlStats?.failureReasons || []).filter(Boolean);

  const isMissingAll = status === 'missing';
  const Icon = isMissingAll ? AlertTriangle : Info;
  const title = isMissingAll ? t('missingTitle') : t('partialTitle');
  const message = isMissingAll
    ? t('missingMessage')
    : t('partialMessage', { count: failedNoteCount });

  const containerClass = isMissingAll
    ? 'bg-red-50 border-red-200 text-red-800'
    : 'bg-amber-50 border-amber-200 text-amber-800';
  const iconClass = isMissingAll ? 'bg-red-100 text-red-600' : 'bg-amber-100 text-amber-600';

  return (
    <div className={`${containerClass} border rounded-2xl p-5 transition-all duration-300`}>
      <div className="flex items-start gap-4">
        <div className={`flex-shrink-0 w-10 h-10 ${iconClass} rounded-full flex items-center justify-center`}>
          <Icon className="w-5 h-5" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-sm font-semibold">{title}</span>
            {failedNoteCount > 0 && (
              <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-white/60">
                {t('failedNotes', { count: failedNoteCount })}
              </span>
            )}
          </div>
          <p className="text-xs opacity-80">{message}</p>
          {reasons.length > 0 && (
            <ul className="mt-2 space-y-1">
              {reasons.map((reason, index) => (
                <li key={index} className="text-xs opacity-70 break-words">
                  · {reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
