"""Защита паролей FTP-панелей: DPAPI (Windows), системное хранилище секретов
(keyring на Linux/macOS) и обратимый base64-фолбэк.

Выделено из webapp/app.py. Модуль-«лист»: зависит только от stdlib.
"""
from __future__ import annotations

import base64
import logging
import sys
from typing import Any

KEYRING_SERVICE = "OptiCIP Dashboard FTP"


def _keyring_backend() -> Any | None:
    """Модуль keyring, если он есть и не на Windows (там DPAPI). None — нет keyring."""
    if sys.platform == "win32":
        return None
    try:
        import keyring
    except Exception:
        return None
    return keyring


def _keyring_store(secret_id: str, value: str) -> bool:
    keyring = _keyring_backend()
    if keyring is None or not secret_id:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, secret_id, value)
        return True
    except Exception:
        # Нет backend'а (NoKeyringError) или сбой доступа — уходим в base64-фолбэк.
        logging.warning("Системное хранилище секретов недоступно, использую локальное кодирование.")
        return False


def _keyring_fetch(secret_id: str) -> str:
    keyring = _keyring_backend()
    if keyring is None or not secret_id:
        return ""
    try:
        return keyring.get_password(KEYRING_SERVICE, secret_id) or ""
    except Exception:
        logging.warning("Не удалось прочитать пароль из системного хранилища секретов.")
        return ""


def _keyring_delete(secret_id: str) -> None:
    keyring = _keyring_backend()
    if keyring is None or not secret_id:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, secret_id)
    except Exception:
        pass  # best-effort очистка; запись могла отсутствовать (был base64-фолбэк)


def _dpapi_crypt(data: bytes, *, protect: bool) -> bytes | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    func = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN = 0x1
    ok = func(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
    if not ok:
        return None
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def protect_secret(value: str, secret_id: str = "") -> str:
    """Токен-обёртка над паролем. Схемы: `dpapi:` (Windows), `keyring:<id>`
    (секрет в системном хранилище, в токене только ссылка), `b64:` (фолбэк).
    secret_id нужен для keyring — обычно id FTP-подключения."""
    raw = (value or "").encode("utf-8")
    if not raw:
        return ""
    blob = _dpapi_crypt(raw, protect=True)
    if blob is not None:
        return "dpapi:" + base64.b64encode(blob).decode("ascii")
    # Не-Windows: системное хранилище секретов, если доступно и есть id.
    if _keyring_store(secret_id, value):
        return "keyring:" + secret_id
    # Фолбэк: пароль ложится в реестр обратимо (base64) — это не шифрование.
    # Предупреждаем оператора: без DPAPI/keyring секрет по сути открытый (файл
    # прикрыт правами 0600, но не криптографией).
    logging.warning(
        "Пароль панели сохранён обратимым кодированием (base64): DPAPI и системное "
        "хранилище недоступны. Установите keyring для защиты секрета."
    )
    return "b64:" + base64.b64encode(raw).decode("ascii")


def unprotect_secret(token: str) -> str:
    token = str(token or "")
    if not token:
        return ""
    if token.startswith("dpapi:"):
        try:
            blob = base64.b64decode(token[6:])
        except Exception:
            return ""
        raw = _dpapi_crypt(blob, protect=False)
        return raw.decode("utf-8") if raw is not None else ""
    if token.startswith("keyring:"):
        return _keyring_fetch(token[len("keyring:"):])
    if token.startswith("b64:"):
        try:
            return base64.b64decode(token[4:]).decode("utf-8")
        except Exception:
            return ""
    return token  # legacy plaintext
