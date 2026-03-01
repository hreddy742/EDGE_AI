import sys

from loguru import logger


def configure_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | {message}",
        level="INFO",
        enqueue=False,
    )
