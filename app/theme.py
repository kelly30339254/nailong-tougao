"""主题色板与 QSS 模板渲染。"""
from __future__ import annotations

import os
import sys

THEMES = {
    "蔷薇粉": {"primary": "#D6336C", "primary_hover": "#B02858", "primary_light": "#FBE9F0", "text_on_primary": "#FFFFFF"},
    "海岸蓝": {"primary": "#1C7ED6", "primary_hover": "#1668B4", "primary_light": "#E3F0FC", "text_on_primary": "#FFFFFF"},
    "青叶绿": {"primary": "#2F9E44", "primary_hover": "#268238", "primary_light": "#E5F4E9", "text_on_primary": "#FFFFFF"},
    "墨紫":   {"primary": "#7048E8", "primary_hover": "#5C38C9", "primary_light": "#EEEAFB", "text_on_primary": "#FFFFFF"},
    "暖橙":   {"primary": "#E8590C", "primary_hover": "#C94B0A", "primary_light": "#FDECE0", "text_on_primary": "#FFFFFF"},
}

DEFAULT_THEME = "蔷薇粉"


def resource_path(relative: str) -> str:
    """PyInstaller 冻结后从 _MEIPASS 读资源，开发时从项目根目录读。"""
    base = getattr(sys, "_MEIPASS",
                   os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, relative)


_QSS_PATH = resource_path(os.path.join("app", "style.qss"))


def theme_colors(name: str) -> dict:
    return THEMES.get(name, THEMES[DEFAULT_THEME])


def render_qss(theme_name: str) -> str:
    with open(_QSS_PATH, "r", encoding="utf-8") as f:
        template = f.read()
    colors = theme_colors(theme_name)
    qss = template
    for key, value in colors.items():
        qss = qss.replace("{" + key + "}", value)
    # 下拉箭头等资源路径占位符（QSS 中统一用正斜杠）
    qss = qss.replace("{arrow_down}",
                      resource_path(os.path.join("app", "assets", "arrow_down.svg"))
                      .replace("\\", "/"))
    qss = qss.replace("{arrow_up}",
                      resource_path(os.path.join("app", "assets", "arrow_up.svg"))
                      .replace("\\", "/"))
    return qss


def apply_theme(qapp, name: str):
    qapp.setStyleSheet(render_qss(name))
