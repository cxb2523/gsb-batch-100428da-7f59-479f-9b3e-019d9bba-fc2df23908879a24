"""
Esse bot clica no botao seguir dos perfis listados em seguidores ou seguindo de uma conta especifica.
Ele navega ate o perfil alvo, abre a lista configurada e clica no botao seguir dentro do modal.
"""


import argparse
import getpass
import importlib
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import time


PACOTES_AUTOMACAO = [
    {"modulo": "selenium", "pacote": "selenium"},
    {"modulo": "webdriver_manager", "pacote": "webdriver-manager"},
]

MODO_EXECUCAO = "codigo" # opcoes: "codigo" ou "terminal"

# Arquivo local com o progresso do seguimento (pode ser trocado por --arquivo-progresso).
ARQUIVO_PROGRESSO_PADRAO = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "progresso_seguindo.json",
)

VERSAO_PROGRESSO = 1

#PREENCHA COM OS DADOS DA CONTA PARA LOGAR
DADOS_BOT_CODIGO = {
    "usuario": "",
    "senha": "",
    "usa_codigo_verificacao": False,
    "codigo_verificacao": "", # codigo de verificacao em 2 fatores, se a conta tiver habilitado
    "perfil_alvo": "", # perfil do Instagram onde vai seguir os perfis seguidos
    "tipo_lista": "seguidores", # opcoes: "seguidores" ou "seguindo"
    "limite_seguir": 50, # quantidade maxima de perfis para seguir por lote
    "intervalo_lote_segundos": 3600, # 1h de intervalo entre lotes de seguimento
    "arquivo_progresso": "", # opcional: caminho do JSON de progresso; vazio usa ARQUIVO_PROGRESSO_PADRAO
}


def obter_pacotes_ausentes(pacotes_obrigatorios):
    pacotes_ausentes = []

    for dependencia in pacotes_obrigatorios:
        if importlib.util.find_spec(dependencia["modulo"]) is None:
            pacotes_ausentes.append(dependencia)

    return pacotes_ausentes


def obter_comando_pip():
    if platform.system().lower().startswith("win"):
        return "pip"

    return "pip3"


def tentar_instalar_pacotes(pacotes_ausentes, pacotes_obrigatorios):
    nomes_pacotes = [dependencia["pacote"] for dependencia in pacotes_ausentes]
    comandos_instalacao = [
        [obter_comando_pip(), "install", *nomes_pacotes],
        [sys.executable, "-m", "pip", "install", *nomes_pacotes],
    ]

    for comando in comandos_instalacao:
        print("Tentando instalar pacotes: " + " ".join(comando))

        try:
            resultado = subprocess.run(comando, check=False)
        except FileNotFoundError:
            print("Comando nao encontrado: " + comando[0])
            continue

        importlib.invalidate_caches()

        if resultado.returncode == 0 and not obter_pacotes_ausentes(pacotes_obrigatorios):
            return True

    return False


def mostrar_instrucoes_instalacao(pacotes_ausentes):
    nomes_pacotes = " ".join(dependencia["pacote"] for dependencia in pacotes_ausentes)

    print("\nNao foi possivel instalar todos os pacotes automaticamente.")
    print("Ative seu ambiente virtual e execute um destes comandos:")
    print(f"{obter_comando_pip()} install {nomes_pacotes}")
    print(f"{sys.executable} -m pip install {nomes_pacotes}")
    print("\nSe o pip nao estiver instalado, instale/atualize o pip antes de rodar o bot.")


def verificar_pacotes_instalados():
    pacotes_obrigatorios = PACOTES_AUTOMACAO.copy()
    pacotes_ausentes = obter_pacotes_ausentes(pacotes_obrigatorios)

    if not pacotes_ausentes:
        return True

    print("Pacotes ausentes:")
    for dependencia in pacotes_ausentes:
        print("- " + dependencia["pacote"])

    if tentar_instalar_pacotes(pacotes_ausentes, pacotes_obrigatorios):
        return True

    mostrar_instrucoes_instalacao(obter_pacotes_ausentes(pacotes_obrigatorios))
    return False


def carregar_dependencias_externas():
    global webdriver
    global NoSuchElementException
    global WebDriverException
    global TimeoutException
    global Keys
    global By
    global ChromeService
    global ChromeDriverManager
    global Options
    global WebDriverWait
    global EC

    from selenium import webdriver as selenium_webdriver
    from selenium.common.exceptions import NoSuchElementException as SeleniumNoSuchElementException
    from selenium.common.exceptions import WebDriverException as SeleniumWebDriverException
    from selenium.common.exceptions import TimeoutException as SeleniumTimeoutException
    from selenium.webdriver.common.keys import Keys as SeleniumKeys
    from selenium.webdriver.common.by import By as SeleniumBy
    from selenium.webdriver.chrome.options import Options as SeleniumChromeOptions
    from selenium.webdriver.chrome.service import Service as SeleniumChromeService
    from selenium.webdriver.support import expected_conditions as SeleniumEC
    from selenium.webdriver.support.wait import WebDriverWait as SeleniumWebDriverWait
    from webdriver_manager.chrome import ChromeDriverManager as SeleniumChromeDriverManager

    webdriver = selenium_webdriver
    NoSuchElementException = SeleniumNoSuchElementException
    WebDriverException = SeleniumWebDriverException
    TimeoutException = SeleniumTimeoutException
    Keys = SeleniumKeys
    By = SeleniumBy
    ChromeService = SeleniumChromeService
    ChromeDriverManager = SeleniumChromeDriverManager
    Options = SeleniumChromeOptions
    WebDriverWait = SeleniumWebDriverWait
    EC = SeleniumEC


XPATHS = {
    "campo_usuario": [
        "//input[@name='email']",
        "//input[@name='username']",
        "//input[contains(@autocomplete, 'username')]",
    ],
    "campo_senha": [
        "//input[@name='pass']",
        "//input[@name='password']",
        "//input[@type='password']",
    ],
    "botao_entrar": [
        "//*[@role='button' and @aria-label='Entrar']",
        "//button[@type='submit']",
        "//*[@role='button' and .//span[normalize-space()='Entrar']]",
    ],
    "campo_verificacao_codigo": [
        "//input[@name='verificationCode']",
        "//input[contains(@autocomplete, 'one-time-code')]",
    ],
    "tela_verificacao_manual": [
        "//*[contains(normalize-space(.), 'Verifique seu email')]",
        "//*[contains(normalize-space(.), 'Insira o código que enviamos')]",
        "//*[contains(normalize-space(.), 'Insira o codigo que enviamos')]",
        "//input[@name='email' and @autocomplete='off']",
    ],
    "link_seguidores": [
        "//header//a[contains(@href, '/followers')]",
        "//a[contains(@href, '/followers')]",
        "//a[contains(@href, 'followers')]",
        "//a[.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'followers')]]",
        "//a[.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'seguidores')]]",
    ],
    "link_seguindo": [
        "//header//a[contains(@href, '/following')]",
        "//a[contains(@href, '/following')]",
        "//a[contains(@href, 'following')]",
        "//a[.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'following')]]",
        "//a[.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'seguindo')]]",
    ],
    "modal_lista_perfis": [
        "//div[@role='dialog' and .//*[normalize-space()='Seguidores']]",
        "//div[@role='dialog' and .//*[normalize-space()='Seguindo']]",
        "//div[@role='dialog']",
    ],
    "botao_seguir_modal": [
        ".//button[.//div[normalize-space()='Seguir']]",
        ".//*[@role='button' and .//*[normalize-space()='Seguir']]",
        ".//button[normalize-space()='Seguir']",
    ],
}


def encontrar_elemento(driver, nome_elemento):
    for xpath in XPATHS[nome_elemento]:
        try:
            return driver.find_element(By.XPATH, xpath)
        except NoSuchElementException:
            continue

    raise NoSuchElementException("Nao foi possivel encontrar o elemento: " + nome_elemento)


def elemento_existe(driver, nome_elemento):
    for xpath in XPATHS[nome_elemento]:
        if driver.find_elements(By.XPATH, xpath):
            return True

    return False


def esperar_elemento(driver, nome_elemento, timeout=20, clicavel=False):
    ultimo_erro = None

    for xpath in XPATHS[nome_elemento]:
        try:
            condicao = EC.element_to_be_clickable((By.XPATH, xpath)) if clicavel else EC.presence_of_element_located((By.XPATH, xpath))
            return WebDriverWait(driver, timeout).until(condicao)
        except TimeoutException as erro:
            ultimo_erro = erro

    raise NoSuchElementException("Nao foi possivel encontrar o elemento: " + nome_elemento) from ultimo_erro


def encontrar_elemento_dentro(elemento_base, nome_elemento):
    for xpath in XPATHS[nome_elemento]:
        elementos = elemento_base.find_elements(By.XPATH, xpath)
        if elementos:
            return elementos[0]

    raise NoSuchElementException("Nao foi possivel encontrar o elemento: " + nome_elemento)


def encontrar_elementos_dentro(elemento_base, nome_elemento):
    elementos_encontrados = []
    ids_encontrados = set()

    for xpath in XPATHS[nome_elemento]:
        for elemento in elemento_base.find_elements(By.XPATH, xpath):
            if elemento.id in ids_encontrados:
                continue

            ids_encontrados.add(elemento.id)
            elementos_encontrados.append(elemento)

    return elementos_encontrados


def esperar_elemento_dentro(elemento_base, nome_elemento, timeout=20):
    fim = time.time() + timeout

    while time.time() < fim:
        try:
            return encontrar_elemento_dentro(elemento_base, nome_elemento)
        except NoSuchElementException:
            time.sleep(0.5)

    raise NoSuchElementException("Nao foi possivel encontrar o elemento dentro do modal: " + nome_elemento)


def salvar_diagnostico(driver, contexto):
    caminho_screenshot = f"/tmp/instagrambot_{contexto}.png"

    print("\nDiagnostico da pagina:")
    print("URL atual: " + driver.current_url)
    print("Titulo: " + driver.title)

    try:
        driver.save_screenshot(caminho_screenshot)
        print("Screenshot salvo em: " + caminho_screenshot)
    except WebDriverException:
        print("Nao foi possivel salvar screenshot de diagnostico.")


def extrair_total_perfis_lista(texto_total):
    texto_normalizado = texto_total.lower().replace("seguindo", "").replace("following", "").strip()
    match = re.search(r"[\d.,]+", texto_normalizado)

    if not match:
        raise ValueError("Nao foi possivel identificar o total de perfis no texto: " + texto_total)

    return int(match.group(0).replace(".", "").replace(",", ""))


def extrair_usuario_de_href(href):
    if not href:
        return None

    caminho = re.sub(r"^https?://[^/]+/", "", href.strip())
    caminho = caminho.split("?", 1)[0].split("#", 1)[0].strip("/")

    if not caminho:
        return None

    segmento = caminho.split("/", 1)[0]

    if segmento.lower() in ("accounts", "about", "developer", "direct", "explore", "p", "reels", "stories"):
        return None

    if not re.match(r"^[A-Za-z0-9._]{1,30}$", segmento):
        return None

    return segmento.lower()


def obter_perfil_do_botao(botao_seguir):
    try:
        links = botao_seguir.find_elements(
            By.XPATH,
            "ancestor::div[.//a[starts-with(@href, '/') or contains(@href, 'instagram.com/')]][1]"
            "//a[starts-with(@href, '/') or contains(@href, 'instagram.com/')]",
        )
    except Exception:
        return None

    for link in links:
        perfil = extrair_usuario_de_href(link.get_attribute("href"))

        if perfil:
            return perfil

    return None


def resumir_erro(erro):
    mensagem = " ".join(str(erro).split())

    if mensagem:
        resumo = f"{type(erro).__name__}: {mensagem}"
    else:
        resumo = type(erro).__name__

    if len(resumo) > 300:
        resumo = resumo[:297] + "..."

    return resumo


class ProgressoSeguir:
    """Progresso local do seguimento, gravado em JSON apos cada tentativa."""

    def __init__(self, caminho, perfil_alvo, tipo_lista, total_perfis_lista, imprimir=print):
        self.caminho = caminho
        self.perfil_alvo = perfil_alvo
        self.tipo_lista = tipo_lista
        self.total_perfis_lista = total_perfis_lista
        self.imprimir = imprimir
        self.indices = {}
        self.seguidos = {}
        self.falhas = {}
        self.posicao_processada = 0
        self.proximo_indice = 1

    @classmethod
    def abrir(cls, caminho, perfil_alvo, tipo_lista, total_perfis_lista, imprimir=print):
        progresso = cls(caminho, perfil_alvo, tipo_lista, total_perfis_lista, imprimir)
        progresso.carregar()
        return progresso

    def _limpar(self):
        self.indices = {}
        self.seguidos = {}
        self.falhas = {}
        self.posicao_processada = 0
        self.proximo_indice = 1

    def _recalcular_proximo_indice(self):
        maiores_indices = [self.posicao_processada]
        maiores_indices.extend(self.indices.values())
        maiores_indices.extend(self.seguidos.values())
        maiores_indices.extend(falha["indice"] for falha in self.falhas.values())
        self.proximo_indice = max(maiores_indices, default=0) + 1

    def _aplicar_dicionario(self, dados):
        if dados.get("versao") != VERSAO_PROGRESSO:
            raise ValueError("versao de progresso desconhecida")

        if dados.get("perfil_alvo") != self.perfil_alvo or dados.get("tipo_lista") != self.tipo_lista:
            raise ValueError("progresso pertence a outro perfil ou tipo de lista")

        indices_carregados = dados.get("indices", {})

        if not isinstance(indices_carregados, dict):
            raise ValueError("campo indices invalido")

        for perfil, indice in indices_carregados.items():
            if not isinstance(perfil, str) or not perfil:
                raise ValueError("perfil invalido no campo indices")

            self.indices[perfil] = int(indice)

            if self.indices[perfil] <= 0:
                raise ValueError("indice invalido no campo indices")

        for item in dados.get("seguidos", []):
            if not isinstance(item, dict):
                raise ValueError("item de seguidos invalido")

            perfil = item.get("perfil")
            indice = int(item.get("indice", 0))

            if not isinstance(perfil, str) or not perfil or indice <= 0:
                raise ValueError("item de seguidos invalido")

            self.seguidos[perfil] = indice
            self.indices.setdefault(perfil, indice)

        for item in dados.get("falhas", []):
            if not isinstance(item, dict):
                raise ValueError("item de falhas invalido")

            perfil = item.get("perfil")
            indice = int(item.get("indice", 0))
            motivo = item.get("motivo", "")

            if not isinstance(perfil, str) or not perfil or indice <= 0:
                raise ValueError("item de falhas invalido")

            self.falhas[perfil] = {"indice": indice, "motivo": str(motivo)}
            self.indices.setdefault(perfil, indice)

        self.posicao_processada = int(dados.get("posicao_processada", 0))

        if self.posicao_processada < 0:
            raise ValueError("posicao processada invalida")

        total_salvo = dados.get("total_perfis_lista")

        if isinstance(total_salvo, int) and total_salvo != self.total_perfis_lista:
            self.imprimir(
                f"Aviso: o progresso foi salvo com total {total_salvo} e a lista atual tem "
                f"{self.total_perfis_lista}. Perfis ja seguidos serao pulados mesmo assim."
            )

        self._recalcular_proximo_indice()

    def carregar(self):
        if not os.path.exists(self.caminho):
            self.imprimir(
                f"Arquivo de progresso nao encontrado ({self.caminho}). Iniciando o seguimento do zero."
            )
            return

        try:
            with open(self.caminho, "r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
        except (OSError, ValueError) as erro:
            self.imprimir(
                f"Arquivo de progresso invalido ou corrompido ({self.caminho}): {erro}. Iniciando o seguimento do zero."
            )
            self._limpar()
            return

        if not isinstance(dados, dict):
            self.imprimir(
                f"Arquivo de progresso invalido ou corrompido ({self.caminho}): formato inesperado. Iniciando o seguimento do zero."
            )
            self._limpar()
            return

        try:
            self._aplicar_dicionario(dados)
        except (KeyError, TypeError, ValueError) as erro:
            self.imprimir(
                f"Arquivo de progresso invalido ou corrompido ({self.caminho}): {erro}. Iniciando o seguimento do zero."
            )
            self._limpar()
            return

        self.imprimir(
            f"Progresso carregado de {self.caminho}: posicao processada {self.posicao_processada}, "
            f"{len(self.seguidos)} perfil(is) ja seguido(s) e {len(self.falhas)} falha(s) pendente(s)."
        )

        falhas_pendentes = self.listar_falhas_pendentes()

        if falhas_pendentes:
            self.imprimir("Falhas da execucao anterior que serao tentadas novamente:")

            for falha in falhas_pendentes:
                self.imprimir(
                    f"- Perfil {falha['indice']} ({falha['perfil']}): {falha['motivo']}"
                )

    def _dados_para_salvar(self):
        return {
            "versao": VERSAO_PROGRESSO,
            "perfil_alvo": self.perfil_alvo,
            "tipo_lista": self.tipo_lista,
            "total_perfis_lista": self.total_perfis_lista,
            "posicao_processada": self.posicao_processada,
            "indices": {perfil: indice for perfil, indice in sorted(self.indices.items(), key=lambda item: item[1])},
            "seguidos": [
                {"indice": indice, "perfil": perfil}
                for perfil, indice in sorted(self.seguidos.items(), key=lambda item: item[1])
            ],
            "falhas": [
                {"indice": falha["indice"], "perfil": perfil, "motivo": falha["motivo"]}
                for perfil, falha in sorted(self.falhas.items(), key=lambda item: item[1]["indice"])
            ],
            "atualizado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def salvar(self):
        diretorio = os.path.dirname(os.path.abspath(self.caminho))

        try:
            os.makedirs(diretorio, exist_ok=True)
            caminho_temporario = self.caminho + ".tmp"

            with open(caminho_temporario, "w", encoding="utf-8") as arquivo:
                json.dump(self._dados_para_salvar(), arquivo, ensure_ascii=False, indent=2)
                arquivo.flush()
                os.fsync(arquivo.fileno())

            os.replace(caminho_temporario, self.caminho)
            return True
        except OSError as erro:
            self.imprimir(f"Nao foi possivel salvar o progresso em {self.caminho}: {erro}")
            return False

    def obter_indice(self, perfil):
        if perfil and perfil in self.indices:
            return self.indices[perfil]

        indice = self.proximo_indice
        self.proximo_indice += 1

        if perfil:
            self.indices[perfil] = indice

        return indice

    def ja_seguido(self, perfil):
        return bool(perfil) and perfil in self.seguidos

    def registrar_sucesso(self, perfil, indice):
        if perfil:
            self.indices[perfil] = indice
            self.seguidos[perfil] = indice
            self.falhas.pop(perfil, None)

        self.posicao_processada = max(self.posicao_processada, indice)
        self.proximo_indice = max(self.proximo_indice, indice + 1)
        self.salvar()

    def registrar_falha(self, perfil, motivo, indice):
        motivo_formatado = resumir_erro(motivo) if isinstance(motivo, BaseException) else str(motivo)

        if perfil:
            self.indices.setdefault(perfil, indice)
            self.falhas[perfil] = {"indice": indice, "motivo": motivo_formatado}

        self.posicao_processada = max(self.posicao_processada, indice)
        self.proximo_indice = max(self.proximo_indice, indice + 1)
        self.salvar()

    def listar_falhas_pendentes(self):
        return [
            {"indice": falha["indice"], "perfil": perfil, "motivo": falha["motivo"]}
            for perfil, falha in sorted(self.falhas.items(), key=lambda item: item[1]["indice"])
        ]


ROTULOS_GENERICOS_BOTAO = {"seguir", "seguindo", "follow", "following", "unfollow"}


def _chave_do_botao(botao, perfil):
    """Identidade estavel do botao para pular perfis ja seguidos.

    Usa o perfil quando o Selenium consegue identificar o dono do botao. Caso contrario,
    tenta atributos comuns em botoes de teste (perfil, username, nome, label...) e chaves
    de dicionario. Botoes sem identidade estavel nao entram no arquivo de progresso.
    """

    if perfil:
        return perfil

    if isinstance(botao, str):
        texto = " ".join(botao.split())

        if texto and len(texto) <= 60 and texto.lower() not in ROTULOS_GENERICOS_BOTAO:
            return texto

        return None

    if isinstance(botao, dict):
        for chave in ("perfil", "username", "usuario", "nome", "label"):
            valor = botao.get(chave)

            if isinstance(valor, str):
                valor = " ".join(valor.split())

                if valor and len(valor) <= 60 and valor.lower() not in ROTULOS_GENERICOS_BOTAO:
                    return valor

        return None

    for atributo in ("perfil", "username", "usuario", "nome", "label"):
        try:
            valor = getattr(botao, atributo, None)
        except Exception:
            continue

        if callable(valor):
            try:
                valor = valor()
            except Exception:
                continue

        if isinstance(valor, str):
            valor = " ".join(valor.split())

            if valor and len(valor) <= 60 and valor.lower() not in ROTULOS_GENERICOS_BOTAO:
                return valor

    return None


def _identidade_botao(botao, chave):
    if chave:
        return ("chave", chave)

    id_elemento = getattr(botao, "id", None)

    if isinstance(id_elemento, str) and id_elemento:
        return ("elemento", id_elemento)

    return ("objeto", id(botao))


def executar_lotes_seguir(
    obter_botoes,
    clicar,
    progresso,
    total_perfis_lista,
    limite_seguir,
    intervalo_lote_segundos,
    perfil_do_botao=None,
    preparar_clique=None,
    rolar=None,
    esperar=time.sleep,
    pausa_entre_cliques=2.0,
    pausa_apos_rolar=2.0,
    max_rolagens_sem_novos=8,
    imprimir=print,
):
    """Executa os cliques em lotes registrando sucesso/falha de cada botao.

    obter_botoes deve retornar os botoes "Seguir" disponiveis na varredura atual.
    clicar recebe um botao e pode lancar excecao; a falha e registrada sem interromper os demais.
    perfil_do_botao e opcional e serve para identificar o perfil de cada botao.
    """

    total_seguidos = 0
    total_falhas = 0
    total_lote = 0
    total_processados = 0
    rolagens_sem_novos = 0
    identidades_vistas = set()
    falhas_execucao = []
    resultados = []

    imprimir(f"Iniciando cliques em Seguir na lista de {progresso.tipo_lista}.")
    imprimir(f"Limite por lote: {limite_seguir}. Intervalo entre lotes: {intervalo_lote_segundos} segundos.")

    while total_processados < total_perfis_lista and rolagens_sem_novos < max_rolagens_sem_novos:
        try:
            botoes = list(obter_botoes() or [])
        except Exception as erro:
            rolagens_sem_novos += 1
            imprimir(f"Nao foi possivel ler os botoes Seguir desta varredura ({resumir_erro(erro)}). Tentando novamente.")

            if rolar is not None:
                rolar()

            esperar(pausa_apos_rolar)
            continue

        botoes_novos = []
        identidades_da_varredura = set()

        for botao in botoes:
            perfil = None

            if perfil_do_botao is not None:
                try:
                    perfil = perfil_do_botao(botao)
                except Exception:
                    perfil = None

            chave = _chave_do_botao(botao, perfil)
            identidade = _identidade_botao(botao, chave)

            if identidade in identidades_vistas or identidade in identidades_da_varredura:
                continue

            identidades_da_varredura.add(identidade)
            botoes_novos.append((botao, chave, identidade))

        if not botoes_novos:
            rolagens_sem_novos += 1

            if rolar is not None:
                rolar()

            esperar(pausa_apos_rolar)
            continue

        rolagens_sem_novos = 0
        recomecar_apos_lote = False

        for botao, chave, identidade in botoes_novos:
            if total_processados >= total_perfis_lista:
                break

            identidades_vistas.add(identidade)
            indice = progresso.obter_indice(chave)

            if chave and progresso.ja_seguido(chave):
                total_processados += 1
                resultados.append({
                    "indice": indice,
                    "perfil": chave,
                    "status": "ignorado",
                    "motivo": "ja seguido em execucao anterior",
                })
                imprimir(f"Perfil {indice}/{total_perfis_lista} ({chave}) ja foi seguido em execucao anterior. Pulando.")
                continue

            # Sem identidade estavel (botao sem perfil localizavel) o resultado nao entra
            # no progresso como perfil seguido; apenas a posicao processada e avancada.
            rotulo = chave or ("botao_%d" % indice)

            try:
                if preparar_clique is not None:
                    preparar_clique(botao)
                clicar(botao)
            except Exception as erro:
                motivo = resumir_erro(erro)
                progresso.registrar_falha(chave, motivo, indice)
                total_falhas += 1
                total_lote += 1
                total_processados += 1
                falhas_execucao.append({"indice": indice, "perfil": rotulo, "motivo": motivo})
                resultados.append({
                    "indice": indice,
                    "perfil": rotulo,
                    "status": "falha",
                    "motivo": motivo,
                })
                imprimir(f"Falha ao seguir perfil {indice}/{total_perfis_lista} ({rotulo}): {motivo}")
            else:
                progresso.registrar_sucesso(chave, indice)
                total_seguidos += 1
                total_lote += 1
                total_processados += 1
                resultados.append({
                    "indice": indice,
                    "perfil": rotulo,
                    "status": "sucesso",
                    "motivo": "",
                })
                imprimir(f"Seguindo perfil {total_seguidos}/{total_perfis_lista} (lote {total_lote}/{limite_seguir})")

            esperar(pausa_entre_cliques)

            if total_lote >= limite_seguir and total_processados < total_perfis_lista:
                imprimir(f"Limite do lote atingido. Aguardando {intervalo_lote_segundos} segundos para continuar de onde parou.")
                esperar(intervalo_lote_segundos)
                total_lote = 0
                imprimir("Retomando cliques no modal.")

                if rolar is not None:
                    rolar()

                esperar(pausa_apos_rolar)
                recomecar_apos_lote = True
                break

        if not recomecar_apos_lote and total_processados < total_perfis_lista:
            if rolar is not None:
                rolar()

            esperar(pausa_apos_rolar)

    imprimir(f"Finalizado. Total seguido nesta execucao: {total_seguidos}")
    imprimir(f"Resumo desta execucao: {total_seguidos} sucesso(s) e {total_falhas} falha(s).")
    imprimir(f"Total ja seguido registrado no progresso: {len(progresso.seguidos)}.")

    if falhas_execucao:
        imprimir("Falhas desta execucao:")

        for falha in sorted(falhas_execucao, key=lambda item: item["indice"]):
            imprimir(
                f"- Perfil {falha['indice']}/{total_perfis_lista} ({falha['perfil']}): {falha['motivo']}"
            )

    falhas_pendentes = progresso.listar_falhas_pendentes()

    if falhas_pendentes:
        imprimir(
            f"Falhas pendentes no arquivo de progresso ({len(falhas_pendentes)}). "
            "Elas serao tentadas novamente na proxima execucao:"
        )

        for falha in falhas_pendentes:
            imprimir(
                f"- Perfil {falha['indice']}/{total_perfis_lista} ({falha['perfil']}): {falha['motivo']}"
            )

    return {
        "sucessos": total_seguidos,
        "falhas": total_falhas,
        "processados": total_processados,
        "resultados": resultados,
    }


def esta_logado(driver):
    return driver.get_cookie("sessionid") is not None


def esta_em_tela_login(driver):
    url_atual = driver.current_url.lower()

    if "/accounts/login" in url_atual:
        return True

    return elemento_existe(driver, "campo_usuario") and elemento_existe(driver, "campo_senha")


def esta_em_verificacao_manual(driver):
    url_atual = driver.current_url.lower()
    return (
        elemento_existe(driver, "tela_verificacao_manual") or
        "/auth_platform/" in url_atual
    )


def esta_em_fluxo_de_verificacao(driver):
    url_atual = driver.current_url.lower()
    return (
        "/challenge/" in url_atual or
        "/checkpoint/" in url_atual or
        "/two_factor" in url_atual or
        "/accounts/login/two_factor" in url_atual
    )


def esperar_login_concluido(driver, timeout=90):
    tempo_esperar_cookie = 12

    try:
        WebDriverWait(driver, timeout).until(
            lambda navegador: (
                esta_logado(navegador) or
                esta_em_verificacao_manual(navegador) or
                esta_em_fluxo_de_verificacao(navegador) or
                not esta_em_tela_login(navegador)
            )
        )
    except TimeoutException as erro:
        salvar_diagnostico(driver, "login_nao_concluido")
        raise TimeoutException("Login nao concluiu dentro do tempo esperado. Verifique usuario, senha ou bloqueios do Instagram.") from erro

    if esta_logado(driver):
        print("Login concluido.")
        return

    if esta_em_verificacao_manual(driver):
        print("\nO Instagram pediu verificacao manual por codigo.")
        print("Digite o codigo no navegador e clique em Continuar.")
        print("O bot vai aguardar o login concluir antes de continuar.")

        try:
            WebDriverWait(driver, 300).until(lambda navegador: esta_logado(navegador))
        except TimeoutException as erro:
            salvar_diagnostico(driver, "verificacao_manual_nao_concluida")
            raise TimeoutException("A verificacao manual nao foi concluida em ate 5 minutos.") from erro

        print("Login concluido apos verificacao manual.")
        return

    if not esta_em_tela_login(driver):
        print("Tela de login encerrada. Confirmando autenticacao...")
        deadline = time.time() + tempo_esperar_cookie

        while time.time() < deadline:
            if esta_logado(driver):
                print("Login concluido.")
                return
            time.sleep(1)

        print("Login parece ter saindo da tela de login sem o cookie de sessao detectavel.")
        return

    salvar_diagnostico(driver, "login_exige_verificacao")
    raise RuntimeError("O Instagram abriu uma etapa de verificacao/checkpoint. Conclua essa etapa ou habilite o codigo 2FA no script antes de continuar.")


def criar_opcoes_chrome():
    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")
    # chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])
    return chrome_options


def criar_driver_chrome():
    chrome_options = criar_opcoes_chrome()

    try:
        service = ChromeService(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=chrome_options)
    except WebDriverException as erro_webdriver_manager:
        print("Falha ao iniciar Chrome com chromedriver do webdriver-manager.")
        print("Tentando fallback com Selenium Manager...")

        try:
            return webdriver.Chrome(options=chrome_options)
        except WebDriverException as erro_selenium_manager:
            print("\nNao foi possivel iniciar o Chrome automaticamente.")
            print("Tente remover o cache do chromedriver e rodar novamente:")
            print("rm -rf ~/.wdm/drivers/chromedriver")
            print("\nErro webdriver-manager:")
            print(erro_webdriver_manager)
            print("\nErro Selenium Manager:")
            print(erro_selenium_manager)
            raise


class InstagramBot:
    def __init__(
        self,
        usuario,
        senha,
        usa_codigo_verificacao,
        codigo_verificacao,
        perfil_alvo,
        tipo_lista,
        limite_seguir,
        intervalo_lote_segundos,
        arquivo_progresso=ARQUIVO_PROGRESSO_PADRAO,
    ):
        self.usuario = usuario
        self.senha = senha
        self.usa_codigo_verificacao = usa_codigo_verificacao
        self.codigo_verificacao = codigo_verificacao
        self.lista_seguidores = []
        self.perfil = perfil_alvo.strip().lstrip('@') # perfil do Instagram onde vai seguir os perfis seguidos
        self.tipo_lista = tipo_lista
        self.limite_seguir = limite_seguir
        self.intervalo_lote_segundos = intervalo_lote_segundos
        self.arquivo_progresso = arquivo_progresso
        self.driver = criar_driver_chrome()

    def login(self):
        driver = self.driver
        driver.get("https://www.instagram.com")
        #input[@name=email] = login inspecionado na pagina instagram
        #input[@name=pass] = senha inspecionado na pagina instagram
        #input[@name=verificationCode] = verificaçao de codigo em autenticaçao de dois fatores
        campo_usuario = esperar_elemento(driver, "campo_usuario", timeout=30)
        campo_usuario.click()
        campo_usuario.clear()
        campo_usuario.send_keys(self.usuario)
        campo_senha = esperar_elemento(driver, "campo_senha", timeout=20)
        campo_senha.clear()
        campo_senha.send_keys(self.senha)
        botao_entrar = esperar_elemento(driver, "botao_entrar", timeout=20, clicavel=True)
        botao_entrar.click()

        if self.usa_codigo_verificacao:
            campo_verificacao_codigo = esperar_elemento(driver, "campo_verificacao_codigo", timeout=30)
            campo_verificacao_codigo.click()
            campo_verificacao_codigo.clear()
            campo_verificacao_codigo.send_keys(self.codigo_verificacao)
            campo_verificacao_codigo.send_keys(Keys.RETURN)

        esperar_login_concluido(driver)
        self.seguir_perfis_da_lista(self.perfil)
  
    def seguir_perfis_da_lista(self, perfil_instagram):
        driver = self.driver
        driver.get("https://www.instagram.com/" + perfil_instagram + "/")
        nome_link_lista = "link_seguidores" if self.tipo_lista == "seguidores" else "link_seguindo"

        try:
            link_lista = esperar_elemento(driver, nome_link_lista, timeout=30, clicavel=True)
        except NoSuchElementException:
            salvar_diagnostico(driver, f"link_{self.tipo_lista}_nao_encontrado")
            raise

        total_perfis_lista = link_lista.text
        print(total_perfis_lista)
        total_perfis_lista = extrair_total_perfis_lista(total_perfis_lista)
        link_lista.click()

        self.seguir_perfis_no_modal(total_perfis_lista)

    def seguir_perfis_no_modal(self, total_perfis_lista):
        driver = self.driver
        modal = esperar_elemento(driver, "modal_lista_perfis", timeout=30)
        esperar_elemento_dentro(modal, "botao_seguir_modal", timeout=30)

        progresso = ProgressoSeguir.abrir(
            self.arquivo_progresso,
            self.perfil,
            self.tipo_lista,
            total_perfis_lista,
        )

        def obter_botoes():
            return [
                botao for botao in encontrar_elementos_dentro(modal, "botao_seguir_modal")
                if botao.is_displayed() and botao.is_enabled()
            ]

        def preparar_clique(botao_seguir):
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", botao_seguir)
            time.sleep(0.5)

        def rolar():
            self.rolar_modal_seguindo(modal)

        resultado = executar_lotes_seguir(
            obter_botoes=obter_botoes,
            clicar=lambda botao_seguir: botao_seguir.click(),
            progresso=progresso,
            total_perfis_lista=total_perfis_lista,
            limite_seguir=self.limite_seguir,
            intervalo_lote_segundos=self.intervalo_lote_segundos,
            perfil_do_botao=obter_perfil_do_botao,
            preparar_clique=preparar_clique,
            rolar=rolar,
        )

        print(f"Progresso salvo em: {self.arquivo_progresso}")
        return resultado

    def rolar_modal_seguindo(self, modal):
        self.driver.execute_script(
            """
            const modal = arguments[0];
            const scrollables = Array.from(modal.querySelectorAll('div')).filter((element) => {
                const style = window.getComputedStyle(element);
                return element.scrollHeight > element.clientHeight && ['auto', 'scroll'].includes(style.overflowY);
            });
            const target = scrollables[scrollables.length - 1] || modal;
            target.scrollTop = target.scrollHeight;
            """,
            modal,
        )

def campo_vazio(valor):
    return not str(valor).strip()


def validar_formulario(valores_formulario):
    campos_pendentes = []

    if campo_vazio(valores_formulario['usuario']):
        campos_pendentes.append('Usuario')

    if campo_vazio(valores_formulario['senha']):
        campos_pendentes.append('Senha')

    if campo_vazio(valores_formulario['perfil_alvo']):
        campos_pendentes.append('Perfil alvo')

    if valores_formulario['tipo_lista'] not in ("seguidores", "seguindo"):
        campos_pendentes.append('Tipo lista deve ser "seguidores" ou "seguindo"')

    if valores_formulario['usa_codigo_verificacao'] and campo_vazio(valores_formulario['codigo_verificacao']):
        campos_pendentes.append('Codigo 2FA')

    if int(valores_formulario['limite_seguir']) <= 0:
        campos_pendentes.append('Limite seguir maior que zero')

    if int(valores_formulario['intervalo_lote_segundos']) < 0:
        campos_pendentes.append('Intervalo do lote maior ou igual a zero')

    return campos_pendentes


def criar_parser_argumentos():
    parser = argparse.ArgumentParser(description='Bot para seguir perfis da lista de seguidores ou seguindo de uma conta do Instagram.')
    parser.add_argument('--terminal', action='store_true', help='Roda sem janela, solicitando dados pelo terminal.')
    parser.add_argument('--usuario', help='Usuario, email ou telefone da conta do Instagram.')
    parser.add_argument('--senha', help='Senha da conta do Instagram.')
    parser.add_argument('--perfil-alvo', dest='perfil_alvo', help='Perfil de onde o bot vai abrir a lista configurada.')
    parser.add_argument('--tipo-lista', dest='tipo_lista', choices=['seguidores', 'seguindo'], default='seguindo', help='Lista que sera aberta no perfil alvo.')
    parser.add_argument('--usa-codigo-verificacao', action='store_true', help='Indica que a conta usa codigo 2FA.')
    parser.add_argument('--codigo-verificacao', help='Codigo de verificacao em 2 fatores.')
    parser.add_argument('--limite-seguir', dest='limite_seguir', type=int, default=50, help='Quantidade maxima de perfis para seguir por lote.')
    parser.add_argument('--intervalo-lote-segundos', dest='intervalo_lote_segundos', type=int, default=3600, help='Pausa entre lotes de cliques em Seguir.')
    parser.add_argument('--arquivo-progresso', dest='arquivo_progresso', default=ARQUIVO_PROGRESSO_PADRAO, help='Arquivo JSON onde o progresso do seguimento e salvo e retomado.')
    return parser


def obter_modo_execucao(argumentos):
    if argumentos.terminal:
        return "terminal"

    return MODO_EXECUCAO


def validar_modo_execucao(modo_execucao):
    modos_validos = ("codigo", "terminal")

    if modo_execucao not in modos_validos:
        print("MODO_EXECUCAO invalido: " + modo_execucao)
        print('Use "codigo" ou "terminal".')
        sys.exit(1)


def pedir_texto_terminal(rotulo, valor_atual=None, oculto=False):
    if valor_atual:
        return valor_atual.strip()

    while True:
        if oculto:
            valor = getpass.getpass(rotulo + ': ')
        else:
            valor = input(rotulo + ': ')

        valor = valor.strip()

        if valor:
            return valor

        print(rotulo + ' e obrigatorio.')


def pedir_confirmacao_terminal(rotulo):
    while True:
        resposta = input(rotulo + ' [s/N]: ').strip().lower()

        if resposta == '':
            return False

        if resposta in ('s', 'sim', 'y', 'yes'):
            return True

        if resposta in ('n', 'nao', 'não', 'no'):
            return False

        print('Responda com s ou n.')


def coletar_dados_terminal(argumentos):
    usuario = pedir_texto_terminal('Usuario', argumentos.usuario)
    senha = pedir_texto_terminal('Senha', argumentos.senha, oculto=argumentos.senha is None)
    perfil_alvo = pedir_texto_terminal('Perfil alvo', argumentos.perfil_alvo)

    usa_codigo_verificacao = argumentos.usa_codigo_verificacao

    if not usa_codigo_verificacao and argumentos.codigo_verificacao:
        usa_codigo_verificacao = True

    if not usa_codigo_verificacao:
        usa_codigo_verificacao = pedir_confirmacao_terminal('Conta tem verificacao em 2 fatores?')

    codigo_verificacao = ''

    if usa_codigo_verificacao:
        codigo_verificacao = pedir_texto_terminal('Codigo 2FA', argumentos.codigo_verificacao)

    return {
        'usuario': usuario,
        'senha': senha,
        'usa_codigo_verificacao': usa_codigo_verificacao,
        'codigo_verificacao': codigo_verificacao,
        'perfil_alvo': perfil_alvo,
        'tipo_lista': argumentos.tipo_lista,
        'limite_seguir': argumentos.limite_seguir,
        'intervalo_lote_segundos': argumentos.intervalo_lote_segundos,
        'arquivo_progresso': argumentos.arquivo_progresso,
    }


def coletar_dados_codigo():
    arquivo_progresso = DADOS_BOT_CODIGO.get('arquivo_progresso', '').strip() or ARQUIVO_PROGRESSO_PADRAO

    dados_bot = {
        'usuario': DADOS_BOT_CODIGO['usuario'].strip(),
        'senha': DADOS_BOT_CODIGO['senha'].strip(),
        'usa_codigo_verificacao': DADOS_BOT_CODIGO['usa_codigo_verificacao'],
        'codigo_verificacao': DADOS_BOT_CODIGO['codigo_verificacao'].strip(),
        'perfil_alvo': DADOS_BOT_CODIGO['perfil_alvo'].strip(),
        'tipo_lista': DADOS_BOT_CODIGO['tipo_lista'].strip().lower(),
        'limite_seguir': int(DADOS_BOT_CODIGO['limite_seguir']),
        'intervalo_lote_segundos': int(DADOS_BOT_CODIGO['intervalo_lote_segundos']),
        'arquivo_progresso': arquivo_progresso,
    }
    campos_pendentes = validar_formulario(dados_bot)

    if campos_pendentes:
        print("Preencha os campos em DADOS_BOT_CODIGO antes de rodar:")
        for campo in campos_pendentes:
            print("- " + campo)
        sys.exit(1)

    return dados_bot


def iniciar_bot(dados_bot):
    bot_instagram = InstagramBot(
        dados_bot['usuario'],
        dados_bot['senha'],
        dados_bot['usa_codigo_verificacao'],
        dados_bot['codigo_verificacao'],
        dados_bot['perfil_alvo'],
        dados_bot['tipo_lista'],
        dados_bot['limite_seguir'],
        dados_bot['intervalo_lote_segundos'],
        dados_bot.get('arquivo_progresso') or ARQUIVO_PROGRESSO_PADRAO,
    )
    bot_instagram.login()


def executar_modo_terminal(argumentos):
    dados_bot = coletar_dados_terminal(argumentos)
    print('Iniciando automacao...')
    iniciar_bot(dados_bot)


def executar_modo_codigo():
    dados_bot = coletar_dados_codigo()
    print('Iniciando automacao com dados definidos no codigo...')
    print('Arquivo de progresso: ' + dados_bot['arquivo_progresso'])
    iniciar_bot(dados_bot)


def main():
    argumentos = criar_parser_argumentos().parse_args()
    modo_execucao = obter_modo_execucao(argumentos)
    validar_modo_execucao(modo_execucao)

    if not verificar_pacotes_instalados():
        sys.exit(1)

    carregar_dependencias_externas()

    if modo_execucao == "codigo":
        executar_modo_codigo()
    else:
        executar_modo_terminal(argumentos)


if __name__ == '__main__':
    main()
