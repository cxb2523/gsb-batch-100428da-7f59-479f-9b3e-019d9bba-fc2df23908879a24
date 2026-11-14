"""Integração com a extensão TrackTab (gravador de elementos/XPath).

O TrackTab (pasta ``tracktab/`` na raiz do projeto) é uma extensão de navegador
que captura elementos de qualquer página e exporta um JSON com três variações
de seletor por elemento. Este módulo lê esse arquivo e entrega os seletores
prontos para usar com o :class:`core.rpa.browser.Navegador`.

Fluxo típico::

    from core.rpa import tracktab

    elementos = tracktab.carregar("data/input/tracktab.json")
    navegador.clicar_elemento(elementos["Botão Entrar"].relativo)

    # ou apenas os seletores, por nome
    seletores = tracktab.seletores("data/input/tracktab.json")
    navegador.escrever_texto(seletores["Input E-mail"], "eu@exemplo.com")

Nomes repetidos
---------------
Um export pode conter mais de um elemento com o mesmo ``name``. Como o
dicionário devolvido por :func:`carregar` só comporta uma entrada por nome,
**o primeiro elemento é mantido** e os demais são registrados em um
:class:`ResultadoCarregamento` (use :func:`carregar_detalhado` ou
:func:`combinar_detalhado`). Assim o chamador consegue distinguir as três
situações que antes caíam todas em ``""``:

* nome inexistente → :class:`ErroElementoAusente` (em :func:`obter`);
* nome repetido → :class:`ErroNomeDuplicado` (em :func:`obter`) ou a
  propriedade ``duplicados`` do dicionário de :func:`seletores`/:func:`buscar`;
* elemento sem seletor do tipo pedido → :class:`ErroSeletorIndisponivel` ou a
  propriedade ``ausentes`` do dicionário de :func:`seletores`/:func:`buscar`.

Em nenhum desses casos um ``""`` é devolvido para ser entregue ao Navegador.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from core.tratador import ErroRPA

TIPOS = {
    "relativo": "relativo",
    "relative": "relativo",
    "custom": "custom",
    "completo": "completo",
    "full": "completo",
}


@dataclass(frozen=True)
class Elemento:
    """Um elemento capturado pelo TrackTab, com seus três seletores."""

    nome: str
    tag: str
    texto: str
    completo: str
    relativo: str
    custom: str

    @property
    def seletor(self) -> str:
        """Melhor seletor disponível (relativo → custom → completo)."""
        return self.relativo or self.custom or self.completo


class ErroElementoAusente(ErroRPA):
    """O nome pedido não existe no export (diferente de nome repetido)."""

    def __init__(self, nome: str, caminho: str | Path | None = None):
        self.nome = nome
        self.caminho = str(caminho) if caminho is not None else None
        super().__init__(f"Elemento '{nome}' não encontrado no TrackTab.")


class ErroNomeDuplicado(ErroRPA):
    """O nome existe mais de uma vez no export; só a primeira ocorrência vale."""

    def __init__(
        self,
        nome: str,
        *,
        arquivo: str | Path | None = None,
        mantido_em: str | Path | None = None,
        quantidade: int = 1,
    ):
        self.nome = nome
        self.arquivo = str(arquivo) if arquivo is not None else None
        self.mantido_em = str(mantido_em) if mantido_em is not None else None
        self.quantidade = quantidade
        origem = f" em {arquivo}" if arquivo is not None else ""
        super().__init__(
            f"Elemento '{nome}' está repetido{origem} "
            f"({quantidade} ocorrência(s) ignorada(s)); use carregar_detalhado() "
            "para inspecionar os conflitos."
        )


class ErroSeletorIndisponivel(ErroRPA):
    """O elemento existe, mas não tem seletor do tipo pedido."""

    def __init__(self, nome: str, tipo: str, caminho: str | Path | None = None):
        self.nome = nome
        self.tipo = tipo
        self.caminho = str(caminho) if caminho is not None else None
        super().__init__(
            f"Elemento '{nome}' não possui seletor do tipo '{tipo}' no TrackTab."
        )


class ErroSeletores(ErroRPA):
    """Erro agregado ao pedir seletores em modo estrito."""

    def __init__(
        self,
        *,
        ausentes: tuple[str, ...] = (),
        conflitos: tuple[Conflito, ...] = (),
        tipo: str | None = None,
    ):
        self.ausentes = tuple(ausentes)
        self.conflitos = tuple(conflitos)
        self.tipo = tipo
        partes = []
        if ausentes:
            rotulo = f" do tipo '{tipo}'" if tipo else ""
            partes.append(
                "sem seletor" + rotulo + ": " + ", ".join(sorted(ausentes))
            )
        if conflitos:
            nomes = sorted({conflito.nome for conflito in conflitos})
            partes.append("nomes repetidos: " + ", ".join(nomes))
        detalhe = "; ".join(partes) or "problemas nos seletores"
        super().__init__(f"TrackTab: {detalhe}. Use estrito=False para ignorar.")


class Seletores(dict):
    """``{nome: seletor}`` com o registro dos problemas encontrados.

    Continua sendo um ``dict`` normal (comparações e acessos por chave são
    idênticos ao comportamento anterior). Os metadados evitam que o chamador
    confunda "nome ausente", "nome repetido" e "elemento sem seletor":

    - ``ausentes``: nomes que ficaram de fora por não terem seletor do tipo;
    - ``conflitos``: :class:`Conflito` dos nomes repetidos (vale a primeira
      ocorrência, como em :func:`carregar`);
    - ``duplicados``: somente os nomes repetidos.
    """

    def __init__(
        self,
        dados: dict[str, str] | None = None,
        *,
        ausentes: tuple[str, ...] = (),
        conflitos: tuple[Conflito, ...] = (),
    ):
        super().__init__(dados or {})
        self.ausentes = tuple(ausentes)
        self.conflitos = tuple(conflitos)

    @property
    def duplicados(self) -> tuple[str, ...]:
        """Nomes repetidos que influenciaram o resultado."""
        return tuple(sorted({conflito.nome for conflito in self.conflitos}))

    @property
    def tem_problemas(self) -> bool:
        return bool(self.ausentes or self.conflitos)


@dataclass(frozen=True)
class Conflito:
    """Uma ocorrência (ou lote) de nome repetido.

    ``arquivo`` é onde a ocorrência ignorada foi encontrada e ``mantido_em``
    é o arquivo do elemento que prevaleceu — iguais dentro de um mesmo export
    e diferentes em :func:`combinar_detalhado`.
    """

    nome: str
    arquivo: str
    mantido_em: str
    ignorados: tuple[Elemento, ...] = field(default_factory=tuple)

    @property
    def quantidade(self) -> int:
        """Quantos elementos foram ignorados por causa deste nome."""
        return len(self.ignorados)

    @property
    def arquivo_nome(self) -> str:
        return Path(self.arquivo).name

    @property
    def mantido_em_nome(self) -> str:
        return Path(self.mantido_em).name


@dataclass(frozen=True)
class ResultadoCarregamento:
    """Resultado completo de um (ou vários) exports.

    - ``elementos``: ``{nome: Elemento}`` com a primeira ocorrência de cada nome
      (mesmo formato devolvido por :func:`carregar`);
    - ``conflitos``: nomes repetidos, com os elementos ignorados e a origem;
    - ``lidos``: **todos** os elementos do JSON, inclusive os ignorados (ordem
      do arquivo);
    - ``arquivos``: exports que participaram da leitura.
    """

    elementos: dict[str, Elemento]
    conflitos: tuple[Conflito, ...] = ()
    lidos: tuple[Elemento, ...] = ()
    arquivos: tuple[str, ...] = ()

    @property
    def total_lido(self) -> int:
        return len(self.lidos)

    @property
    def ignorados(self) -> tuple[Elemento, ...]:
        return tuple(
            elemento
            for conflito in self.conflitos
            for elemento in conflito.ignorados
        )

    @property
    def duplicados(self) -> dict[str, int]:
        """``{nome: quantidade de ocorrências ignoradas}``."""
        contagem: Counter[str] = Counter()
        for conflito in self.conflitos:
            contagem[conflito.nome] += conflito.quantidade
        return dict(contagem)

    @property
    def tem_conflitos(self) -> bool:
        return bool(self.conflitos)

    def conflito_de(self, nome: str) -> Conflito | None:
        """Primeiro conflito registrado para ``nome`` (ou ``None``)."""
        for conflito in self.conflitos:
            if conflito.nome == nome:
                return conflito
        return None


def _ler_export(caminho: str | Path) -> dict:
    arquivo = Path(caminho)
    if not arquivo.is_file():
        raise ErroRPA(f"Export do TrackTab não encontrado: {arquivo}")
    try:
        return json.loads(arquivo.read_text(encoding="utf-8"))
    except json.JSONDecodeError as erro:
        raise ErroRPA(f"JSON inválido em {arquivo}: {erro}") from erro


def _montar_elemento(item: dict[str, Any]) -> Elemento:
    return Elemento(
        nome=item.get("name") or item.get("tag") or "elemento",
        tag=item.get("tag", ""),
        texto=item.get("text", ""),
        completo=item.get("fullXpath", ""),
        relativo=item.get("relativeXpath", ""),
        custom=item.get("customPath", ""),
    )


def carregar_detalhado(caminho: str | Path) -> ResultadoCarregamento:
    """Lê um export e devolve elementos **e** o registro de nomes repetidos.

    Diferente de :func:`carregar`, preserva os elementos ignorados em
    ``conflitos``/``lidos``, permitindo que o chamador saiba quais nomes se
    repetiram e quantas ocorrências foram descartadas.
    """
    arquivo = Path(caminho)
    arquivo_str = str(arquivo)
    dados = _ler_export(arquivo)

    elementos: dict[str, Elemento] = {}
    origens: dict[str, str] = {}
    ignorados: dict[str, list[Elemento]] = {}
    lidos: list[Elemento] = []

    for item in dados.get("elements", []):
        elemento = _montar_elemento(item)
        lidos.append(elemento)
        if elemento.nome in elementos:
            ignorados.setdefault(elemento.nome, []).append(elemento)
        else:
            elementos[elemento.nome] = elemento
            origens[elemento.nome] = arquivo_str

    conflitos = tuple(
        Conflito(
            nome=nome,
            arquivo=arquivo_str,
            mantido_em=origens[nome],
            ignorados=tuple(itens),
        )
        for nome, itens in ignorados.items()
    )
    for conflito in conflitos:
        logger.warning(
            f"TrackTab: nome '{conflito.nome}' repetido em {arquivo.name}; "
            f"{conflito.quantidade} ocorrência(s) ignorada(s) "
            "(mantida a primeira)."
        )

    logger.success(
        f"TrackTab: {len(elementos)} elemento(s) carregado(s) de {arquivo.name}"
    )
    return ResultadoCarregamento(
        elementos=elementos,
        conflitos=conflitos,
        lidos=tuple(lidos),
        arquivos=(arquivo_str,),
    )


def carregar(caminho: str | Path) -> dict[str, Elemento]:
    """Lê um export do TrackTab e devolve ``{nome: Elemento}``.

    Em caso de nomes repetidos, mantém o primeiro e registra aviso no log. Use
    :func:`carregar_detalhado` para consultar os conflitos programaticamente.
    """
    return carregar_detalhado(caminho).elementos


def _validar_tipo(tipo: str) -> str:
    chave = TIPOS.get(tipo)
    if chave is None:
        raise ErroRPA(f"Tipo de seletor inválido: {tipo!r}. Use: {', '.join(sorted(TIPOS))}.")
    return chave


def seletores(caminho: str | Path, tipo: str = "relativo", *, estrito: bool = False) -> dict[str, str]:
    """Devolve ``{nome: seletor}`` usando o tipo pedido.

    ``tipo`` aceita ``relativo`` (padrão), ``custom`` ou ``completo``.

    Elementos sem seletor do tipo pedido **não entram** no dicionário e seus
    nomes ficam em ``resultado.ausentes``; nomes repetidos mantêm a primeira
    ocorrência e ficam em ``resultado.conflitos``/``resultado.duplicados``.
    Com ``estrito=True``, qualquer ausência ou conflito gera
    :class:`ErroSeletores` em vez de resultado marcado.
    """
    chave = _validar_tipo(tipo)
    resultado = carregar_detalhado(caminho)
    ausentes: list[str] = []
    dados: dict[str, str] = {}

    for nome, elemento in resultado.elementos.items():
        valor = getattr(elemento, chave)
        if not valor:
            ausentes.append(nome)
            logger.warning(
                f"TrackTab: elemento '{nome}' não possui seletor '{tipo}' "
                f"em {Path(caminho).name}; ficou fora do resultado."
            )
            continue
        dados[nome] = valor

    seletores_resultado = Seletores(
        dados,
        ausentes=tuple(ausentes),
        conflitos=resultado.conflitos,
    )
    if estrito and seletores_resultado.tem_problemas:
        raise ErroSeletores(
            ausentes=seletores_resultado.ausentes,
            conflitos=seletores_resultado.conflitos,
            tipo=tipo,
        )
    return seletores_resultado


def obter(
    caminho: str | Path,
    nome: str,
    tipo: str = "relativo",
    *,
    permitir_duplicado: bool = False,
) -> str:
    """Retorna o seletor de um elemento pelo nome exato.

    Levanta erros distintos para cada causa de "seletor vazio":

    - :class:`ErroElementoAusente` — não existe elemento com esse nome;
    - :class:`ErroNomeDuplicado` — o nome aparece mais de uma vez (use
      ``permitir_duplicado=True`` para aceitar a primeira ocorrência);
    - :class:`ErroSeletorIndisponivel` — o elemento existe, mas não tem seletor
      do tipo pedido.
    """
    chave = _validar_tipo(tipo)
    resultado = carregar_detalhado(caminho)
    elemento = resultado.elementos.get(nome)
    if elemento is None:
        raise ErroElementoAusente(nome, caminho)
    conflito = resultado.conflito_de(nome)
    if conflito is not None and not permitir_duplicado:
        raise ErroNomeDuplicado(
            nome,
            arquivo=conflito.arquivo,
            mantido_em=conflito.mantido_em,
            quantidade=conflito.quantidade,
        )
    valor = getattr(elemento, chave)
    if not valor:
        raise ErroSeletorIndisponivel(nome, tipo, caminho)
    return valor


def buscar(
    caminho: str | Path,
    nome_contem: str,
    tipo: str = "relativo",
    *,
    estrito: bool = False,
) -> dict[str, str]:
    """Busca elementos cujo nome contenha o texto (sem diferenciar maiúsculas).

    Como :func:`seletores`, omite nomes sem seletor do tipo (em ``ausentes``)
    e marca os repetidos (em ``conflitos``) em vez de devolver ``""``.
    """
    chave = _validar_tipo(tipo)
    alvo = nome_contem.lower()
    resultado = carregar_detalhado(caminho)
    encontrados = {
        nome: elemento
        for nome, elemento in resultado.elementos.items()
        if alvo in nome.lower()
    }
    conflitos = tuple(
        conflito for conflito in resultado.conflitos if conflito.nome in encontrados
    )
    ausentes: list[str] = []
    dados: dict[str, str] = {}
    for nome, elemento in encontrados.items():
        valor = getattr(elemento, chave)
        if not valor:
            ausentes.append(nome)
            logger.warning(
                f"TrackTab: elemento '{nome}' não possui seletor '{tipo}' "
                f"em {Path(caminho).name}; ficou fora da busca."
            )
            continue
        dados[nome] = valor

    encontrados_resultado = Seletores(
        dados,
        ausentes=tuple(ausentes),
        conflitos=conflitos,
    )
    if estrito and encontrados_resultado.tem_problemas:
        raise ErroSeletores(
            ausentes=encontrados_resultado.ausentes,
            conflitos=encontrados_resultado.conflitos,
            tipo=tipo,
        )
    logger.info(f"TrackTab: {len(dados)} elemento(s) casaram com '{nome_contem}'.")
    return encontrados_resultado


def listar_nomes(caminho: str | Path, *, incluir_repetidos: bool = False) -> list[str]:
    """Lista os nomes dos elementos capturados.

    Com ``incluir_repetidos=True``, devolve uma entrada por ocorrência (na
    ordem do export), permitindo enxergar os nomes que seriam sobrescritos.
    """
    if incluir_repetidos:
        return [elemento.nome for elemento in carregar_detalhado(caminho).lidos]
    return list(carregar(caminho))


def para_lista(caminho: str | Path) -> list[dict[str, Any]]:
    """Converte o export em uma lista de dicionários (pronta para DataFrame/CSV)."""
    return [
        {
            "nome": elemento.nome,
            "tag": elemento.tag,
            "texto": elemento.texto,
            "relativo": elemento.relativo,
            "custom": elemento.custom,
            "completo": elemento.completo,
        }
        for elemento in carregar(caminho).values()
    ]


def exportar_csv(
    caminho_export: str | Path,
    caminho_csv: str | Path,
    tipo: str = "relativo",
) -> Path:
    """Exporta ``nome`` e o seletor escolhido para um CSV."""
    chave = _validar_tipo(tipo)
    caminho_csv = Path(caminho_csv)
    caminho_csv.parent.mkdir(parents=True, exist_ok=True)
    with caminho_csv.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=["nome", "seletor"])
        escritor.writeheader()
        for nome, elemento in carregar(caminho_export).items():
            escritor.writerow({"nome": nome, "seletor": getattr(elemento, chave)})
    logger.success(f"TrackTab exportado para CSV: {caminho_csv}")
    return caminho_csv


def resumo(caminho: str | Path) -> dict[str, Any]:
    """Resumo do export: total, tags e quantos elementos têm cada seletor."""
    elementos = carregar(caminho)
    dados = {
        "total": len(elementos),
        "tags": dict(Counter(elemento.tag for elemento in elementos.values())),
        "com_relativo": sum(1 for elemento in elementos.values() if elemento.relativo),
        "com_custom": sum(1 for elemento in elementos.values() if elemento.custom),
        "com_completo": sum(1 for elemento in elementos.values() if elemento.completo),
    }
    logger.info(f"TrackTab: resumo {dados['total']} elemento(s).")
    return dados


def combinar_detalhado(*caminhos: str | Path) -> ResultadoCarregamento:
    """Junta vários exports preservando a origem de cada conflito.

    Dentro de cada arquivo vale a primeira ocorrência; entre arquivos também
    vale o primeiro arquivo. Os nomes repetidos ficam em ``conflitos`` com
    ``arquivo`` (onde estava a ocorrência ignorada) e ``mantido_em`` (arquivo
    do elemento que prevaleceu), além de ``duplicados`` com as quantidades.
    """
    if not caminhos:
        raise ErroRPA("Informe ao menos um export do TrackTab.")

    elementos: dict[str, Elemento] = {}
    origens: dict[str, str] = {}
    conflitos: list[Conflito] = []
    lidos: list[Elemento] = []
    arquivos: list[str] = []

    for caminho in caminhos:
        resultado = carregar_detalhado(caminho)
        arquivo = resultado.arquivos[0]
        arquivos.append(arquivo)
        lidos.extend(resultado.lidos)
        conflitos.extend(resultado.conflitos)
        for nome, elemento in resultado.elementos.items():
            if nome in elementos:
                conflito = Conflito(
                    nome=nome,
                    arquivo=arquivo,
                    mantido_em=origens[nome],
                    ignorados=(elemento,),
                )
                conflitos.append(conflito)
                logger.warning(
                    f"TrackTab: nome '{nome}' repetido entre arquivos; "
                    f"mantido em {conflito.mantido_em_nome} e ignorado em "
                    f"{conflito.arquivo_nome}."
                )
                continue
            elementos[nome] = elemento
            origens[nome] = arquivo

    logger.success(f"TrackTab: {len(elementos)} elemento(s) combinado(s).")
    return ResultadoCarregamento(
        elementos=elementos,
        conflitos=tuple(conflitos),
        lidos=tuple(lidos),
        arquivos=tuple(arquivos),
    )


def combinar(*caminhos: str | Path) -> dict[str, Elemento]:
    """Junta vários exports do TrackTab em um único dicionário.

    Em nomes repetidos (dentro de um arquivo ou entre arquivos), mantém a
    primeira ocorrência e registra aviso. Use :func:`combinar_detalhado` para
    saber de qual arquivo veio cada conflito.
    """
    return combinar_detalhado(*caminhos).elementos


__all__ = [
    "Elemento",
    "TIPOS",
    "Conflito",
    "ResultadoCarregamento",
    "Seletores",
    "ErroElementoAusente",
    "ErroNomeDuplicado",
    "ErroSeletorIndisponivel",
    "ErroSeletores",
    "carregar",
    "carregar_detalhado",
    "seletores",
    "obter",
    "buscar",
    "listar_nomes",
    "para_lista",
    "exportar_csv",
    "resumo",
    "combinar",
    "combinar_detalhado",
]
