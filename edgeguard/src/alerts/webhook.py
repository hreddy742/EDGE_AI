import requests

from src.core.config import Settings
from src.core.logger import logger


def send_event_webhook(settings: Settings, payload: dict) -> None:
    if not settings.webhook_url:
        return
    try:
        response = requests.post(
            settings.webhook_url,
            json=payload,
            timeout=settings.webhook_timeout_sec,
        )
        if response.status_code >= 300:
            logger.warning(f"Webhook returned non-success status={response.status_code}")
    except Exception as exc:
        logger.warning(f"Webhook delivery failed: {exc}")
