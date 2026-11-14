"""Testes do fluxo de lotes do instagrambot_segue_following.

Os testes substituem o Selenium por uma lista falsa de botoes "Seguir" e usam um
registrador de espera, para cobrir falhas no meio do lote, a pausa entre lotes e a
retomada de uma execucao interrompida sem abrir o Instagram.
"""

import importlib.util
import json
from pathlib import Path

import pytest


CAMINHO_MODULO = Path(__file__).resolve().parents[1] / "instagrambot_segue_following.py"

especificacao = importlib.util.spec_from_file_location("instagrambot_segue_following", CAMINHO_MODULO)
modulo = importlib.util.module_from_spec(especificacao)
especificacao.loader.exec_module(modulo)


class BotaoFalso:
    def __init__(self, perfil):
        self.perfil = perfil
        self.id = None


class ListaFalsa:
    """Lista de perfis que devolve sempre os mesmos botoes, como o modal do Instagram."""

    def __init__(self, perfis, falhas=None, interromper_no_clique=None):
        self.perfis = list(perfis)
        self.botoes = [BotaoFalso(perfil) for perfil in self.perfis]
        self.falhas = dict(falhas or {})
        self.interromper_no_clique = interromper_no_clique
        self.tentativas = []
        self.seguidos = []

    def obter_botoes(self):
        return list(self.botoes)

    def clicar(self, botao):
        self.tentativas.append(botao.perfil)

        if self.interromper_no_clique is not None and len(self.tentativas) == self.interromper_no_clique:
            raise KeyboardInterrupt("interrupcao simulada")

        if botao.perfil in self.falhas:
            raise RuntimeError(self.falhas[botao.perfil])

        self.seguidos.append(botao.perfil)


def executar_fluxo(
    lista,
    caminho_progresso,
    limite_seguir=2,
    intervalo_lote_segundos=30,
    total_perfis_lista=None,
    saida=None,
):
    saida = saida if saida is not None else []
    esperas = []
    total = total_perfis_lista if total_perfis_lista is not None else len(lista.perfis)
    progresso = modulo.ProgressoSeguir.abrir(
        str(caminho_progresso),
        "conta_alvo",
        "seguindo",
        total,
        imprimir=saida.append,
    )
    resultado = modulo.executar_lotes_seguir(
        obter_botoes=lista.obter_botoes,
        clicar=lista.clicar,
        progresso=progresso,
        total_perfis_lista=total,
        limite_seguir=limite_seguir,
        intervalo_lote_segundos=intervalo_lote_segundos,
        esperar=esperas.append,
        imprimir=saida.append,
    )
    return resultado, progresso, esperas, saida


def ler_progresso(caminho):
    with open(caminho, "r", encoding="utf-8") as arquivo:
        return json.load(arquivo)


def test_falha_no_meio_do_lote_registra_motivo_e_continua(tmp_path):
    caminho = tmp_path / "progresso.json"
    lista = ListaFalsa(
        ["ana", "bruno", "carla", "diego", "elisa"],
        falhas={"carla": "botao coberto por popup"},
    )

    resultado, progresso, esperas, saida = executar_fluxo(
        lista,
        caminho,
        limite_seguir=5,
        intervalo_lote_segundos=3600,
    )

    assert lista.tentativas == ["ana", "bruno", "carla", "diego", "elisa"]
    assert lista.seguidos == ["ana", "bruno", "diego", "elisa"]
    assert resultado["sucessos"] == 4
    assert resultado["falhas"] == 1
    assert progresso.seguidos == {"ana": 1, "bruno": 2, "diego": 4, "elisa": 5}
    assert progresso.falhas["carla"]["indice"] == 3
    assert "botao coberto por popup" in progresso.falhas["carla"]["motivo"]

    texto = "\n".join(saida)
    assert "Resumo desta execucao: 4 sucesso(s) e 1 falha(s)." in texto
    assert "Falhas desta execucao:" in texto
    assert "Perfil 3/5 (carla): RuntimeError: botao coberto por popup" in texto
    assert "Seguindo perfil 3/5 (lote 4/5)" in texto

    dados = ler_progresso(caminho)
    assert {"indice": 3, "perfil": "carla", "motivo": "RuntimeError: botao coberto por popup"} in dados["falhas"]
    assert 3600 not in esperas


def test_intervalo_entre_lotes_e_respeitado(tmp_path):
    caminho = tmp_path / "progresso.json"
    lista = ListaFalsa(["ana", "bruno", "carla", "diego", "elisa"])

    resultado, _, esperas, saida = executar_fluxo(
        lista,
        caminho,
        limite_seguir=2,
        intervalo_lote_segundos=3600,
    )

    assert resultado["sucessos"] == 5
    assert resultado["falhas"] == 0
    assert esperas.count(3600) == 2
    assert lista.seguidos == ["ana", "bruno", "carla", "diego", "elisa"]

    texto = "\n".join(saida)
    assert texto.count("Limite do lote atingido. Aguardando 3600 segundos") == 2
    assert "Finalizado. Total seguido nesta execucao: 5" in texto


def test_execucao_interrompida_retoma_do_ponto_salvo(tmp_path):
    caminho = tmp_path / "progresso.json"
    primeira_lista = ListaFalsa(
        ["ana", "bruno", "carla", "diego", "elisa"],
        interromper_no_clique=3,
    )

    with pytest.raises(KeyboardInterrupt):
        executar_fluxo(primeira_lista, caminho, limite_seguir=5)

    dados = ler_progresso(caminho)
    assert [item["perfil"] for item in dados["seguidos"]] == ["ana", "bruno"]
    assert dados["falhas"] == []
    assert dados["posicao_processada"] == 2

    segunda_lista = ListaFalsa(["ana", "bruno", "carla", "diego", "elisa"])
    resultado, progresso, _, saida = executar_fluxo(segunda_lista, caminho, limite_seguir=5)

    assert segunda_lista.tentativas == ["carla", "diego", "elisa"]
    assert resultado["sucessos"] == 3
    assert resultado["falhas"] == 0
    assert progresso.seguidos == {"ana": 1, "bruno": 2, "carla": 3, "diego": 4, "elisa": 5}

    texto = "\n".join(saida)
    assert "Progresso carregado de" in texto
    assert "Perfil 1/5 (ana) ja foi seguido em execucao anterior. Pulando." in texto
    assert "Perfil 2/5 (bruno) ja foi seguido em execucao anterior. Pulando." in texto


def test_falha_da_execucao_anterior_e_tentada_novamente(tmp_path):
    caminho = tmp_path / "progresso.json"
    primeira_lista = ListaFalsa(
        ["ana", "bruno", "carla"],
        falhas={"bruno": "timeout"},
    )
    executar_fluxo(primeira_lista, caminho, limite_seguir=3)

    segunda_lista = ListaFalsa(["ana", "bruno", "carla"])
    resultado, progresso, _, saida = executar_fluxo(segunda_lista, caminho, limite_seguir=3)

    assert segunda_lista.tentativas == ["bruno"]
    assert resultado["sucessos"] == 1
    assert progresso.falhas == {}
    assert "Falhas da execucao anterior que serao tentadas novamente" in "\n".join(saida)


def test_resumo_inclui_falhas_pendentes_do_progresso(tmp_path):
    caminho = tmp_path / "progresso.json"
    lista = ListaFalsa(["ana", "bruno"], falhas={"bruno": "timeout"})

    resultado, progresso, _, saida = executar_fluxo(lista, caminho, limite_seguir=2)

    texto = "\n".join(saida)
    assert resultado["falhas"] == 1
    assert "Falhas desta execucao:" in texto
    assert "Falhas pendentes no arquivo de progresso (1)." in texto
    assert "na proxima execucao" in texto


def test_progresso_ausente_inicia_do_zero_com_aviso(tmp_path):
    caminho = tmp_path / "nao_existe.json"
    lista = ListaFalsa(["ana", "bruno"])
    saida = []

    resultado, progresso, _, _ = executar_fluxo(
        lista,
        caminho,
        limite_seguir=2,
        saida=saida,
    )

    assert resultado["sucessos"] == 2
    assert progresso.seguidos == {"ana": 1, "bruno": 2}
    assert any("nao encontrado" in mensagem for mensagem in saida)


def test_progresso_corrompido_inicia_do_zero_com_aviso(tmp_path):
    caminho = tmp_path / "progresso.json"
    caminho.write_text("{ isso nao e json", encoding="utf-8")
    lista = ListaFalsa(["ana", "bruno"])
    saida = []

    resultado, progresso, _, _ = executar_fluxo(
        lista,
        caminho,
        limite_seguir=2,
        saida=saida,
    )

    assert resultado["sucessos"] == 2
    assert progresso.seguidos == {"ana": 1, "bruno": 2}
    assert any("invalido ou corrompido" in mensagem for mensagem in saida)


def test_progresso_de_outro_perfil_inicia_do_zero_com_aviso(tmp_path):
    caminho = tmp_path / "progresso.json"
    caminho.write_text(
        json.dumps(
            {
                "versao": modulo.VERSAO_PROGRESSO,
                "perfil_alvo": "outra_conta",
                "tipo_lista": "seguindo",
                "posicao_processada": 1,
                "indices": {"ana": 1},
                "seguidos": [{"indice": 1, "perfil": "ana"}],
                "falhas": [],
            }
        ),
        encoding="utf-8",
    )
    lista = ListaFalsa(["ana", "bruno"])
    saida = []

    resultado, progresso, _, _ = executar_fluxo(
        lista,
        caminho,
        limite_seguir=2,
        saida=saida,
    )

    assert lista.tentativas == ["ana", "bruno"]
    assert resultado["sucessos"] == 2
    assert any("invalido ou corrompido" in mensagem for mensagem in saida)


def test_progresso_com_lista_json_inicia_do_zero_com_aviso(tmp_path):
    caminho = tmp_path / "progresso.json"
    caminho.write_text("[]", encoding="utf-8")
    lista = ListaFalsa(["ana"])
    saida = []

    resultado, progresso, _, _ = executar_fluxo(lista, caminho, limite_seguir=1, saida=saida)

    assert resultado["sucessos"] == 1
    assert progresso.seguidos == {"ana": 1}
    assert any("formato inesperado" in mensagem for mensagem in saida)


def test_progresso_com_versao_desconhecida_inicia_do_zero_com_aviso(tmp_path):
    caminho = tmp_path / "progresso.json"
    caminho.write_text(
        json.dumps(
            {
                "versao": 99,
                "perfil_alvo": "conta_alvo",
                "tipo_lista": "seguindo",
                "seguidos": [{"indice": 1, "perfil": "ana"}],
            }
        ),
        encoding="utf-8",
    )
    lista = ListaFalsa(["ana"])
    saida = []

    resultado, progresso, _, _ = executar_fluxo(lista, caminho, limite_seguir=1, saida=saida)

    assert lista.tentativas == ["ana"]
    assert resultado["sucessos"] == 1
    assert progresso.seguidos == {"ana": 1}
    assert any("versao de progresso desconhecida" in mensagem for mensagem in saida)


def test_resumo_de_erro_limita_tamanho_e_inclui_tipo():
    resumo = modulo.resumir_erro(RuntimeError("linha 1\nlinha 2"))

    assert resumo == "RuntimeError: linha 1 linha 2"
    assert len(modulo.resumir_erro(RuntimeError("x" * 500))) <= 300


def test_parser_mantem_argumentos_antigos_e_aceita_arquivo_progresso():
    parser = modulo.criar_parser_argumentos()
    argumentos = parser.parse_args(["--terminal", "--perfil-alvo", "conta", "--arquivo-progresso", "x.json"])

    assert argumentos.terminal is True
    assert argumentos.perfil_alvo == "conta"
    assert argumentos.tipo_lista == "seguindo"
    assert argumentos.limite_seguir == 50
    assert argumentos.intervalo_lote_segundos == 3600
    assert argumentos.arquivo_progresso == "x.json"

    padrao = parser.parse_args([])
    assert padrao.arquivo_progresso == modulo.ARQUIVO_PROGRESSO_PADRAO


def test_extrair_usuario_de_href():
    assert modulo.extrair_usuario_de_href("https://www.instagram.com/ana.silva/") == "ana.silva"
    assert modulo.extrair_usuario_de_href("/bruno_92?igsh=1") == "bruno_92"
    assert modulo.extrair_usuario_de_href("https://www.instagram.com/explore/tags/foo/") is None
    assert modulo.extrair_usuario_de_href(None) is None
