
<h3 align="center">Bots para Instagram</h3>

## Sobre

Colecao de scripts para automacao no Instagram com Python e Selenium.

## Scripts

| Arquivo | Objetivo |
| --- | --- |
| `instagrambot.py` | Comenta em posts a partir de hashtag. |
| `instagram_curtir_fotos.py` | Curte fotos de uma hashtag. |
| `instagrambot_baixa_seguidores.py` | Coleta seguidores e comenta em sorteio. |
| `instagrambot_segue_following.py` | Segue perfis da lista de seguindo de uma conta. |
| `instagrambot_seguidores_lista_excel_mais_que_1_comentario.py` | Comenta marcando usuarios de uma planilha. |

## Pre-requisitos

- Python 3.9+
- Firefox instalado

## Instalacao

```bash
pip install -r requirements.txt
```

## Execucao

1. Entre na pasta: `cd instagrambot`
2. Execute o script desejado, por exemplo:

```bash
python3 instagrambot.py
```

## Observacoes

- Os scripts usam Selenium 4 com `webdriver-manager` para baixar e gerenciar o `geckodriver` automaticamente.
- A interface e os seletores do Instagram mudam com frequencia; pode ser necessario ajustar XPaths/CSS ao longo do tempo.

## Seguir perfis em lotes com progresso (`instagrambot_segue_following.py`)

O fluxo de seguir perfis clica em lotes de `--limite-seguir` com pausa de
`--intervalo-lote-segundos` entre eles. Cada botao clicado e registrado:

- sucesso: aparece na lista `seguidos` do arquivo de progresso;
- falha: a excecao e resumida em uma linha, o botao e registrado na lista `falhas` e
  os demais botoes continuam sendo processados.

Ao final de cada execucao o script mostra o total seguido no formato antigo
(`Seguindo perfil X/total`) e um resumo com o numero de sucessos e falhas. Em caso
de falha, cada uma e listada com o indice e o motivo.

### Arquivo de progresso e retomada

O progresso fica em JSON (por padrao `instagrambot/progresso_seguindo.json`, ignorado
pelo Git) e e gravado apos cada tentativa. O caminho pode ser trocado por
`--arquivo-progresso` ou pela chave `arquivo_progresso` em `DADOS_BOT_CODIGO`.

```bash
python3 instagrambot_segue_following.py --terminal --perfil-alvo conta_alvo \
  --limite-seguir 50 --intervalo-lote-segundos 3600 \
  --arquivo-progresso ./progresso_conta_alvo.json
```

Ao rodar de novo com o mesmo arquivo, os perfis ja seguidos (lista `seguidos`) sao
pulados e apenas os restantes sao processados; perfis que falharam na execucao
anterior sao tentados novamente. Se o arquivo nao existir, estiver corrompido ou
pertencer a outro perfil/tipo de lista, o script avisa e comeca do zero.

### Testes sem o Instagram

`instagrambot/tests/test_segue_following.py` substitui o Selenium por uma lista falsa
de botoes e um registrador de espera, cobrindo falha no meio do lote, pausa entre
lotes e retomada apos interrupcao:

```bash
pip install pytest   # dependencia apenas para os testes
python3 -m pytest instagrambot/tests -q
```
