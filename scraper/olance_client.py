"""Cliente HTTP para o OLANCE. Autentica com Bearer CRON_SECRET."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("scraper.olance")


class OlanceClient:
    def __init__(self, base_url: str, cron_secret: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {cron_secret}"}
        self._timeout = timeout

    def seed_csv(self, csv_bytes: bytes) -> int:
        """Envia o CSV bruto para importação. Retorna a quantidade importada."""
        url = f"{self.base_url}/api/admin/leiloes-seed-file"
        resp = httpx.post(
            url,
            content=csv_bytes,
            headers={**self._headers, "Content-Type": "application/octet-stream"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return int(data.get("imported", 0))

    def get_pending(self, limit: int) -> list[str]:
        """Lista numero_imovel ainda não enriquecidos (novos primeiro)."""
        url = f"{self.base_url}/api/admin/leiloes/pending-enrichment"
        resp = httpx.get(
            url,
            params={"limit": limit},
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return [str(n) for n in data.get("numeros", [])]

    def post_enrichment(self, items: list[dict]) -> int:
        """Envia o lote de imóveis enriquecidos. Retorna a quantidade atualizada."""
        if not items:
            return 0
        url = f"{self.base_url}/api/admin/leiloes/enrich-batch"
        resp = httpx.post(
            url,
            json={"items": items},
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return int(data.get("updated", 0))
