"""应用日志：写到数据目录 logs/app.log，轮转 5MB×3。"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "nailong"


def setup_logging() -> logging.Logger:
    from .db import data_dir
    log_dir = Path(data_dir()) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)
    return logger


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(name if name.startswith(_LOGGER_NAME) else f"{_LOGGER_NAME}.{name}")


def install_excepthook():
    logger = setup_logging()

    def _hook(exc_type, exc, tb):
        logger.error("未处理异常", exc_info=(exc_type, exc, tb))
        try:
            from PySide6.QtWidgets import QMessageBox, QApplication
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None, "程序出错",
                    f"{exc_type.__name__}: {exc}\n\n详细信息已写入诊断日志。")
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


def export_log_path() -> str:
    from .db import data_dir
    return str(Path(data_dir()) / "logs" / "app.log")
