"""Raspa a página de detalhe de um imóvel da Caixa e extrai os campos que não vêm no CSV.

Campos: aceita FGTS, datas e preços dos leilões (varia por modalidade), responsabilidade
de condomínio e tributos, inscrição fiscal, link do edital.
A matrícula NÃO é raspada aqui — sua URL é derivada no OLANCE (uf+numero).
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("scraper.detail")

DETAIL_URL = "https://venda-imoveis.caixa.gov.br/sistema/detalhe-imovel.asp?hdnOrigem=index&hdnimovel={numero}"
_BASE_URL = "https://venda-imoveis.caixa.gov.br"

_ORD = r"[ºo°ª]?"
_LEILAO = r"Leil[ãa]o"
_DATE = r"(\d{2}/\d{2}/\d{4}\s*-\s*\d{1,2}h\d{2})"

_RE_INSCRICAO = re.compile(r"Inscri[çc][ãa]o\s+imobili[áa]ria:\s*([\d.]+)", re.IGNORECASE)
_RE_CEP = re.compile(r"CEP:\s*(\d{5}-?\d{3})", re.IGNORECASE)
_RE_FGTS = re.compile(r"Permite\s+utiliza[çc][ãa]o\s+de\s+FGTS", re.IGNORECASE)
_RE_RECURSOS = re.compile(r"Recursos\s+pr[óo]prios", re.IGNORECASE)

# Datas — SFI tem 1º e 2º; Licitação Aberta tem uma só ("Data da Licitação Aberta")
_RE_DATA_1 = re.compile(rf"Data\s+do\s+1{_ORD}\s*{_LEILAO}\s*-\s*{_DATE}", re.IGNORECASE)
_RE_DATA_2 = re.compile(rf"Data\s+do\s+2{_ORD}\s*{_LEILAO}\s*-\s*{_DATE}", re.IGNORECASE)
_RE_DATA_LICITACAO = re.compile(rf"Data\s+da\s+Licita[çc][ãa]o[^-\d]*-\s*{_DATE}", re.IGNORECASE)

# Preços — SFI tem "mínimo de venda 1º/2º Leilão"; demais têm "mínimo de venda:" (único)
_RE_PRECO_1 = re.compile(rf"m[íi]nimo\s+de\s+venda\s+1{_ORD}\s*{_LEILAO}:?\s*R\$\s*([\d.]+,\d{{2}})", re.IGNORECASE)
_RE_PRECO_2 = re.compile(rf"m[íi]nimo\s+de\s+venda\s+2{_ORD}\s*{_LEILAO}:?\s*R\$\s*([\d.]+,\d{{2}})", re.IGNORECASE)
_RE_PRECO_UNICO = re.compile(r"m[íi]nimo\s+de\s+venda:\s*R\$\s*([\d.]+,\d{2})", re.IGNORECASE)

# Responsabilidade — texto pode ser longo (explica limite de 10% etc.)
_RE_CONDOMINIO = re.compile(r"Condom[íi]nio:\s*(.+?)\s*(?:Tributos:|$)", re.IGNORECASE)
_RE_TRIBUTOS = re.compile(
    r"Tributos:\s*(.+?)\s*(?:Baixar|Regras\s+da|Edital|D[êe]\s+seu\s+lance|Sou\s+o\s+ex|"
    r"Corretores|Formas\s+de\s+pagamento|$)",
    re.IGNORECASE,
)

_RE_EXIBEDOC = re.compile(r"ExibeDoc\(['\"]([^'\"]+)['\"]\)", re.IGNORECASE)


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
    return "leil" not in lowered and "venda" not in lowered


def parse_detail_text(raw_text: str, numero: str) -> dict | None:
    """Extrai os campos do texto da página. Retorna None se a página estiver bloqueada."""
    if is_blocked(raw_text):
        return None

    text = re.sub(r"\s+", " ", raw_text)

    # Datas
    primeiro_data = _m(_RE_DATA_1, text)
    segundo_data = _m(_RE_DATA_2, text)
    if not primeiro_data:
        # Licitação Aberta tem uma data única → vai pro "primeiro"
        primeiro_data = _m(_RE_DATA_LICITACAO, text)

    # Preços
    primeiro_preco = _parse_brl(_m(_RE_PRECO_1, text))
    segundo_preco = _parse_brl(_m(_RE_PRECO_2, text))
    if primeiro_preco is None:
        # Modalidades de leilão único usam "mínimo de venda:" (sem ordinal)
        primeiro_preco = _parse_brl(_m(_RE_PRECO_UNICO, text))

    # Responsabilidade (corta em ~400 chars pra caber na coluna)
    condominio = _m(_RE_CONDOMINIO, text)
    tributos = _m(_RE_TRIBUTOS, text)

    return {
        "numeroImovel": numero,
        "aceitaFgts": bool(_RE_FGTS.search(text)),
        "recursosProprios": bool(_RE_RECURSOS.search(text)),
        "inscricaoImobiliaria": _m(_RE_INSCRICAO, text),
        "cep": _m(_RE_CEP, text),
        "primeiroLeilaoData": primeiro_data,
        "primeiroLeilaoPreco": primeiro_preco,
        "segundoLeilaoData": segundo_data,
        "segundoLeilaoPreco": segundo_preco,
        "condominioResponsavel": condominio[:400] if condominio else None,
        "tributosResponsavel": tributos[:400] if tributos else None,
    }


def _extract_edital_url(page) -> str | None:
    """Lê o onclick `ExibeDoc('/editais/...PDF')` do botão de edital.

    A matrícula NÃO é extraída aqui — sua URL é derivada no OLANCE (uf+numero).
    """
    try:
        els = page.query_selector_all('[onclick*="ExibeDoc"]')
        for el in els:
            onclick = el.get_attribute("onclick") or ""
            m = _RE_EXIBEDOC.search(onclick)
            if not m:
                continue
            path = m.group(1)
            if "/matricula/" in path.lower():
                continue  # é a matrícula, ignora (derivada no OLANCE)
            return path if path.startswith("http") else f"{_BASE_URL}{path}"
    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_loteamento(page) -> str | None:
    """Lê o título do imóvel (nome do loteamento/localidade) — o 1º <h5> da página.

    Ex: 'LOT PQ VIDA NOVA VOTUPORANGA III'. Não vem no CSV.
    """
    try:
        el = page.query_selector("h5")
        if not el:
            return None
        t = (el.inner_text() or "").strip()
        # Sanidade: não vazio e tamanho plausível de um nome
        if 2 <= len(t) <= 200:
            return t
    except Exception:  # noqa: BLE001
        pass
    return None


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
        return None

    data["editalUrl"] = _extract_edital_url(page)
    data["loteamento"] = _extract_loteamento(page)
    return data
