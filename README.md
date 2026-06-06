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

O OLANCE é a fonte da verdade. Imóveis bloqueados pelo anti-bot ficam sem `detalhes_enriched_at` e são **retentados no próximo run** automaticamente, priorizando os recém-importados.

### Limite da Caixa (importante)

O **CSV** é liberado de qualquer IP, mas as **páginas de detalhe** têm um limite: a Caixa bloqueia o IP após **~150 requisições** seguidas. Por isso:

- `BATCH_SIZE=130` por execução (abaixo do limite, completa limpo)
- **Disjuntor**: o scraper encerra após 8 bloqueios seguidos (não desperdiça tempo martelando um IP já barrado)
- A vazão é dada por **runs/dia × 130**. Cada execução do GitHub Actions usa um **IP diferente**, então rodar várias vezes/dia multiplica a cobertura. O agendamento padrão é **6×/dia** (a cada 4h ≈ 780 detalhes/dia → ~28k em ~36 dias). Imóveis novos têm prioridade, então ficam atualizados em ~1 dia independente do backlog.

## Configuração

Copie `.env.example` para `.env` e preencha:

| Variável | Descrição |
|---|---|
| `OLANCE_URL` | URL base do OLANCE (ex: `https://olance.app`) |
| `CRON_SECRET` | Mesmo segredo do `CRON_SECRET` no OLANCE |
| `BATCH_SIZE` | Imóveis enriquecidos por execução (default 130 — abaixo do limite da Caixa) |
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

### GitHub Actions (recomendado — repo público = minutos ilimitados)
`.github/workflows/scrape.yml` roda **6×/dia** (a cada 4h, no minuto 17). Cada execução
usa um IP diferente do pool do GitHub, o que contorna o limite de ~150/IP da Caixa.

Configure os **secrets** do repositório (**Settings → Secrets and variables → Actions**):
- `OLANCE_URL` (ex: `https://olance.app`)
- `CRON_SECRET` (mesmo segredo do OLANCE)

Dispare manualmente em **Actions → Scraper Leilões Caixa → Run workflow** para testar.

> 💡 Em **repo público** os minutos do Actions são **ilimitados e grátis**. Os secrets
> continuam criptografados e **não** são expostos a workflows de PRs de forks. Mantenha
> a frequência em ~3-6 runs/dia por educação com a Caixa (evita endurecer a proteção).

### Alternativa — VPS (OVH / SoYouStart) ou máquina local
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
