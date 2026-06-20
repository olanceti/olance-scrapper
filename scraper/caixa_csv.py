"""Baixa o CSV geral de imóveis da Caixa usando Camoufox (burla o Radware Bot Manager).

Estratégia em cascata:
1. Visita SESSION_URL para estabelecer sessão e passar o desafio JS (Radware).
2. Intercepta via page.route() + submete formulário com JS síncrono.
3. Tenta expect_download com click em elementos do form.
4. Fallback final: innerText.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from camoufox.sync_api import Camoufox

log = logging.getLogger("scraper.csv")

CSV_URL = "https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_geral.csv"
SESSION_URL = "https://venda-imoveis.caixa.gov.br/sistema/download-lista.asp"

# JS síncrono: seleciona "Todos" no dropdown de estado e submete o form.
# Síncrono = sem "Permission denied to access property constructor" do Firefox async.
_FORM_SUBMIT_JS = """() => {
    const sel = document.querySelector('select');
    if (sel) {
        for (let i = 0; i < sel.options.length; i++) {
            const t = (sel.options[i].text || '').toLowerCase();
            if (t.includes('todo') || t.includes('geral') || sel.options[i].value === '') {
                sel.selectedIndex = i;
                break;
            }
        }
    }
    const form = document.querySelector('form');
    if (form) { form.submit(); return 'submitted:' + (form.action || '?'); }
    return 'no-form';
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

        log.info("🌐 Estabelecendo sessão na Caixa...")
        try:
            page.goto(SESSION_URL, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(5_000)
            log.info("✅ Sessão estabelecida em %s", page.url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Aviso ao abrir página de sessão: %s", exc)

        # ── Diagnóstico: loga o HTML da página para identificar seletores ──
        try:
            html = page.content()
            log.info("=== HTML download-lista.asp (3000 chars) ===\n%s\n===", html[:3000])
        except Exception:  # noqa: BLE001
            pass

        log.info("⬇️  Baixando CSV geral...")
        csv_bytes: bytes | None = None

        # ── Abordagem 1: page.route() intercepta no nível de rede ──────────────────
        # route.fetch() obtém a resposta ANTES de o browser poder eviccionar o body.
        _csv_buf: list[bytes] = []
        _route_done = threading.Event()

        def _intercept(route) -> None:
            log.debug("Route interceptou: %s", route.request.url)
            resp = None
            try:
                resp = route.fetch()
                log.info("Route fetch: HTTP %d para %s", resp.status, route.request.url)
                if 200 <= resp.status < 300:
                    _csv_buf.append(resp.body())
            except Exception as exc:  # noqa: BLE001
                log.warning("route.fetch() falhou (%s) — continuando normalmente", exc)
            finally:
                try:
                    if resp is not None:
                        route.fulfill(response=resp)
                    else:
                        route.continue_()
                except Exception:  # noqa: BLE001
                    pass
                _route_done.set()

        page.route("**/Lista_imoveis*", _intercept)

        try:
            js_result = page.evaluate(_FORM_SUBMIT_JS)
            log.info("Form submit JS: %s", js_result)
        except Exception as exc:  # noqa: BLE001
            log.warning("Form submit JS falhou: %s", exc)

        if _route_done.wait(timeout=120):
            if _csv_buf:
                csv_bytes = _csv_buf[0]
                log.info("✅ CSV capturado via route interception")
            else:
                log.warning("Route interceptou mas CSV buffer vazio")
        else:
            log.warning("Timeout 120s aguardando route do CSV")

        try:
            page.unroute("**/Lista_imoveis*", _intercept)
        except Exception:  # noqa: BLE001
            pass

        # ── Abordagem 2: expect_download com click em elementos do form ─────────────
        if not csv_bytes:
            try:
                # Volta para SESSION_URL se a navegação levou pra outro lugar
                if SESSION_URL not in page.url:
                    log.info("Voltando para SESSION_URL (página atual: %s)", page.url)
                    page.goto(SESSION_URL, wait_until="networkidle", timeout=60_000)
                    page.wait_for_timeout(3_000)

                with page.expect_download(timeout=90_000) as dl_info:
                    # Restringe o click a elementos DENTRO do form ou da área de conteúdo
                    # para evitar nav-bar links como "Downloads"
                    page.click(
                        "form button, form input[type='submit'], "
                        "form a[href*='csv'], form a[href*='lista'], "
                        "form a[href*='Lista'], form a[href*='imovel']",
                        timeout=15_000,
                    )
                dl = dl_info.value
                dl_path = dl.path()
                if dl_path:
                    csv_bytes = Path(dl_path).read_bytes()
                    log.info("✅ CSV capturado via expect_download")
            except Exception as exc:  # noqa: BLE001
                log.warning("expect_download falhou: %s", exc)
                # Log da página atual para entender onde estamos
                try:
                    log.info("Página atual após falha: %s", page.url)
                    html2 = page.content()
                    log.info("HTML após falha (2000 chars):\n%s", html2[:2000])
                except Exception:  # noqa: BLE001
                    pass

        # ── Abordagem 3: innerText (last resort) ───────────────────────────────────
        if not csv_bytes:
            try:
                text = page.inner_text("body")
                preview = text[:300].replace("\n", " ↵ ")
                log.warning("Fallback innerText (300 chars): %s", preview)
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
