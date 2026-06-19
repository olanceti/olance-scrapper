"""Baixa o CSV geral de imóveis da Caixa usando Camoufox (burla o Radware Bot Manager).

Estratégia (espelha o script Puppeteer comprovado do OLANCE):
1. Visita a página de download para estabelecer sessão e passar o desafio JS.
2. Captura a resposta via event handler (evita "evicted" do expect_response).
3. Fallback: lê o texto renderizado (innerText) e re-codifica em cp1252.
"""
from __future__ import annotations

import logging
import threading

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

        log.info("🌐 Estabelecendo sessão na Caixa...")
        try:
            page.goto(SESSION_URL, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(5_000)
            log.info("✅ Sessão estabelecida")
        except Exception as exc:  # noqa: BLE001
            log.warning("Aviso ao abrir página de sessão: %s", exc)

        log.info("⬇️  Baixando CSV geral...")
        csv_bytes: bytes | None = None

        # Caminho primário: response handler lê o body enquanto a resposta ainda está
        # viva no buffer do Playwright — evita o "evicted" que ocorre ao ler
        # resp_info.value.body() depois do with-block fechar.
        _body: list[bytes | BaseException] = []
        _ready = threading.Event()

        def _on_csv_response(response) -> None:
            if "Lista_imoveis_geral" not in response.url:
                return
            if not (200 <= response.status < 300):
                log.warning("Resposta inesperada para CSV: HTTP %d", response.status)
                _ready.set()
                return
            try:
                _body.append(response.body())
            except Exception as exc:  # noqa: BLE001
                _body.append(exc)
            _ready.set()

        page.on("response", _on_csv_response)
        try:
            page.goto(CSV_URL, wait_until="commit", timeout=300_000)
        except Exception:  # noqa: BLE001
            # navegação pode abortar se o CSV vier como download — resposta já foi capturada
            pass

        if _ready.wait(timeout=300):
            result = _body[0] if _body else None
            if isinstance(result, bytes):
                csv_bytes = result
            else:
                log.warning("Response handler falhou ao ler body: %s", result)
        else:
            log.warning("Timeout de 300s esperando response do CSV")

        page.remove_listener("response", _on_csv_response)

        # Fallback: texto renderizado (só se a captura da resposta falhar)
        if not csv_bytes:
            try:
                text = page.inner_text("body")
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Não foi possível ler o CSV: {exc}") from exc
            # Log do conteúdo para diagnóstico de bloqueio anti-bot
            preview = text[:300].replace("\n", " ↵ ")
            log.warning("Fallback innerText (primeiros 300 chars): %s", preview)
            # Se o navegador mangleou o encoding (decodificou como UTF-8 e gerou o
            # char de substituição �), os acentos já estão perdidos. NÃO enviar lixo
            # — melhor falhar e deixar o próximo run (com a resposta crua) corrigir.
            if "�" in text:
                raise RuntimeError(
                    "CSV veio com encoding corrompido (char de substituição) — abortando para não gravar acentos errados"
                )
            # innerText correto (Firefox usa win1252 como fallback Western) → latin1 preserva 0x00-0xFF
            csv_bytes = text.encode("latin-1", errors="replace")

    if not csv_bytes or not _has_data_lines(csv_bytes):
        raise RuntimeError("CSV inválido — possível bloqueio anti-bot (não veio CSV)")

    size_mb = len(csv_bytes) / 1024 / 1024
    log.info("✅ CSV baixado (%.1f MB)", size_mb)
    return csv_bytes
