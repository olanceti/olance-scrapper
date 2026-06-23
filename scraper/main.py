"""Orquestra o scraper diário:

1. Baixa o CSV geral da Caixa (Camoufox) e envia ao OLANCE (importação).
2. Pega do OLANCE os imóveis ainda sem detalhes (novos primeiro).
3. Raspa a página de detalhe de cada um e envia os dados enriquecidos em lotes.
"""
from __future__ import annotations

import logging
import os
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
# Após N bloqueios seguidos, o IP foi limitado pela Caixa — encerra e tenta no próximo run
CONSECUTIVE_BLOCK_LIMIT = 8
# Página da Caixa visitada antes do loop para estabelecer a sessão/cookie do Radware
WARMUP_URL = "https://venda-imoveis.caixa.gov.br/sistema/busca-imovel.asp"


def import_csv(client: OlanceClient, headless: bool) -> None:
    csv_bytes = download_csv(headless=headless)
    log.info("📤 Enviando CSV ao OLANCE...")
    imported = client.seed_csv(csv_bytes)
    log.info("✅ %d imóveis importados", imported)


def _warmup(page) -> None:
    """Visita uma página da Caixa para passar o desafio JS uma vez antes do loop."""
    try:
        page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(3_000)
    except Exception as exc:  # noqa: BLE001
        log.warning("Aviso no warmup: %s", exc)


def _scrape_loop(
    page, numeros: list[str], cfg,
    buffer: list[dict], flush,
    removidos: list[str], flush_removidos,
    stats: dict,
) -> bool:
    """Raspa cada imóvel. Retorna True se foi interrompido por bloqueio em série."""
    consecutive_blocks = 0
    total = len(numeros)
    for i, numero in enumerate(numeros, start=1):
        data = scrape_detail(page, numero)
        if data is not None:
            if data.get("removido"):
                removidos.append(numero)
                consecutive_blocks = 0  # não conta como bloqueio
            else:
                buffer.append(data)
                stats["scraped"] += 1
                consecutive_blocks = 0
        else:
            consecutive_blocks += 1
            if consecutive_blocks >= CONSECUTIVE_BLOCK_LIMIT:
                log.warning(
                    "⛔ %d bloqueios seguidos — IP provavelmente limitado pela Caixa. "
                    "Encerrando (os %d restantes voltam no próximo run).",
                    consecutive_blocks, total - i,
                )
                return True

        if len(buffer) >= FLUSH_EVERY:
            flush()
        if len(removidos) >= FLUSH_EVERY:
            flush_removidos()
        if i < total:
            time.sleep(random.uniform(cfg.detail_min_delay, cfg.detail_max_delay))
        if i % 25 == 0:
            log.info(
                "   progresso: %d/%d raspados=%d removidos=%d",
                i, total, stats["scraped"], stats["removed"],
            )
    return False


def enrich_details(client: OlanceClient, cfg) -> None:
    numeros = client.get_pending(cfg.batch_size)
    if not numeros:
        log.info("Nada para enriquecer — todos os imóveis já têm detalhes.")
        return

    log.info("🔎 Enriquecendo %d imóveis (raspando detalhes)...", len(numeros))
    stats = {"scraped": 0, "sent": 0, "removed": 0}
    buffer: list[dict] = []
    removidos: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        updated = client.post_enrichment(buffer)
        stats["sent"] += updated
        log.info("   ↳ lote enviado: %d atualizados (acumulado %d)", updated, stats["sent"])
        buffer.clear()

    def flush_removidos() -> None:
        if not removidos:
            return
        removed = client.remove_items(removidos)
        stats["removed"] += removed
        log.info("   ↳ %d removidos do OLANCE (não disponíveis na Caixa)", removed)
        removidos.clear()

    with Camoufox(headless=cfg.headless, humanize=True, locale="pt-BR") as browser:
        page = browser.new_page()
        _warmup(page)
        aborted = _scrape_loop(page, numeros, cfg, buffer, flush, removidos, flush_removidos, stats)
        flush()
        flush_removidos()

    status = "interrompido (IP bloqueado)" if aborted else "concluído"
    log.info(
        "✅ Enriquecimento %s: %d raspados, %d salvos, %d removidos do OLANCE",
        status, stats["scraped"], stats["sent"], stats["removed"],
    )


def enrich_single(client: OlanceClient, cfg, numero: str) -> int:
    """Modo alvo único: raspa só um imóvel e envia (sem CSV nem fila de pendentes).

    Usado pelo botão "Enriquecer imóvel" do admin no OLANCE (workflow_dispatch
    com input `numero`). Roda em paralelo aos runs agendados (concurrency group próprio).
    """
    log.info("🎯 Modo alvo único: enriquecendo imóvel %s", numero)
    with Camoufox(headless=cfg.headless, humanize=True, locale="pt-BR") as browser:
        page = browser.new_page()
        _warmup(page)
        data = scrape_detail(page, numero)

    if data is None:
        log.warning("⚠️ Imóvel %s bloqueado/sem conteúdo — não enriquecido.", numero)
        return 1

    if data.get("removido"):
        removed = client.remove_items([numero])
        log.info("✅ Imóvel %s não disponível na Caixa — removido do OLANCE (%d).", numero, removed)
        return 0

    updated = client.post_enrichment([data])
    log.info("✅ Imóvel %s enriquecido (%d atualizado no OLANCE).", numero, updated)
    return 0


def main() -> int:
    cfg = load_config()
    client = OlanceClient(cfg.olance_url, cfg.cron_secret)

    log.info("=== Scraper Leilões Caixa → OLANCE (%s) ===", cfg.olance_url)

    target = os.environ.get("TARGET_NUMERO", "").strip()
    if target:
        try:
            return enrich_single(client, cfg, target)
        except Exception:  # noqa: BLE001
            log.exception("❌ Falha no enriquecimento alvo único (%s)", target)
            return 1

    try:
        import_csv(client, cfg.headless)
    except Exception:  # noqa: BLE001
        log.exception("❌ Falha na importação do CSV")
        # Sem CSV não há o que enriquecer de novo; aborta
        return 1

    try:
        enrich_details(client, cfg)
    except Exception:  # noqa: BLE001
        log.exception("❌ Falha no enriquecimento")
        return 1

    log.info("🏁 Concluído com sucesso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
