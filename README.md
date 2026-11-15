# 🚀 Gupy Job Tracker: Automação de Vagas (RJ & Home Office) no Telegram

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![Gupy API](https://img.shields.io/badge/Source-Gupy%20API-orange)](https://portal.gupy.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 📖 Sobre o Projeto

Buscar vagas de emprego diariamente é um processo exaustivo e repetitivo. Para resolver esse problema, desenvolvi este **rpa** em Python. 

O robô atua como um "radar" silencioso: ele monitora a plataforma Gupy, captura novas oportunidades de trabalho no **Estado do Rio de Janeiro** e vagas **100% Home Office**, e dispara alertas formatados em tempo real diretamente em um canal do Telegram. Assim, as vagas chegam mastigadas até os candidatos.

---

## 🎯 Veja rodando na prática

Quer ver o robô trabalhando ao vivo? Entre no grupo aberto do Telegram que é alimentado 100% por esta automação:
👉 **[Entrar no Grupo: Vagas Gupy | Rio de Janeiro](https://t.me/+20ZgzGruRzJlYjVh)**

---

## 📸 Demonstração do Bot

<img width="913" height="920" alt="image" src="https://github.com/user-attachments/assets/7e2e0cdc-40ec-4336-986e-383fd6c96e6b" />


*Acima: Interface do bot entregando vagas no grupo com formatação amigável.*

---

## 💡 Diferenciais Técnicos (Por que não usei Selenium?)

A maioria das automações de Web Scraping utiliza ferramentas que simulam um navegador aberto (como Selenium), o que consome muita memória RAM e deixa o processo lento. Este projeto resolve o problema usando uma **abordagem API-First**:

1. **Performance Extrema:** O script intercepta a API oculta que a própria Gupy usa para renderizar as vagas. Ele consome pacotes JSON puros, sendo incrivelmente mais rápido e leve.
2. **Tratamento de Dados:** A API retorna datas no fuso horário global (UTC) e termos técnicos em inglês. O código faz o *parse* automático, traduzindo para o horário de Brasília e para o português.
3. **Trava Anti-Spam (SQLite):** Utiliza um banco de dados relacional leve (`vagas_gupy.db`) para memorizar o ID de cada vaga processada. Isso garante que o bot nunca envie a mesma vaga duplicada para o grupo, mesmo que o script seja reiniciado.

---

## ⚙️ Como Adaptar para o SEU Estado / Filtro

O código foi criado para buscar RJ e Home Office, mas você pode mudar para qualquer estado ou adicionar novas buscas.

Abra o arquivo `main.py`, vá até a função `buscar_vagas_gupy()` e modifique a lista `filtros_de_busca`:

```python
# Adicione ou altere os dicionários da lista abaixo
filtros_de_busca = [
    {"nome": "SÃO PAULO", "params": {'state': 'São Paulo', 'limit': 10}},
    {"nome": "HOME OFFICE", "params": {'workplaceTypes': 'remote', 'limit': 10}}
]
```

---

## 🚀 Instalação e Uso Local

### 1. Pré-requisitos
* Python 3.8+ instalado.
* Um Bot criado no Telegram via [@BotFather](https://t.me/botfather).

### 2. Clonando o Repositório
Abra o terminal e rode os comandos:
```bash
git clone [https://github.com/lucasnunestrabalho99-sudo/telegram-vagas-gupy-bot.git](https://github.com/lucasnunestrabalho99-sudo/telegram-vagas-gupy-bot.git)
cd telegram-vagas-gupy-bot
```

### 3. Configurando o Ambiente
Crie um ambiente virtual e instale as bibliotecas necessárias:
```bash
python -m venv venv

# No Windows:
venv\Scripts\activate
# No Linux/Mac:
source venv/bin/activate

pip install -r requirements.txt
```

### 4. Variáveis de Ambiente
Crie um arquivo chamado **exatamente** `.env` na raiz do projeto e insira as credenciais do seu robô e do seu grupo:
```env
TELEGRAM_TOKEN=cole_seu_token_aqui
CHAT_ID_GRUPO=cole_o_id_do_seu_grupo_aqui
```

### 5. Executando o Motor
```bash
python main.py
```

---

## 🔁 Estados de envio e reprocessamento

O banco `vagas_gupy.db` guarda dois estados por vaga na coluna `status`:

* `pending` — a vaga já foi registrada, mas o Telegram ainda não confirmou o envio.
* `sent` — o Telegram respondeu HTTP 200; a vaga está concluída.

O fluxo de cada execução é:

1. Tenta reenviar todas as vagas `pending` (falhas individuais não interrompem as demais nem a busca).
2. Varre a Gupy e grava cada vaga nova como `pending` **antes** de enviar.
3. Só marca como `sent` após confirmação do Telegram; caso contrário permanece `pending` para a próxima execução.
4. Ao encontrar `GUPY_LIMITE_VELHAS` vagas antigas consecutivas, encerra **todas** as páginas daquele filtro.

Bancos criados pela versão anterior (tabela sem `status`) são migrados automaticamente: os registros existentes recebem `status = 'sent'`, portanto nunca são reenviados, e nada é apagado.

Opções de configuração (todas opcionais, via variáveis de ambiente):

| Variável | Padrão | Descrição |
|---|---|---|
| `GUPY_DB_PATH` | `vagas_gupy.db` na pasta do script | Caminho do banco SQLite |
| `GUPY_API_URL` | API pública da Gupy | Endpoint de busca |
| `TELEGRAM_API_BASE` | `https://api.telegram.org` | Base da API do Telegram |
| `GUPY_LIMITE_VELHAS` | `20` | Vagas antigas consecutivas que encerram o filtro |
| `GUPY_PAGINA_MAXIMA` | `35` | Máximo de páginas por filtro |
| `INTERVALO_ENVIO` | `2` | Segundos entre envios |

### Teste de aceitação local

`tests/acceptance_check.py` sobe um servidor HTTP local que simula a Gupy e o Telegram (sem credenciais reais) e valida: falha de envio seguida de reenvio único, migração de banco antigo sem reenvio, encerramento de todas as páginas ao atingir o limite de vagas antigas e ausência de duplicatas:

```bash
python tests/acceptance_check.py
```

---

## 📅 Agendamento Automático (Windows)
Se você utiliza Windows, o repositório inclui um arquivo `rodar_gupy.bat`. Você pode usar o **Agendador de Tarefas do Windows** para rodar esse arquivo de hora em hora. O `.bat` já está configurado para forçar o encoding UTF-8 (evitando crash com emojis) e gerar um arquivo `erro_log.txt` para monitoramento.

---
**Desenvolvido com ☕ por Lucas Nunes** | [LinkedIn](linkedin.com/in/lucas-nunes-da-silva-574604216)
