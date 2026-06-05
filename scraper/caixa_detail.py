"""Raspa a página de detalhe de um imóvel da Caixa e extrai os campos que não vêm no CSV.

Campos: matrícula, comarca, leiloeiro, inscrição imobiliária,
datas e preços do 1º e 2º leilão.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("scraper.detail")

DETAIL_URL = "https://venda-imoveis.caixa.gov.br/sistema/detalhe-imovel.asp?hdnOrigem=index&hdnimovel={numero}"

# Ordinal tolerante (º, °, ª ou 'o') e "Leilão"/"Leilao"
_ORD = r"[ºo°ª]?"
_LEILAO = r"Leil[ãa]o"

_RE_MATRICULA = re.compile(r"Matr[íi]cula\(?s?\)?:\s*(\d+)", re.IGNORECASE)
_RE_COMARCA = re.compile(r"Comarca:\s*(.+?)\s+(?:Of[íi]cio|Inscri|Averba)", re.IGNORECASE)
_RE_INSCRICAO = re.compile(r"Inscri[çc][ãa]o\s+imobili[áa]ria:\s*([\d.]+)", re.IGNORECASE)
_RE_LEILOEIRO = re.compile(r"Leiloeiro\(a\):\s*(.+?)\s+(?:Data\s+do|Endere|$)", re.IGNORECASE)

_RE_DATA_1 = re.compile(
    rf"Data\s+do\s+1{_ORD}\s*{_LEILAO}\s*-\s*(\d{{2}}/\d{{2}}/\d{{4}}\s*-\s*\d{{1,2}}h\d{{2}})",
    re.IGNORECASE,
)
_RE_DATA_2 = re.compile(
    rf"Data\s+do\s+2{_ORD}\s*{_LEILAO}\s*-\s*(\d{{2}}/\d{{2}}/\d{{4}}\s*-\s*\d{{1,2}}h\d{{2}})",
    re.IGNORECASE,
)
_RE_PRECO_1 = re.compile(
    rf"m[íi]nimo\s+de\s+venda\s+1{_ORD}\s*{_LEILAO}:?\s*R\$\s*([\d.]+,\d{{2}})",
    re.IGNORECASE,
)
_RE_PRECO_2 = re.compile(
    rf"m[íi]nimo\s+de\s+venda\s+2{_ORD}\s*{_LEILAO}:?\s*R\$\s*([\d.]+,\d{{2}})",
    re.IGNORECASE,
)
# Fallback: todos os valores "mínimo ... R$" em ordem
_RE_PRECO_ANY = re.compile(r"m[íi]nimo[^R]{0,40}R\$\s*([\d.]+,\d{2})", re.IGNORECASE)


def _parse_brl(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _m(pattern: re.Pattern, text: str) -> str | None:
    found = pattern.search(text)
    return found.group(1).strip() if found else None


def is_blocked(text: str) -> bool:
    """Detecta página de CAPTCHA/anti-bot em vez do conteúdo real."""
    if not text:
        return True
    lowered = text.lower()
    if "bot manager" in lowered or "captcha" in lowered:
        return True
    # página real sempre contém "Leilão"/"Leilao"
    return "leil" not in lowered


def parse_detail_text(raw_text: str, numero: str) -> dict | None:
    """Extrai os campos do texto da página. Retorna None se a página estiver bloqueada."""
    if is_blocked(raw_text):
        return None

    text = re.sub(r"\s+", " ", raw_text)

    preco_1 = _parse_brl(_m(_RE_PRECO_1, text))
    preco_2 = _parse_brl(_m(_RE_PRECO_2, text))

    # Fallback de preços por ordem (1º depois 2º) se os rótulos específicos falharem
    if preco_1 is None or preco_2 is None:
        ordered = _RE_PRECO_ANY.findall(text)
        if preco_1 is None and len(ordered) >= 1:
            preco_1 = _parse_brl(ordered[0])
        if preco_2 is None and len(ordered) >= 2:
            preco_2 = _parse_brl(ordered[1])

    return {
        "numeroImovel": numero,
        "matricula": _m(_RE_MATRICULA, text),
        "comarca": _m(_RE_COMARCA, text),
        "leiloeiro": _m(_RE_LEILOEIRO, text),
        "inscricaoImobiliaria": _m(_RE_INSCRICAO, text),
        "primeiroLeilaoData": _m(_RE_DATA_1, text),
        "primeiroLeilaoPreco": preco_1,
        "segundoLeilaoData": _m(_RE_DATA_2, text),
        "segundoLeilaoPreco": preco_2,
    }


def scrape_detail(page, numero: str) -> dict | None:
    """Navega até o detalhe e extrai os dados. Retorna None se bloqueado/erro."""
    url = DETAIL_URL.format(numero=numero)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(1_500)  # deixa o desafio JS resolver, se houver
        text = page.inner_text("body")
    except Exception as exc:  # noqa: BLE001
        log.warning("[%s] erro ao carregar detalhe: %s", numero, exc)
        return None

    data = parse_detail_text(text, numero)
    if data is None:
        log.warning("[%s] página bloqueada/sem conteúdo", numero)
    return data
