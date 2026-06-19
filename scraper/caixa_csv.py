"""Baixa o CSV geral de imóveis da Caixa usando Camoufox (burla o Radware Bot Manager).

Estratégia em cascata:
1. Visita SESSION_URL para estabelecer sessão e passar o desafio JS (Radware).
2. Interage com o formulário da página (clica no link/botão de download do CSV geral)
   e captura o arquivo via page.expect_download().
3. Fallback: urllib.request com os cookies da sessão do browser (mesmo IP, sessão válida).
4. Fallback final: lê o texto renderizado (innerText) e re-codifica em cp1252.
"""
from __future__ import annotations

import logging
import urllib.request
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


def _urllib_download(cookies: list[dict]) -> bytes | None:
    """Baixa o CSV usando urllib com os cookies da sessão do browser."""
    cookie_str = "; ".join(
        f"{c['name']}={c['value']}"
        for c in cookies
        if "caixa.gov.br" in c.get("domain", "")
    )
    req = urllib.request.Request(
        CSV_URL,
        headers={
            "Cookie": cookie_str,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Referer": SESSION_URL,
            "Accept": "text/csv,text/plain,*/*",
            "Accept-Language": "pt-BR,pt;q=0.9",
            "Connection": "keep-alive",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310
        return resp.read()


def download_csv(headless: bool = True) -> bytes:
    """Retorna os bytes brutos do CSV (windows-1252). Lança RuntimeError se bloqueado."""
    with Camoufox(headless=headless, humanize=True, locale="pt-BR") as browser:
        page = browser.new_page()

        log.info("🌐 Estabelecendo sessão na Caixa...")
        try:
            page.goto(SESSION_URL, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(5_000)
            log.info("✅ Sessão estabelecida")
        except Exception as exc:  # noqa: BLE001
            log.warning("Aviso ao abrir página de sessão: %s", exc)

        log.info("⬇️  Baixando CSV geral...")
        csv_bytes: bytes | None = None

        # Caminho 1: interagir com o formulário da página e capturar via expect_download
        # (o servidor agora redireciona navegação direta para o formulário)
        try:
            with page.expect_download(timeout=120_000) as dl_info:
                # Tenta link direto para o CSV geral
                try:
                    page.click('a[href*="Lista_imoveis_geral"]', timeout=5_000)
                except Exception:  # noqa: BLE001
                    # Tenta botões/links de download genéricos na página
                    page.click(
                        'a:has-text("ista completa"), '
                        'button:has-text("ista completa"), '
                        'a[href*="geral"], '
                        'a:has-text("ownload"), '
                        'button:has-text("ownload"), '
                        'a:has-text("aixar"), '
                        'button:has-text("aixar"), '
                        'input[type="submit"]',
                        timeout=10_000,
                    )
            dl = dl_info.value
            dl_path = dl.path()
            if dl_path:
                csv_bytes = Path(dl_path).read_bytes()
                log.info("✅ CSV capturado via download do formulário")
        except Exception as exc:  # noqa: BLE001
            log.warning("Download via formulário falhou (%s), tentando urllib...", exc)

        # Caminho 2: urllib com cookies da sessão (mesmo IP, sessão validada pelo Radware)
        if not csv_bytes:
            try:
                cookies = page.context.cookies()
                csv_bytes = _urllib_download(cookies)
                if csv_bytes and _has_data_lines(csv_bytes):
                    log.info("✅ CSV capturado via urllib")
                else:
                    preview = (csv_bytes or b"")[:200].decode("latin-1", errors="replace")
                    log.warning("urllib retornou conteúdo inválido (preview): %s", preview)
                    csv_bytes = None
            except Exception as exc:  # noqa: BLE001
                log.warning("urllib falhou (%s), tentando innerText...", exc)

        # Caminho 3: texto renderizado (last resort)
        if not csv_bytes:
            try:
                text = page.inner_text("body")
                preview = text[:300].replace("\n", " ↵ ")
                log.warning("Fallback innerText (primeiros 300 chars): %s", preview)
                # Se o navegador mangleou o encoding (char de substituição �), os acentos
                # já estão perdidos — melhor falhar e deixar o próximo run corrigir.
                if "�" in text:
                    raise RuntimeError(
                        "CSV veio com encoding corrompido (char de substituição) — abortando"
                    )
                # Firefox usa win1252 como fallback Western → latin1 preserva 0x00-0xFF
                csv_bytes = text.encode("latin-1", errors="replace")
            except RuntimeError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Não foi possível ler o CSV: {exc}") from exc

    if not csv_bytes or not _has_data_lines(csv_bytes):
        raise RuntimeError("CSV inválido — possível bloqueio anti-bot (não veio CSV)")

    size_mb = len(csv_bytes) / 1024 / 1024
    log.info("✅ CSV baixado (%.1f MB)", size_mb)
    return csv_bytes
