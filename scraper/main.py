"""Orquestra o scraper diário:

1. Baixa o CSV geral da Caixa (Camoufox) e envia ao OLANCE (importação).
2. Pega do OLANCE os imóveis ainda sem detalhes (novos primeiro).
3. Raspa a página de detalhe de cada um e envia os dados enriquecidos em lotes.
"""
from __future__ import annotations

import logging
import random
import sys
import time

from camoufox.sync_api import Camoufox

from .caixa_csv import download_csv
from .caixa_detail import scrape_detail
from .config import load_config
from .olance_client import OlanceClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraper")

# A cada quantos imóveis raspados enviar um lote ao OLANCE (progresso incremental)
FLUSH_EVERY = 50
# Página da Caixa visitada antes do loop para estabelecer a sessão/cookie do Radware
WARMUP_URL = "https://venda-imoveis.caixa.gov.br/sistema/busca-imovel.asp"


def import_csv(client: OlanceClient, headless: bool) -> None:
    csv_bytes = download_csv(headless=headless)
    log.info("📤 Enviando CSV ao OLANCE...")
    imported = client.seed_csv(csv_bytes)
    log.info("✅ %d imóveis importados", imported)


def enrich_details(client: OlanceClient, cfg) -> None:
    numeros = client.get_pending(cfg.batch_size)
    if not numeros:
        log.info("Nada para enriquecer — todos os imóveis já têm detalhes.")
        return

    log.info("🔎 Enriquecendo %d imóveis (raspando detalhes)...", len(numeros))
    sent_total = 0
    scraped_total = 0
    buffer: list[dict] = []

    def flush() -> None:
        nonlocal sent_total, buffer
        if not buffer:
            return
        updated = client.post_enrichment(buffer)
        sent_total += updated
        log.info("   ↳ lote enviado: %d atualizados (acumulado %d)", updated, sent_total)
        buffer = []

    with Camoufox(headless=cfg.headless, humanize=True, locale="pt-BR") as browser:
        page = browser.new_page()

        # Aquece a sessão (passa o desafio JS uma vez)
        try:
            page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3_000)
        except Exception as exc:  # noqa: BLE001
            log.warning("Aviso no warmup: %s", exc)

        for i, numero in enumerate(numeros, start=1):
            data = scrape_detail(page, numero)
            if data is not None:
                buffer.append(data)
                scraped_total += 1
            else:
                # Bloqueado/erro: não envia — fica NULL no banco e é retentado amanhã
                pass

            if len(buffer) >= FLUSH_EVERY:
                flush()

            if i < len(numeros):
                time.sleep(random.uniform(cfg.detail_min_delay, cfg.detail_max_delay))

            if i % 25 == 0:
                log.info("   progresso: %d/%d raspados=%d", i, len(numeros), scraped_total)

        flush()

    log.info("✅ Enriquecimento concluído: %d raspados, %d salvos no OLANCE", scraped_total, sent_total)


def main() -> int:
    cfg = load_config()
    client = OlanceClient(cfg.olance_url, cfg.cron_secret)

    log.info("=== Scraper Leilões Caixa → OLANCE (%s) ===", cfg.olance_url)

    try:
        import_csv(client, cfg.headless)
    except Exception as exc:  # noqa: BLE001
        log.error("❌ Falha na importação do CSV: %s", exc)
        # Sem CSV não há o que enriquecer de novo; aborta
        return 1

    try:
        enrich_details(client, cfg)
    except Exception as exc:  # noqa: BLE001
        log.error("❌ Falha no enriquecimento: %s", exc)
        return 1

    log.info("🏁 Concluído com sucesso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
