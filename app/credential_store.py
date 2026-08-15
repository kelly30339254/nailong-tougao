"""Secure storage for mailbox authorization codes.

Production uses the operating-system keyring.  Test/development runs that set
``NAILONG_DATA_DIR`` use a process-local, thread-safe store instead.
"""
from __future__ import annotations

import os
from threading import RLock

SERVICE_NAME = "NailongPost"
_LOCK = RLock()
_MEMORY: dict[str, str] = {}


def _memory_mode() -> bool:
    return bool(os.environ.get("NAILONG_DATA_DIR"))


def get(key: str) -> str | None:
    if _memory_mode():
        with _LOCK:
            return _MEMORY.get(key)
    try:
        import keyring
        return keyring.get_password(SERVICE_NAME, key)
    except Exception:
        return None


def set(key: str, value: str) -> bool:
    if _memory_mode():
        with _LOCK:
            _MEMORY[key] = value
        return True
    try:
        import keyring
        keyring.set_password(SERVICE_NAME, key, value)
        return True
    except Exception:
        return False


def delete(key: str) -> bool:
    if _memory_mode():
        with _LOCK:
            _MEMORY.pop(key, None)
        return True
    try:
        import keyring
        keyring.delete_password(SERVICE_NAME, key)
        return True
    except Exception as exc:
        try:
            if isinstance(exc, keyring.errors.PasswordDeleteError):
                return True
        except Exception:
            pass
        return False
