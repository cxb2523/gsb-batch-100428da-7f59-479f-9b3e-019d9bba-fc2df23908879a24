import io

import pytest
from selenium.webdriver.common.by import By

from src import crawler_cei
from src.crawler_cei import CrawlerCei, STATUS_SUCESSO, STATUS_SEM_TRADES, STATUS_FALHA
import pandas as pd


@pytest.fixture(autouse=True)
def _compat_pandas3(monkeypatch):
    """Adequacoes locais para rodar os testes sob pandas 3.x; o projeto roda
    em pandas 2.2.x (ver requirements.txt), onde nenhum destes desvios existe.
    Nenhum modulo de producao e alterado.
    """
    # 1) Em pandas 3.x, df.apply sobre DataFrame vazio retorna um DataFrame em
    # vez de Series, quebrando a atribuicao existente em
    # ComunsB3.converte_dataframe_para_formato_padrao.
    apply_original = pd.DataFrame.apply

    def apply_compat(self, func, *args, **kwargs):
        if self.empty and kwargs.get('axis') == 1:
            return pd.Series([], dtype=object, index=self.index)
        return apply_original(self, func, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, 'apply', apply_compat)

    # 2) pandas 3.x nao aceita mais markup HTML cru como string (tenta abrir
    # como arquivo); pandas 2.x aceitava. Mantem o comportamento antigo.
    read_html_original = pd.read_html

    def read_html_compat(io_arg, *args, **kwargs):
        if isinstance(io_arg, str) and '<' in io_arg:
            io_arg = io.StringIO(io_arg)
        return read_html_original(io_arg, *args, **kwargs)

    monkeypatch.setattr(pd, 'read_html', read_html_compat)


ID_SELECAO_CORRETORAS = 'ctl00_ContentPlaceHolder1_ddlAgentes'
ID_TABELA = ('ctl00_ContentPlaceHolder1_rptAgenteBolsa_ctl00_rptContaBolsa_ctl00_'
             'pnAtivosNegociados')
ID_MENSAGEM = 'CEIMessageDIV'

MENSAGEM_SEM_RESULTADOS = 'Não foram encontrados resultados para esta pesquisa.\n×'


class FakeWebDriverWait(object):
    """Substituto imediato do WebDriverWait do Selenium: nao faz polling,
    apenas avalia a expected condition (ou clica no elemento clicavel)."""

    def __init__(self, driver, timeout):
        self.driver = driver
        self.timeout = timeout

    def until(self, condition):
        return condition(self.driver)


class FakeOption(object):

    def __init__(self, text, index, select):
        self.text = text
        self._index = str(index)
        self._select = select
        self.selected = False

    def get_attribute(self, name):
        if name == 'index':
            return self._index
        return None

    def get_dom_attribute(self, name):
        return None

    def is_selected(self):
        return self.selected

    def is_enabled(self):
        return True

    def click(self):
        self._select.driver.registra_selecao(self.text)


class FakeSelectElement(object):

    def __init__(self, driver, nomes_corretoras):
        self.tag_name = 'select'
        self.driver = driver
        self.options = [FakeOption(nome, i, self) for i, nome in enumerate(nomes_corretoras)]

    def find_elements(self, by, value):
        if by == By.TAG_NAME and value == 'option':
            return self.options
        return []

    def get_dom_attribute(self, name):
        return None

    def get_attribute(self, name):
        if name == 'disabled':
            return None
        return None


class FakeElement(object):

    def __init__(self, element_id, text=''):
        self.id = element_id
        self.tag_name = 'div'
        self.text = text

    def clear(self):
        pass

    def send_keys(self, value):
        pass

    def click(self):
        pass

    def get_attribute(self, name):
        if name == 'disabled':
            return None
        return None

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True


class FakeDriver(object):
    """Driver focado no fluxo do CrawlerCei. O comportamento por corretora
    vem de `planos`: {nome_da_corretora: {'tipo': ..., 'erro': ...}}."""

    def __init__(self, nomes_corretoras, planos):
        self.nomes_corretoras = nomes_corretoras
        self.planos = planos
        self.corretora_atual = None
        self.quit_chamado = False
        self.urls = []

    def registra_selecao(self, nome_corretora):
        self.corretora_atual = nome_corretora

    def get(self, url):
        self.urls.append(url)

    def quit(self):
        self.quit_chamado = True

    def save_screenshot(self, path):
        pass

    @property
    def page_source(self):
        plano = self.planos[self.corretora_atual]
        if 'erro_html' in plano:
            raise plano['erro_html']
        if plano['tipo'] == 'sucesso':
            # StringIO: pandas 3.x nao aceita mais markup cru como string,
            # enquanto pandas 2.x aceita; assim o teste roda nas duas faixas.
            return io.StringIO(_html_com_trades(plano['ticker'], plano['quantidade']))
        return io.StringIO('<html><body></body></html>')

    def find_element_by_id(self, element_id):
        if element_id == ID_SELECAO_CORRETORAS:
            return FakeSelectElement(self, self.nomes_corretoras)
        if element_id == ID_MENSAGEM:
            return self._elemento_mensagem()
        if element_id == ID_TABELA:
            if self._plano_atual().get('erro_tabela'):
                raise self._plano_atual()['erro_tabela']
            return FakeElement(ID_TABELA)
        return FakeElement(element_id)

    def find_element(self, by, value):
        assert by == By.ID
        return self.find_element_by_id(value)

    def _plano_atual(self):
        if self.corretora_atual is None:
            return {'tipo': 'login'}
        return self.planos[self.corretora_atual]

    def _elemento_mensagem(self):
        plano = self._plano_atual()
        if plano.get('erro_botao_consultar'):
            raise plano['erro_botao_consultar']
        if plano['tipo'] == 'sem_trades':
            return FakeElement(ID_MENSAGEM, text=MENSAGEM_SEM_RESULTADOS)
        return FakeElement(ID_MENSAGEM, text='')


def _html_com_trades(ticker, quantidade):
    linhas = ''.join(
        '<tr>'
        '<td>10/03/2024</td><td>Compra</td><td>Mercado a Vista</td><td>-</td>'
        '<td>{ticker}</td><td>ACOES</td><td>100</td><td>10,50</td>'
        '<td>1.050,00</td><td>1</td>'
        '</tr>'.format(ticker=ticker)
        for _ in range(quantidade)
    )
    return (
        '<html><body>'
        '<div id="{id_tabela}">'
        '<table>'
        '<thead><tr>'
        '<th>Data do Negócio</th><th>Compra/Venda</th><th>Mercado</th>'
        '<th>Prazo/Vencimento</th><th>Código Negociação</th>'
        '<th>Especificação do Ativo</th><th>Quantidade</th><th>Preço (R$)</th>'
        '<th>Valor Total(R$)</th><th>Fator de Cotação</th>'
        '</tr></thead>'
        '<tbody>{linhas}</tbody>'
        '</table>'
        '</div>'
        '</body></html>'
    ).format(id_tabela=ID_TABELA, linhas=linhas)


def _novo_crawler(monkeypatch, fake_driver):
    monkeypatch.setattr(crawler_cei, 'ChromeDriver', lambda: fake_driver)
    monkeypatch.setattr(crawler_cei, 'WebDriverWait', FakeWebDriverWait)
    monkeypatch.setattr(crawler_cei.time, 'sleep', lambda segundos: None)
    return CrawlerCei()


class TestCrawlerCeiToleranciaCorretoras():

    def test_uma_corretora_sucesso_e_outra_falha_retorna_trades_da_sucesso(self, monkeypatch):
        planos = {
            'CORRETORA A': {'tipo': 'sucesso', 'ticker': 'AAAA3', 'quantidade': 2},
            'CORRETORA B': {'tipo': 'falha', 'erro_botao_consultar': RuntimeError('timeout no clique em consultar')},
        }
        fake_driver = FakeDriver(['Selecione', 'CORRETORA A', 'CORRETORA B'], planos)
        crawler = _novo_crawler(monkeypatch, fake_driver)

        df = crawler.busca_trades('12345678900', 'senha')

        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert list(df['ticker']) == ['AAAA3', 'AAAA3']
        assert len(df) == 2
        colunas_padrao = {'ticker', 'operacao', 'qtd', 'data', 'preco', 'valor',
                          'qtd_ajustada', 'taxas', 'aquisicao_via'}
        assert set(df.columns) == colunas_padrao

        resultado = crawler.resultado_corretoras
        assert resultado['CORRETORA A']['status'] == STATUS_SUCESSO
        assert resultado['CORRETORA A']['quantidade_trades'] == 2
        assert resultado['CORRETORA A']['motivo'] is None
        assert resultado['CORRETORA B']['status'] == STATUS_FALHA
        assert 'timeout no clique em consultar' in resultado['CORRETORA B']['motivo']
        assert fake_driver.quit_chamado

    def test_todas_corretoras_falham_retorna_dataframe_vazio_com_colunas_originais(self, monkeypatch):
        planos = {
            'CORRETORA A': {'tipo': 'falha', 'erro_tabela': TimeoutError('tabela nao apareceu')},
            'CORRETORA B': {'tipo': 'falha', 'erro_html': ValueError('html sem tabela de negocios')},
        }
        fake_driver = FakeDriver(['Selecione', 'CORRETORA A', 'CORRETORA B'], planos)
        crawler = _novo_crawler(monkeypatch, fake_driver)

        df = crawler.busca_trades('12345678900', 'senha')

        assert isinstance(df, pd.DataFrame)
        assert df.empty
        assert df is not None
        colunas_padrao = {'ticker', 'operacao', 'qtd', 'data', 'preco', 'valor',
                          'qtd_ajustada', 'taxas', 'aquisicao_via'}
        assert set(df.columns) == colunas_padrao

        resultado = crawler.resultado_corretoras
        assert set(resultado.keys()) == {'CORRETORA A', 'CORRETORA B'}
        for nome in ('CORRETORA A', 'CORRETORA B'):
            assert resultado[nome]['status'] == STATUS_FALHA
            assert resultado[nome]['motivo']
            assert resultado[nome]['quantidade_trades'] == 0
        assert 'tabela nao apareceu' in resultado['CORRETORA A']['motivo']
        assert 'html sem tabela de negocios' in resultado['CORRETORA B']['motivo']
        assert fake_driver.quit_chamado

    def test_unica_corretora_sem_trades_marca_sem_trades_e_nao_falha(self, monkeypatch):
        planos = {
            'UNICA CORRETORA': {'tipo': 'sem_trades'},
        }
        fake_driver = FakeDriver(['UNICA CORRETORA'], planos)
        crawler = _novo_crawler(monkeypatch, fake_driver)

        df = crawler.busca_trades('12345678900', 'senha')

        assert isinstance(df, pd.DataFrame)
        assert df is not None
        assert df.empty
        assert 'ticker' in df.columns
        assert 'data' in df.columns

        resultado = crawler.resultado_corretoras
        assert list(resultado.keys()) == ['UNICA CORRETORA']
        assert resultado['UNICA CORRETORA']['status'] == STATUS_SEM_TRADES
        assert resultado['UNICA CORRETORA']['motivo'] is None
        assert resultado['UNICA CORRETORA']['quantidade_trades'] == 0
        assert fake_driver.quit_chamado

    def test_unica_corretora_com_sucesso_mantem_formato_padrao(self, monkeypatch):
        planos = {
            'UNICA CORRETORA': {'tipo': 'sucesso', 'ticker': 'BBBB4', 'quantidade': 1},
        }
        fake_driver = FakeDriver(['UNICA CORRETORA'], planos)
        crawler = _novo_crawler(monkeypatch, fake_driver)

        df = crawler.busca_trades('12345678900', 'senha')

        assert len(df) == 1
        assert df.iloc[0]['ticker'] == 'BBBB4'
        assert crawler.resultado_corretoras['UNICA CORRETORA']['status'] == STATUS_SUCESSO
        assert crawler.resultado_corretoras['UNICA CORRETORA']['quantidade_trades'] == 1
        assert fake_driver.quit_chamado

    def test_falha_no_login_continua_lancando_excecao_e_fechando_driver(self, monkeypatch):
        fake_driver = FakeDriver(['Selecione'], {})

        def wait_falhando(driver, timeout):
            raise RuntimeError('elemento de posicao nunca visivel')

        monkeypatch.setattr(crawler_cei, 'ChromeDriver', lambda: fake_driver)
        monkeypatch.setattr(crawler_cei, 'WebDriverWait', wait_falhando)
        monkeypatch.setattr(crawler_cei.time, 'sleep', lambda segundos: None)
        crawler = CrawlerCei()

        with pytest.raises(Exception, match='Nao foi possivel logar no CEI'):
            crawler.busca_trades('12345678900', 'senha_errada')

        assert crawler.resultado_corretoras == {}
        assert fake_driver.quit_chamado
