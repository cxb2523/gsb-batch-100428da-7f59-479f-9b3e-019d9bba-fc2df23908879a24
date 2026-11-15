import os
import sys
import time
import sqlite3
import requests
from dotenv import load_dotenv
from datetime import datetime, timedelta

# O console do Windows pode usar GBK e falhar ao imprimir emojis. Reconfigurar
# o stdout evita que um print derrube a execução; o .bat também define UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# --- 1. CONFIGURAÇÕES E CAMINHOS ---
DIRETORIO_ATUAL = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(DIRETORIO_ATUAL, '.env'))

# Os valores abaixo podem ser sobrescritos por variáveis de ambiente, o que
# também permite testar o fluxo com um banco local e um endpoint falso.
CAMINHO_BANCO = os.getenv("GUPY_DB_PATH") or os.path.join(DIRETORIO_ATUAL, 'vagas_gupy.db')

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID_GRUPO")

GUPY_API_URL = os.getenv("GUPY_API_URL", "https://employability-portal.gupy.io/api/v1/jobs")
TELEGRAM_API_BASE = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")

LIMITE_VELHAS = int(os.getenv("GUPY_LIMITE_VELHAS", "20"))
PAGINA_MAXIMA = int(os.getenv("GUPY_PAGINA_MAXIMA", "35"))
INTERVALO_ENVIO = float(os.getenv("INTERVALO_ENVIO", "2"))

# Estados possíveis de uma vaga no banco:
#   pending -> foi gravada, mas o Telegram ainda não confirmou o envio
#   sent    -> o Telegram confirmou o envio (HTTP 200)
STATUS_PENDENTE = "pending"
STATUS_ENVIADO = "sent"

TRADUCAO_MODELO = {
    "on-site": "Presencial",
    "hybrid": "Híbrido",
    "remote": "Remoto"
}

TRADUCAO_TIPO_VAGA = {
    "vacancy_type_effective": "Efetivo",
    "vacancy_type_apprentice": "Jovem Aprendiz",
    "vacancy_type_internship": "Estágio",
    "vacancy_type_temporary": "Temporário",
    "vacancy_type_freelancer": "Freelancer"
}

FILTROS_DE_BUSCA = [
    {"nome": "RIO DE JANEIRO", "params": {'state': 'Rio de Janeiro', 'limit': 10}},
    {"nome": "HOME OFFICE", "params": {'workplaceTypes': 'remote', 'limit': 10}}
]


def agora_iso():
    return datetime.now().isoformat(timespec="seconds")


# --- 2. BANCO DE DADOS ---
def _colunas_da_tabela(cursor):
    cursor.execute("PRAGMA table_info(vagas_enviadas)")
    return {linha[1] for linha in cursor.fetchall()}


def migrar_banco(conn):
    """Cria a tabela nova ou adapta a tabela antiga sem perder registros.

    A tabela antiga possuía apenas (link, data_publicacao, titulo). Nela, toda
    vaga registrada já havia sido processada pelo fluxo anterior, então os
    registros existentes são marcados como 'sent' para nunca serem reenviados.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vagas_enviadas'"
    )
    tabela_existe = cursor.fetchone() is not None

    if not tabela_existe:
        # Banco novo: o padrão é 'pending', pois nada foi confirmado ainda.
        # (Na migração da tabela antiga o padrão será 'sent', conforme abaixo.)
        cursor.execute('''
            CREATE TABLE vagas_enviadas (
                link TEXT PRIMARY KEY,
                data_publicacao TEXT,
                titulo TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                mensagem TEXT,
                atualizado_em TEXT
            )
        ''')
    else:
        colunas = _colunas_da_tabela(cursor)

        # ALTER TABLE ... ADD COLUMN preserva todas as linhas já existentes.
        # O DEFAULT 'sent' garante que registros legados não sejam reenviados.
        if 'status' not in colunas:
            cursor.execute(
                "ALTER TABLE vagas_enviadas ADD COLUMN status TEXT NOT NULL DEFAULT 'sent'"
            )
        if 'mensagem' not in colunas:
            cursor.execute("ALTER TABLE vagas_enviadas ADD COLUMN mensagem TEXT")
        if 'atualizado_em' not in colunas:
            cursor.execute("ALTER TABLE vagas_enviadas ADD COLUMN atualizado_em TEXT")

        # Normaliza eventuais status vazios/nulos de versões intermediárias.
        cursor.execute(
            "UPDATE vagas_enviadas SET status = ? "
            "WHERE status IS NULL OR TRIM(status) = ''",
            (STATUS_ENVIADO,)
        )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_vagas_enviadas_status "
        "ON vagas_enviadas(status)"
    )
    conn.commit()


def iniciar_banco(caminho_banco=None):
    conn = sqlite3.connect(caminho_banco or CAMINHO_BANCO)
    migrar_banco(conn)
    return conn, conn.cursor()


def marcar_como_enviada(conn, cursor, link):
    cursor.execute(
        "UPDATE vagas_enviadas SET status = ?, atualizado_em = ? WHERE link = ?",
        (STATUS_ENVIADO, agora_iso(), link)
    )
    conn.commit()


# --- 3. TELEGRAM ---
def montar_mensagem(nome_filtro, titulo, empresa, local, modelo, tipo, pcd,
                    data_f, hora_f, link_vaga):
    titulo_mensagem = f"🎯 <b>VAGA GUPY - {nome_filtro}!</b>"
    return f"{titulo_mensagem}\n\n" \
           f"💼 <b>Vaga:</b> {titulo}\n" \
           f"🏢 <b>Empresa:</b> {empresa}\n" \
           f"📍 <b>Local:</b> {local}\n" \
           f"💻 <b>Modelo:</b> {modelo}\n" \
           f"📄 <b>Tipo:</b> {tipo}\n" \
           f"♿ <b>PCD:</b> {pcd}\n" \
           f"📅 <b>Data:</b> {data_f} às {hora_f}\n\n" \
           f"🔗 <a href='{link_vaga}'>Clique aqui para aplicar na Gupy</a>"


def montar_mensagem_fallback(titulo, link_vaga):
    """Mensagem mínima para registros pendentes que não guardaram o texto."""
    return f"🎯 <b>VAGA GUPY!</b>\n\n" \
           f"💼 <b>Vaga:</b> {titulo or 'Título Indisponível'}\n\n" \
           f"🔗 <a href='{link_vaga}'>Clique aqui para aplicar na Gupy</a>"


def enviar_para_telegram(mensagem):
    """Retorna (sucesso, detalhe_do_erro).

    Somente HTTP 200 conta como sucesso. Qualquer exceção de rede ou status
    diferente de 200 mantém a vaga pendente para a próxima execução.
    """
    url_tg = f"{TELEGRAM_API_BASE}/bot{TOKEN}/sendMessage"
    payload_tg = {
        "chat_id": CHAT_ID,
        "text": mensagem,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        resposta = requests.post(url_tg, json=payload_tg, timeout=10)
    except Exception as e:
        return False, f"exceção de rede: {e}"

    if resposta.status_code != 200:
        corpo = getattr(resposta, "text", "") or ""
        return False, f"HTTP {resposta.status_code}: {corpo[:200]}"

    # A API real devolve {"ok": true, ...}. Se o campo existir e for False,
    # tratamos como falha mesmo com HTTP 200.
    try:
        corpo_json = resposta.json()
    except Exception:
        corpo_json = None
    if isinstance(corpo_json, dict) and corpo_json.get("ok") is False:
        return False, f"Telegram recusou: {str(corpo_json)[:200]}"

    return True, None


def _corpo_para_pendente(titulo, link, mensagem):
    return mensagem or montar_mensagem_fallback(titulo, link)


def reenviar_pendentes(conn, cursor):
    """Tenta reenviar todas as vagas pendentes antes de buscar novas.

    Uma falha individual não interrompe as demais nem a varredura da Gupy.
    """
    cursor.execute(
        "SELECT link, titulo, mensagem FROM vagas_enviadas "
        "WHERE status = ? ORDER BY rowid",
        (STATUS_PENDENTE,)
    )
    pendentes = cursor.fetchall()

    if not pendentes:
        print("📨 Nenhuma vaga pendente de envio.")
        return 0, 0

    print(f"📨 {len(pendentes)} vaga(s) pendente(s) de envio. Tentando reenviar...")
    enviados = 0
    falhas = 0

    for indice, (link, titulo, mensagem) in enumerate(pendentes):
        texto = _corpo_para_pendente(titulo, link, mensagem)
        ok, erro = enviar_para_telegram(texto)

        if ok:
            marcar_como_enviada(conn, cursor, link)
            enviados += 1
            print(f"✅ Reenviada: {(titulo or link)[:60]}...")
        else:
            falhas += 1
            print(f"❌ Falha ao reenviar {(titulo or link)[:60]}: {erro}")

        if indice < len(pendentes) - 1:
            time.sleep(INTERVALO_ENVIO)

    return enviados, falhas


# --- 4. MOTOR DE BUSCA DA GUPY (RJ + REMOTO) ---
def buscar_vagas_gupy(caminho_banco=None, url_api=None):
    print("🚀 Iniciando varredura detalhada na API da Gupy...")
    conn, cursor = iniciar_banco(caminho_banco)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Origin': 'https://portal.gupy.io'
    }

    url_api = url_api or GUPY_API_URL

    # 1) Primeiro, tenta entregar o que ficou pendente de execuções anteriores.
    #    Um erro inesperado aqui nunca deve impedir a busca de vagas novas.
    try:
        reenviar_pendentes(conn, cursor)
    except Exception as e:
        print(f"⚠️ Erro ao reenviar pendentes (a busca continuará): {e}")

    # Vagas inseridas/tratadas nesta execução. Evita nova inserção e novo envio
    # caso a mesma vaga apareça em mais de uma página ou filtro.
    links_vistos_nesta_execucao = set()

    for filtro in FILTROS_DE_BUSCA:
        print(f"\n🔎 Varrendo vagas para: {filtro['nome']}...")

        vagas_velhas = 0
        limite_velhas = LIMITE_VELHAS
        pagina_maxima = PAGINA_MAXIMA
        tamanho_pagina = int(filtro['params'].get('limit', 10))
        encerrar_filtro = False

        for pagina in range(1, pagina_maxima + 1):
            print(f"   ⏳ Lendo página {pagina} de {pagina_maxima}...")

            offset = (pagina - 1) * tamanho_pagina

            params_atuais = filtro['params'].copy()
            params_atuais['offset'] = offset

            try:
                resposta = requests.get(url_api, headers=headers, params=params_atuais, timeout=15)

                if resposta.status_code != 200:
                    print(f"🛑 Erro de conexão. Código HTTP: {resposta.status_code}")
                    break

                try:
                    dados_json = resposta.json()
                except Exception:
                    print(f"🛑 Fomos bloqueados! O servidor não enviou os dados JSON.")
                    break

                lista_vagas = dados_json.get('data', [])
                if not lista_vagas:
                    print("   🔚 Não há mais vagas disponíveis nesta busca.")
                    break  # Fim das páginas no servidor da Gupy

                for vaga in lista_vagas:
                    link_vaga = vaga.get('jobUrl', '')
                    if not link_vaga:
                        continue

                    # A própria API pode repetir uma vaga entre páginas/filtros.
                    # Nesse caso não é uma "vaga velha" e não deve ser reinserida.
                    if link_vaga in links_vistos_nesta_execucao:
                        continue

                    titulo = vaga.get('name', 'Título Indisponível')
                    empresa = vaga.get('careerPageName', 'Empresa não informada')

                    if filtro['nome'] == "HOME OFFICE":
                        local = "Qualquer lugar (Remoto)"
                    else:
                        local = f"{vaga.get('city', 'RJ')} - {vaga.get('state', 'RJ')}"

                    modelo = TRADUCAO_MODELO.get(vaga.get('workplaceType', ''), "Não informado")
                    tipo = TRADUCAO_TIPO_VAGA.get(vaga.get('type', ''), "Outros")
                    pcd = "Sim" if vaga.get('disabilities') else "Não informado"

                    data_iso = vaga.get('publishedDate', '')
                    try:
                        data_limpa = data_iso.split('.')[0]
                        data_utc = datetime.strptime(data_limpa, "%Y-%m-%dT%H:%M:%S")
                        data_brt = data_utc - timedelta(hours=3)
                        data_f = data_brt.strftime("%d/%m/%Y")
                        hora_f = data_brt.strftime("%H:%M")
                    except Exception:
                        data_f, hora_f = "Sem data", "--:--"

                    cursor.execute(
                        'SELECT status FROM vagas_enviadas WHERE link = ?', (link_vaga,)
                    )
                    registro_existente = cursor.fetchone()
                    if registro_existente:
                        status_existente = registro_existente[0]
                        if status_existente == STATUS_PENDENTE:
                            # Pendente é trabalho em aberto, não "vaga antiga":
                            # contá-la como antiga poderia encerrar a paginação
                            # por causa de falhas de envio e bloquear vagas novas.
                            links_vistos_nesta_execucao.add(link_vaga)
                            continue

                        vagas_velhas += 1
                        if vagas_velhas >= limite_velhas:
                            # Precisa encerrar TODAS as páginas deste filtro,
                            # não apenas o laço de vagas da página atual.
                            encerrar_filtro = True
                            break
                        continue

                    vagas_velhas = 0
                    links_vistos_nesta_execucao.add(link_vaga)

                    mensagem = montar_mensagem(
                        filtro['nome'], titulo, empresa, local, modelo,
                        tipo, pcd, data_f, hora_f, link_vaga
                    )

                    # Grava primeiro como pendente. Se o processo cair depois
                    # daqui, a vaga será reenviada na próxima execução.
                    cursor.execute(
                        "INSERT OR IGNORE INTO vagas_enviadas "
                        "(link, data_publicacao, titulo, status, mensagem, atualizado_em) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (link_vaga, data_f, titulo, STATUS_PENDENTE, mensagem, agora_iso())
                    )
                    conn.commit()

                    if cursor.rowcount == 0:
                        # Já inserida por outra execução/processo: não duplica.
                        continue

                    ok, erro = enviar_para_telegram(mensagem)
                    if ok:
                        marcar_como_enviada(conn, cursor, link_vaga)
                        print(f"✅ Enviada ({filtro['nome']}): {titulo[:40]}...")
                    else:
                        # Permanece como 'pending' e será priorizada na próxima
                        # execução; o fluxo atual segue com as demais vagas.
                        print(f"❌ Envio falhou, vaga mantida como pendente "
                              f"({filtro['nome']}): {titulo[:40]}... Detalhe: {erro}")

                    time.sleep(INTERVALO_ENVIO)

                if encerrar_filtro:
                    print(f"   🛑 Muitas vagas antigas ({limite_velhas} consecutivas). "
                          f"Encerrando todas as páginas de '{filtro['nome']}'.")
                    break

            except Exception as e:
                print(f"⚠️ Erro de execução: {e}")
                break

    imprimir_resumo(cursor)
    conn.close()
    print("\n✅ Varredura dupla finalizada!")


def imprimir_resumo(cursor):
    """Mostra os dois estados para que o resultado da execução seja conferível."""
    try:
        cursor.execute(
            "SELECT status, COUNT(*) FROM vagas_enviadas GROUP BY status"
        )
        contagens = dict(cursor.fetchall())
    except Exception:
        return

    enviadas = contagens.get(STATUS_ENVIADO, 0)
    pendentes = contagens.get(STATUS_PENDENTE, 0)
    print(f"\n📊 Resumo do banco: {enviadas} enviada(s), {pendentes} pendente(s).")
    if pendentes:
        print("   ℹ️ As pendentes serão reenviadas na próxima execução.")


if __name__ == '__main__':
    if not TOKEN or not CHAT_ID:
        print("❌ ERRO: Token do Telegram ou Chat ID não encontrados no arquivo .env!")
    else:
        buscar_vagas_gupy()
