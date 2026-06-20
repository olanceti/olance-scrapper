"""Baixa o CSV geral de imóveis da Caixa usando Camoufox (burla o Radware Bot Manager).

Estratégia:
1. Visita SESSION_URL para estabelecer sessão (Radware challenge).
2. Loga HTML do body + todos os requests de rede para diagnóstico.
3. Tenta download via interação com o formulário multi-step.
4. Captura via page.on("response") ou expect_download.
"""
from __future__ import annotations

import logging
from pathlib import Path

from camoufox.sync_api import Camoufox

log = logging.getLogger("scraper.csv")

CSV_URL = "https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_geral.csv"
SESSION_URL = "https://venda-imoveis.caixa.gov.br/sistema/download-lista.asp"

# Tipos de recursos que não interessam para diagnóstico
_SKIP_TYPES = {"stylesheet", "script", "font", "image", "media", "manifest"}


def _has_data_lines(csv_bytes: bytes) -> bool:
    preview = csv_bytes.decode("latin-1", errors="replace")
    for line in preview.split("\n")[:6]:
        s = line.strip()
        if ";" in s and s[:1].isdigit():
            return True
    return False


def download_csv(headless: bool = True) -> bytes:  # noqa: C901
    """Retorna os bytes brutos do CSV (windows-1252). Lança RuntimeError se bloqueado."""
    with Camoufox(headless=headless, humanize=True, locale="pt-BR") as browser:
        page = browser.new_page()

        # ── Captura de rede global ──────────────────────────────────────────────────
        # Monitora TODOS os requests/responses para entender o fluxo do formulário.
        csv_from_response: list[bytes] = []

        def _on_request(req) -> None:
            if req.resource_type not in _SKIP_TYPES:
                log.info("→ REQ  %s  %s", req.method, req.url)

        def _on_response(resp) -> None:
            if resp.request.resource_type not in _SKIP_TYPES:
                log.info("← RES  HTTP %d  %s", resp.status, resp.url)
            # Se o CSV veio em qualquer request, captura imediatamente
            if "Lista_imoveis" in resp.url and 200 <= resp.status < 300:
                try:
                    body = resp.body()
                    csv_from_response.append(body)
                    log.info("!! CSV CAPTURADO no response handler: %d bytes", len(body))
                except Exception as exc:  # noqa: BLE001
                    log.warning("CSV response.body() falhou: %s", exc)

        page.on("request", _on_request)
        page.on("response", _on_response)

        # ── 1. Estabelece sessão ────────────────────────────────────────────────────
        log.info("🌐 Estabelecendo sessão na Caixa...")
        try:
            page.goto(SESSION_URL, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(5_000)
            log.info("✅ Sessão estabelecida em %s", page.url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Aviso ao abrir página de sessão: %s", exc)

        # ── 2. Diagnóstico do body ──────────────────────────────────────────────────
        try:
            body_html = page.evaluate("document.body.innerHTML")
            log.info("=== BODY innerHTML (5000 chars) ===\n%s\n===", body_html[:5000])
        except Exception as exc:  # noqa: BLE001
            log.warning("body innerHTML falhou: %s", exc)

        # Encontra URLs de CSV + todos elementos interativos
        try:
            csv_refs = page.evaluate(
                """() => {
                const out = [];
                document.querySelectorAll('a').forEach(a => {
                    if (a.href && (a.href.includes('.csv') || a.href.includes('Lista_imoveis') || a.href.includes('lista') || a.href.includes('baixar') || a.href.includes('download'))) {
                        out.push('LINK href=' + a.href + '  text=' + a.innerText.trim().slice(0,50));
                    }
                });
                document.querySelectorAll('form').forEach(f => {
                    out.push('FORM action=' + f.action + ' method=' + f.method);
                });
                document.querySelectorAll('input,select,button').forEach(el => {
                    out.push(el.tagName + ' name=' + (el.name||'') + ' type=' + (el.type||'') + ' value="' + (el.value||'').slice(0,30) + '" text="' + (el.innerText||'').trim().slice(0,30) + '"');
                });
                document.querySelectorAll('script:not([src])').forEach(s => {
                    const m = s.textContent.match(/Lista_imoveis[^"' <]+/g);
                    if (m) m.forEach(u => out.push('SCRIPT ref: ' + u));
                });
                return out.join('\\n') || '(nenhum)';
            }"""
            )
            log.info("=== Elementos interativos + refs CSV ===\n%s\n===", csv_refs)
        except Exception as exc:  # noqa: BLE001
            log.warning("Avaliação de elementos falhou: %s", exc)

        # ── 3. Tenta download ───────────────────────────────────────────────────────
        log.info("⬇️  Baixando CSV geral...")
        csv_bytes: bytes | None = None

        # Tentativa A: seleciona "Todos" e clica no botão de submit/avançar do form
        try:
            try:
                page.select_option("select", index=0, timeout=5_000)
                log.info("Selecionou índice 0 no select")
            except Exception as exc:  # noqa: BLE001
                log.warning("select_option[0] falhou: %s", exc)

            page.wait_for_timeout(1_000)

            with page.expect_download(timeout=60_000) as dl_info:
                page.click(
                    "button[type='submit'], input[type='submit'], button:not([type]), "
                    "a.btn, a.button, a[class*='baixar'], a[class*='download']",
                    timeout=10_000,
                )
                log.info("Clicou no primeiro botão — aguardando download...")

            dl = dl_info.value
            dl_path = dl.path()
            if dl_path:
                csv_bytes = Path(dl_path).read_bytes()
                log.info("✅ CSV capturado via expect_download (tentativa A)")
        except Exception as exc:  # noqa: BLE001
            log.warning("Tentativa A falhou: %s", exc)
            page.wait_for_timeout(3_000)

        if not csv_bytes and csv_from_response:
            csv_bytes = csv_from_response[-1]
            log.info("✅ CSV capturado via response handler (pós tentativa A)")

        # Tentativa B: segundo clique (wizard pode ter avançado)
        if not csv_bytes:
            try:
                body2 = page.evaluate("document.body.innerText")
                log.info("innerText pós tentativa A (500 chars): %s", body2[:500].replace("\n", " ↵ "))

                with page.expect_download(timeout=60_000) as dl_info:
                    page.click(
                        "button[type='submit'], input[type='submit'], button:not([type]), "
                        "a.btn, a.button, a[class*='baixar'], a[class*='download']",
                        timeout=10_000,
                    )
                    log.info("Clicou no segundo botão — aguardando download...")

                dl = dl_info.value
                dl_path = dl.path()
                if dl_path:
                    csv_bytes = Path(dl_path).read_bytes()
                    log.info("✅ CSV capturado via expect_download (tentativa B)")
            except Exception as exc:  # noqa: BLE001
                log.warning("Tentativa B falhou: %s", exc)

        if not csv_bytes and csv_from_response:
            csv_bytes = csv_from_response[-1]
            log.info("✅ CSV capturado via response handler (pós tentativa B)")

        # Tentativa C: navega direto para CSV_URL (pode funcionar com sessão ativa)
        if not csv_bytes:
            try:
                log.info("Tentativa C: goto direto CSV_URL com sessão ativa...")
                page.goto(CSV_URL, wait_until="commit", timeout=120_000)
                page.wait_for_timeout(5_000)
                log.info("URL após goto CSV: %s", page.url)
            except Exception as exc:  # noqa: BLE001
                log.warning("goto CSV_URL falhou: %s", exc)

            if not csv_bytes and csv_from_response:
                csv_bytes = csv_from_response[-1]
                log.info("✅ CSV capturado via response handler (tentativa C)")

        # ── Fallback: innerText ─────────────────────────────────────────────────────
        if not csv_bytes:
            try:
                text = page.inner_text("body")
                preview = text[:300].replace("\n", " ↵ ")
                log.warning("Fallback innerText (300 chars): %s", preview)
                if "�" in text:
                    raise RuntimeError(
                        "CSV com encoding corrompido (char de substituição) — abortando"
                    )
                csv_bytes = text.encode("latin-1", errors="replace")
            except RuntimeError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Não foi possível ler o CSV: {exc}") from exc

        page.remove_listener("request", _on_request)
        page.remove_listener("response", _on_response)

    if not csv_bytes or not _has_data_lines(csv_bytes):
        raise RuntimeError("CSV inválido — possível bloqueio anti-bot (não veio CSV)")

    size_mb = len(csv_bytes) / 1024 / 1024
    log.info("✅ CSV baixado (%.1f MB)", size_mb)
    return csv_bytes
