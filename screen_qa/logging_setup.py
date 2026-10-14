"""日志:写入 logs/app.log,便于排查问题。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import log_dir


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("screen_qa")
    logger.setLevel(level)
    if logger.handlers:
        return logger

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        handler = RotatingFileHandler(
            log_dir() / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    except OSError:
        pass

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    return logger


log = setup_logging()