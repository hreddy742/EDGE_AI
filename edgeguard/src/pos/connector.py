from dataclasses import dataclass, field
from datetime import datetime

import requests


@dataclass
class POSReceipt:
    customer_id: str
    paid_count: int | None = None
    paid_item_ids: list[str] = field(default_factory=list)
    ts: datetime | None = None
    metadata: dict = field(default_factory=dict)


class POSConnector:
    """
    Scaffold connector for V2.
    For MVP/V1, API can call reconciliation directly.
    """

    def __init__(self, base_url: str | None = None, timeout_sec: float = 3.0) -> None:
        self.base_url = base_url
        self.timeout_sec = timeout_sec

    def fetch_receipt(self, checkout_session_id: str) -> POSReceipt | None:
        if not self.base_url:
            return None
        url = f"{self.base_url.rstrip('/')}/checkout/{checkout_session_id}"
        try:
            response = requests.get(url, timeout=self.timeout_sec)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None

        return POSReceipt(
            customer_id=str(payload.get("customer_id", "")),
            paid_count=payload.get("paid_count"),
            paid_item_ids=list(payload.get("paid_item_ids", [])),
            ts=datetime.utcnow(),
            metadata=payload,
        )
