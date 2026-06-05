# Scraper Leilões Caixa → OLANCE

Worker independente que, **1×/dia**, importa os imóveis de leilão da Caixa Econômica e enriquece os detalhes que **não existem no CSV** (leiloeiro, datas e preços do 1º/2º leilão, matrícula, comarca, inscrição imobiliária).

Roda separado do OLANCE porque a Caixa usa **Radware Bot Manager** e o IP do servidor OLANCE está numa blacklist. Usa **[Camoufox](https://github.com/daijro/camoufox)** (Firefox stealth) para passar pela proteção.

## Como funciona

```
1. Baixa o CSV geral da Caixa (Camoufox)         → POST /api/admin/leiloes-seed-file
2. Pega imóveis sem detalhes (novos primeiro)    → GET  /api/admin/leiloes/pending-enrichment
3. Raspa a página de detalhe de cada um          → (Camoufox, em lotes)
4. Envia os dados enriquecidos                   → POST /api/admin/leiloes/enrich-batch
```

O OLANCE é a fonte da verdade. Imóveis bloqueados pelo anti-bot ficam sem `detalhes_enriched_at` e são **retentados no dia seguinte** automaticamente. Como são milhares de imóveis, o enriquecimento é feito em **lotes (`BATCH_SIZE`, default 300/dia)**, priorizando os recém-importados — o acervo é coberto ao longo de vários dias.

## Configuração

Copie `.env.example` para `.env` e preencha:

| Variável | Descrição |
|---|---|
| `OLANCE_URL` | URL base do OLANCE (ex: `https://olance.app`) |
| `CRON_SECRET` | Mesmo segredo do `CRON_SECRET` no OLANCE |
| `BATCH_SIZE` | Imóveis enriquecidos por execução (default 300) |
| `HEADLESS` | `true` em servidor/CI; `false` para ver o browser ao depurar |
| `DETAIL_MIN_DELAY` / `DETAIL_MAX_DELAY` | Intervalo (s) entre requisições de detalhe |

## Rodar localmente

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
python -m camoufox fetch          # baixa o Firefox stealth (uma vez)
python -m scraper.main
```

## Deploy

### Opção 1 — GitHub Actions (teste inicial)
`.github/workflows/scrape.yml` roda diariamente às 06:00 UTC. Configure os **secrets** do repositório:
- `OLANCE_URL`
- `CRON_SECRET`

Dispare manualmente em **Actions → Scraper Leilões Caixa → Run workflow** para testar.

> ⚠️ IPs do GitHub Actions (Azure) podem estar bloqueados pela Caixa. Se o log acusar
> "possível bloqueio anti-bot", migre a execução para um IP não bloqueado (abaixo).

### Opção 2 — VPS (OVH / SoYouStart) ou máquina local
O código é agnóstico de ambiente. Em qualquer host com Python:

```bash
pip install -r requirements.txt && python -m camoufox fetch
python -m scraper.main
```

Agende com **cron** (Linux) ou **Agendador de Tarefas** (Windows). IPs de datacenter
(OVH) também podem ser bloqueados pelo Radware — nesse caso use **IP residencial**
(máquina local) ou um **proxy residencial**.

## Estrutura

```
scraper/
  config.py          # env vars
  caixa_csv.py       # download do CSV (Camoufox)
  caixa_detail.py    # scraping + parsing dos detalhes (regex)
  olance_client.py   # cliente HTTP do OLANCE
  main.py            # orquestração
.github/workflows/scrape.yml
```
