"""兼容别名：旧模块名 updater 现指向 editor_sync（编辑数据同步）。

新代码请直接 `from .editor_sync import ...`；本文件仅为旧引用保留。
"""
from .editor_sync import *  # noqa: F401,F403
from .editor_sync import (  # noqa: F401
    SETTINGS_KEY,
    build_data_url,
    build_default_url,
    fetch_json,
    parse_payload,
    sync_from_url,
    store_url,
    load_url,
)
