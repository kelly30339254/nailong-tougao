"""界面侧的 AI 入口：未配置时引导到设置页。"""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox


def require_ai_config(widget, store, main_window):
    cfg = store.get_ai_config()
    if cfg.configured():
        return cfg
    QMessageBox.information(
        widget, "尚未接入 AI",
        "请先到「设置 → AI 接口」填写 API Key。")
    main_window.navigate("settings")
    return None
