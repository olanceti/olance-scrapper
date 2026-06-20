"""Baixa o CSV geral de imóveis da Caixa usando Camoufox (burla o Radware Bot Manager).

Fluxo descoberto via diagnóstico (2025-06-20):
1. Visitar SESSION_URL estabelece a sessão (Radware challenge + cookies de domínio).
2. Clicar em "Próximo" (sem estado selecionado) dispara o fluxo de sessão —
   o servidor registra a sessão mesmo que o browser navegue para o Internet Banking.
3. page.goto(CSV_URL) retorna HTTP 200 + Content-Disposition: attachment.
   page.expect_download() captura o arquivo em disco antes de o browser fechar.
   (page.goto levanta "Download is starting" — é esperado e ignorado.)
"""
from __future__ import annotations

import logging
from pathlib import Path

from camoufox.sync_api import Camoufox

log = logging.getLogger("scraper.csv")

CSV_URL = "https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_geral.csv"
SESSION_URL = "https://venda-imoveis.caixa.gov.br/sistema/download-lista.asp"


def _has_data_lines(csv_bytes: bytes) -> bool:
    preview = csv_bytes.decode("latin-1", errors="replace")
    for line in preview.split("\n")[:6]:
        s = line.strip()
        if ";" in s and s[:1].isdigit():
            return True
    return False


def download_csv(headless: bool = True) -> bytes:
    """Retorna os bytes brutos do CSV (windows-1252). Lança RuntimeError se bloqueado."""
    with Camoufox(headless=headless, humanize=True, locale="pt-BR") as browser:
        page = browser.new_page()

        # ── 1. Estabelece sessão ────────────────────────────────────────────────────
        log.info("🌐 Estabelecendo sessão na Caixa...")
        try:
            page.goto(SESSION_URL, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(5_000)
            log.info("✅ Sessão estabelecida em %s", page.url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Aviso ao abrir SESSION_URL: %s", exc)

        # ── 2. Clica em "Próximo" para registrar sessão no servidor ─────────────────
        # Sem estado selecionado o browser navega para o Internet Banking, mas os
        # cookies de venda-imoveis.caixa.gov.br ficam válidos e permitem o download.
        try:
            page.click("button:has-text('Próximo')", timeout=8_000)
            log.info("Clicou em 'Próximo' (registra sessão — browser pode navegar para IB)")
            page.wait_for_timeout(2_000)
        except Exception as exc:  # noqa: BLE001
            log.warning("Click em 'Próximo' falhou (ignorando): %s", exc)

        # ── 3. Baixa o CSV via expect_download + goto ───────────────────────────────
        # O servidor retorna HTTP 200 + Content-Disposition: attachment.
        # page.goto levanta "Download is starting" — é esperado e ignorado.
        # expect_download captura o arquivo em disco (dl.path() bloqueia até completar).
        log.info("⬇️  Iniciando download do CSV via expect_download + goto...")
        csv_bytes: bytes | None = None

        try:
            with page.expect_download(timeout=300_000) as dl_info:
                try:
                    page.goto(CSV_URL, wait_until="commit", timeout=300_000)
                except Exception:  # noqa: BLE001
                    pass  # "Download is starting" é esperado

            dl = dl_info.value
            dl_path = dl.path()  # Bloqueia até o download completar
            if dl_path:
                csv_bytes = Path(dl_path).read_bytes()
                log.info("✅ CSV capturado via expect_download (%s)", dl_path)
            else:
                log.warning("Download path é None — download falhou ou foi cancelado")
        except Exception as exc:  # noqa: BLE001
            log.warning("expect_download + goto falhou: %s", exc)

    if not csv_bytes or not _has_data_lines(csv_bytes):
        raise RuntimeError("CSV inválido — possível bloqueio anti-bot (não veio CSV)")

    size_mb = len(csv_bytes) / 1024 / 1024
    log.info("✅ CSV baixado (%.1f MB)", size_mb)
    return csv_bytes
