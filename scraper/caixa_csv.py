"""Baixa o CSV geral de imóveis da Caixa usando Camoufox (burla o Radware Bot Manager).

Estratégia:
1. Visita SESSION_URL para estabelecer sessão e passar o desafio JS (Radware).
2. Executa fetch() via page.evaluate() dentro do contexto do browser — herda cookies,
   Referer e fingerprint sem navegar para o arquivo, evitando eviction e redirecionamentos.
3. Fallback: navega diretamente para o CSV e captura via response handler.
4. Fallback final: lê o texto renderizado (innerText) e re-codifica em cp1252.
"""
from __future__ import annotations

import base64
import logging
import threading

from camoufox.sync_api import Camoufox

log = logging.getLogger("scraper.csv")

CSV_URL = "https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_geral.csv"
SESSION_URL = "https://venda-imoveis.caixa.gov.br/sistema/download-lista.asp"

# JS que roda dentro do browser: fetch herda cookies da sessão, retorna base64
_FETCH_JS = """async (url) => {
    const resp = await fetch(url, { credentials: 'include' });
    if (!resp.ok) return null;
    const buf = await resp.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let s = '';
    const CHUNK = 8192;
    for (let i = 0; i < bytes.length; i += CHUNK) {
        s += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
    }
    return btoa(s);
}"""


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
        page.set_default_timeout(300_000)

        log.info("🌐 Estabelecendo sessão na Caixa...")
        try:
            page.goto(SESSION_URL, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(5_000)
            log.info("✅ Sessão estabelecida")
        except Exception as exc:  # noqa: BLE001
            log.warning("Aviso ao abrir página de sessão: %s", exc)

        log.info("⬇️  Baixando CSV geral...")
        csv_bytes: bytes | None = None

        # Caminho primário: fetch JS no contexto do browser (sem navegação, sem eviction)
        try:
            b64: str | None = page.evaluate(_FETCH_JS, CSV_URL)
            if b64:
                csv_bytes = base64.b64decode(b64)
                log.info("✅ CSV capturado via fetch JS")
            else:
                log.warning("fetch JS retornou null — servidor recusou o download (status não-2xx)")
        except Exception as exc:  # noqa: BLE001
            log.warning("fetch JS falhou (%s), tentando navegação direta...", exc)

        # Fallback: navegar para o CSV e capturar via response handler
        if not csv_bytes:
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
                # navegação pode abortar se o CSV vier como download
                pass

            if _ready.wait(timeout=120):
                result = _body[0] if _body else None
                if isinstance(result, bytes):
                    csv_bytes = result
                    log.info("✅ CSV capturado via response handler")
                else:
                    log.warning("Response handler falhou ao ler body: %s", result)
            else:
                log.warning("Timeout esperando response do CSV via navegação")

            page.remove_listener("response", _on_csv_response)

        # Fallback final: texto renderizado
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
