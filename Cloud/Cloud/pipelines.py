# -*- coding: utf-8 -*-

# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://doc.scrapy.org/en/latest/topics/item-pipeline.html

import json
import os


class CloudPipeline(object):
    # ``spider=None`` mantém compatibilidade com Scrapy antigo (que passa o
    # spider) e novo (que deixou de exigi-lo no process_item).
    def process_item(self, item, spider=None):
        return item


class PageOutcomesPipeline(object):
    """Resume, no fim da execução, o desfecho de cada página coletada.

    O spider mantém ``page_outcomes`` (um desfecho por número de página) e
    ``termination_reason``. Este pipeline grava um JSON verificável no
    caminho configurado por ``PAGE_OUTCOMES_FILE`` (vazio desabilita o
    arquivo), publica o resumo em ``spider.logger`` e em ``crawler.stats``.

    Ele não mexe nos itens: as citações já coletadas seguem normalmente para
    o feed de saída.
    """

    def __init__(self, outcomes_file=None):
        self.outcomes_file = outcomes_file
        self.crawler = None
        self.items = 0

    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls(
            outcomes_file=crawler.settings.get('PAGE_OUTCOMES_FILE',
                                               'page_outcomes.json'),
        )
        pipeline.crawler = crawler
        return pipeline

    def open_spider(self, spider=None):
        self.items = 0

    def process_item(self, item, spider=None):
        self.items += 1
        return item

    def close_spider(self, spider=None):
        if spider is None and self.crawler is not None:
            spider = self.crawler.spider
        report = self._build_report(spider)
        self._log_report(spider, report)
        self._save_report(spider, report)

    def _build_report(self, spider):
        page_report = getattr(spider, 'page_report', None)
        if callable(page_report):
            report = page_report()
        else:
            report = {
                'spider': getattr(spider, 'name', None),
                'termination': {
                    'reason': 'not_tracked',
                    'detail': 'o spider não expõe page_report()',
                },
                'pages': [],
                'page_count': 0,
                'pages_ok': 0,
                'pages_failed': 0,
                'total_quotes': 0,
            }
        report['items_pipeline'] = self.items
        return report

    @staticmethod
    def _log_report(spider, report):
        termination = report['termination']
        spider.logger.info(
            'Resumo da paginação: %d página(s) registrada(s), %d ok, %d com '
            'falha, %d citação(ões); término: %s (%s)',
            report['page_count'], report['pages_ok'],
            report['pages_failed'], report['total_quotes'],
            termination['reason'], termination['detail'],
        )
        for page in report['pages']:
            if page['status'] == 'ok':
                spider.logger.info(
                    'Página %d: ok, %d citação(ões)%s',
                    page['page'], page['quotes'],
                    ' (retentativas: %d)' % page['retries']
                    if page['retries'] else '',
                )
            else:
                spider.logger.info(
                    'Página %d: falha (%s)%s',
                    page['page'], page['error'],
                    ' (retentativas: %d)' % page['retries']
                    if page['retries'] else '',
                )

    def _save_report(self, spider, report):
        try:
            stats = spider.crawler.stats
        except AttributeError:
            stats = None
        if stats is not None:
            termination = report['termination']
            stats.set_value('page_outcomes/page_count',
                            report['page_count'])
            stats.set_value('page_outcomes/pages_ok', report['pages_ok'])
            stats.set_value('page_outcomes/pages_failed',
                            report['pages_failed'])
            stats.set_value('page_outcomes/total_quotes',
                            report['total_quotes'])
            stats.set_value('page_outcomes/termination_reason',
                            termination['reason'])
            for page in report['pages']:
                stats.set_value(
                    'page_outcomes/page/%d/status' % page['page'],
                    page['status'],
                )
                stats.set_value(
                    'page_outcomes/page/%d/quotes' % page['page'],
                    page['quotes'],
                )

        if not self.outcomes_file:
            return
        directory = os.path.dirname(os.path.abspath(self.outcomes_file))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        with open(self.outcomes_file, 'w', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        spider.logger.info(
            'Desfechos por página gravados em %s', self.outcomes_file
        )
