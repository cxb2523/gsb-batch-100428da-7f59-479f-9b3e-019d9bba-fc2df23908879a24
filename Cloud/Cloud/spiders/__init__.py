# -*- coding: utf-8 -*-
"""Spider de citações com paginação auditável.

A paginação continua sendo feita pelo link "next", mas cada requisição de
página carrega o número da página em ``meta['page']``: a primeira página é a
1 e cada link "next" gera a página seguinte (n + 1).

O spider mantém ``self.page_outcomes`` (um desfecho por número de página,
mesmo que a página seja retentada ou requisitada de novo) e
``self.termination_reason``, que explica por que a paginação terminou. O
``PageOutcomesPipeline`` habilitado em ``settings.py`` resume esses dados no
fim da execução.
"""
import re
from urllib.parse import urlparse

import scrapy

PAGE_NUMBER_RE = re.compile(r"(?:/page/|[/?&]page=)(\d+)")

# Códigos de erro liberados por requisição de página para que o callback
# registre a falha em vez de o HttpErrorMiddleware descartar a resposta.
HTTP_STATUS_CODES_TO_HANDLE = list(range(400, 600))

STATUS_OK = "ok"
STATUS_FAILED = "failed"

TERMINATION_LAST_PAGE = "last_page"
TERMINATION_PAGE_FAILED = "page_failed"
TERMINATION_LOOP = "loop_detected"
TERMINATION_INCOMPLETE = "incomplete"
TERMINATION_NO_PAGES = "no_pages"


class QuotesSpider(scrapy.Spider):
    name = 'quotes'
    allowed_domains = ['toscrape.com']
    start_urls = ['http://quotes.toscrape.com']

    def __init__(self, *args, **kwargs):
        # Permite apontar o spider para páginas de teste via
        # ``scrapy crawl quotes -a start_url=...`` (uma URL ou lista separada
        # por vírgulas) sem alterar o comportamento padrão do projeto.
        start_url = kwargs.pop('start_url', None)
        start_urls = kwargs.pop('start_urls', None)
        allowed_domains = kwargs.pop('allowed_domains', None)
        super().__init__(*args, **kwargs)

        custom_start = bool(start_url or start_urls)
        if start_url:
            self.start_urls = self._split_values(start_url)
        elif start_urls:
            self.start_urls = self._split_values(start_urls)

        if allowed_domains:
            self.allowed_domains = self._split_values(allowed_domains)
        elif custom_start:
            # Em páginas de teste (locais ou não), libera os hosts de origem
            # para que o OffsiteMiddleware não descarte a primeira página.
            hosts = {
                urlparse(url).hostname
                for url in self.start_urls
                if urlparse(url).hostname
            }
            self.allowed_domains = sorted(hosts)

        # Desfecho por número de página: repetições (retry/duplicata) não
        # criam uma segunda entrada.
        self.page_outcomes = {}
        self.termination_reason = None
        self.termination_detail = None

        self._stopped = False
        self._visited_urls = set()

    @staticmethod
    def _split_values(value):
        """Normaliza argumentos de linha de comando em uma lista."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(',') if item.strip()]
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    async def start(self):
        """Ponto de entrada do Scrapy >= 2.13."""
        for request in self.start_requests():
            yield request

    def start_requests(self):
        """Ponto de entrada do Scrapy < 2.13 (mantido por compatibilidade)."""
        for url in self.start_urls:
            yield self._page_request(url, page=1, dont_filter=True)

    def _page_request(self, url, page, dont_filter=False):
        """Cria uma requisição de página sempre carregando o número da página."""
        page = int(page)
        if page < 1:
            raise ValueError('O número da página deve começar em 1')
        self._visited_urls.add(url)
        return scrapy.Request(
            url=url,
            callback=self.parse,
            # Não-200 precisa chegar ao callback para virar "página falhou"
            # em vez de ser descartado pelo HttpErrorMiddleware. Só os
            # 4xx/5xx são liberados, então redirects 3xx continuam seguindo.
            errback=self.handle_page_failure,
            meta={
                'page': page,
                'page_url': url,
                'handle_httpstatus_list': HTTP_STATUS_CODES_TO_HANDLE,
            },
            dont_filter=dont_filter,
        )

    def parse(self, response):
        page = self._page_number(response)
        if self._stopped:
            self.logger.info(
                'Página %s ignorada: a paginação já foi encerrada', page
            )
            return
        if page in self.page_outcomes:
            # Requisição duplicada/repetida da mesma página: não conta de novo.
            self.logger.info(
                'Página %s ignorada: desfecho já registrado', page
            )
            return

        retries = response.meta.get('retry_times', 0)
        if response.status != 200:
            self._register_failure(
                page,
                url=response.url,
                http_status=response.status,
                error='HTTP %s' % response.status,
                retries=retries,
            )
            return

        #Extraindo as citações
        try:
            caixas = []
            for quote in response.css('div.quote'):
                caixas.append({
                    'autor': quote.css('small.author::text').extract_first(),
                    'texto': quote.css('span.text::text').extract_first(),
                    'categorias': quote.css('a.tag::text').extract(),
                })
        except Exception as exc:
            # Uma exceção no callback não pode sumir sem desfecho de página.
            self._register_failure(
                page,
                url=response.url,
                error='%s: %s' % (exc.__class__.__name__, exc),
                retries=retries,
            )
            return

        self._record_page_outcome(
            page,
            status=STATUS_OK,
            url=response.url,
            quotes=len(caixas),
            http_status=response.status,
            retries=retries,
        )

        for caixa in caixas:
            yield caixa

        #Navegação entre páginas
        try:
            next_href = response.css('li.next > a::attr(href)').extract_first()
        except Exception as exc:
            self._stop_pagination(
                TERMINATION_PAGE_FAILED,
                'falha ao ler o link "next" da página %d (%s); paginação '
                'interrompida' % (page, exc),
            )
            return

        if not next_href:
            self._stop_pagination(
                TERMINATION_LAST_PAGE,
                'a página %d não possui link "next"; fim da paginação' % page,
            )
            return

        next_url = response.urljoin(next_href)
        if next_url in self._visited_urls:
            self._stop_pagination(
                TERMINATION_LOOP,
                'o link "next" da página %d aponta para uma URL já visitada '
                '(%s); paginação encerrada' % (page, next_url),
            )
            return

        yield self._page_request(next_url, page + 1)

    def handle_page_failure(self, failure):
        """Registra a falha da página e encerra a paginação.

        As citações já emitidas continuam no feed/pipeline: nada é descartado
        aqui. Nenhuma página posterior é requisitada, evitando continuar às
        cegas depois de uma página ruim.
        """
        request = getattr(failure, 'request', None)
        page = self._failure_page_number(request, failure)
        if self._stopped or page in self.page_outcomes:
            self.logger.info(
                'Falha da página %s ignorada: desfecho já registrado ou '
                'paginação encerrada', page
            )
            return

        http_status, error = self._describe_failure(failure)
        retries = 0
        url = None
        if request is not None:
            retries = request.meta.get('retry_times', 0)
            # URL lógica da página; em caso de redirect o meta é preservado.
            url = request.meta.get('page_url', request.url)

        self._register_failure(
            page,
            url=url,
            http_status=http_status,
            error=error,
            retries=retries,
        )

    def _register_failure(self, page, url=None, http_status=None, error=None,
                          retries=0):
        self._record_page_outcome(
            page,
            status=STATUS_FAILED,
            url=url,
            quotes=0,
            error=error,
            http_status=http_status,
            retries=retries,
        )
        self._stop_pagination(
            TERMINATION_PAGE_FAILED,
            'falha na página %d (%s); paginação interrompida' % (page, error),
        )
        self.logger.error(
            'Página %d falhou (%s); paginação interrompida, mantendo as '
            'citações já coletadas', page, error,
        )

    def _failure_page_number(self, request, failure):
        if request is not None and request.meta.get('page') is not None:
            return int(request.meta['page'])
        response = getattr(failure.value, 'response', None)
        if response is not None and response.meta.get('page') is not None:
            return int(response.meta['page'])
        if self.page_outcomes:
            return max(self.page_outcomes) + 1
        return 1

    @staticmethod
    def _describe_failure(failure):
        """Devolve (http_status, mensagem) para um Failure do Scrapy."""
        exception = failure.value
        response = getattr(exception, 'response', None)
        status = getattr(response, 'status', None)
        if status is not None:
            return status, 'HTTP %s' % status
        message = str(exception) or 'sem detalhes'
        return None, '%s: %s' % (exception.__class__.__name__, message)

    def _page_number(self, response):
        meta_page = response.meta.get('page')
        if meta_page is not None:
            return int(meta_page)
        match = PAGE_NUMBER_RE.search(response.url)
        if match:
            return int(match.group(1))
        if self.page_outcomes:
            return max(self.page_outcomes) + 1
        return 1

    def _record_page_outcome(self, page, status, url=None, quotes=0,
                             error=None, http_status=None, retries=0):
        """Registra o desfecho de uma página uma única vez."""
        page = int(page)
        if page in self.page_outcomes:
            return False
        self.page_outcomes[page] = {
            'page': page,
            'status': status,
            'url': url,
            'quotes': int(quotes),
            'http_status': http_status,
            'error': error,
            'retries': int(retries),
        }
        return True

    def _stop_pagination(self, reason, detail):
        self._stopped = True
        self.termination_reason = reason
        self.termination_detail = detail
        self.logger.info('Paginação encerrada (%s): %s', reason, detail)

    def page_report(self):
        """Monta o resumo verificável da execução (usado pelo pipeline)."""
        if self.termination_reason is not None:
            reason = self.termination_reason
            detail = self.termination_detail
        elif self.page_outcomes:
            # Rede de segurança: a cadeia parou sem um motivo registrado
            # (por exemplo, spider fechado externamente ou requisição
            # descartada), mas não escondemos isso.
            reason = TERMINATION_INCOMPLETE
            detail = ('a paginação terminou sem um motivo explícito; '
                      'última página registrada: %d' % max(self.page_outcomes))
        else:
            reason = TERMINATION_NO_PAGES
            detail = 'nenhuma página foi processada'

        pages = [dict(self.page_outcomes[key])
                 for key in sorted(self.page_outcomes)]
        return {
            'spider': self.name,
            'termination': {'reason': reason, 'detail': detail},
            'pages': pages,
            'page_count': len(pages),
            'pages_ok': sum(1 for p in pages if p['status'] == STATUS_OK),
            'pages_failed': sum(
                1 for p in pages if p['status'] == STATUS_FAILED
            ),
            'total_quotes': sum(p['quotes'] for p in pages),
        }

    def closed(self, reason):
        # Garante que uma inspeção após o fechamento enxergue o mesmo motivo
        # que o pipeline registrou no fim da execução.
        if self.termination_reason is None:
            report = self.page_report()
            self.termination_reason = report['termination']['reason']
            self.termination_detail = report['termination']['detail']
