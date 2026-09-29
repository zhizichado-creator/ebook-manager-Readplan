"""Store API credentials in the operating system credential vault."""
from __future__ import annotations


def set_secret(service: str, username: str, secret: str) -> None:
    try:
        import keyring
    except ImportError as error:
        raise RuntimeError("未安装安全凭据存储依赖 keyring") from error
    try:
        keyring.set_password(service, username, secret)
    except Exception as error:
        raise RuntimeError(f"系统凭据库拒绝保存 API Key：{error}") from error


def get_secret(service: str, username: str) -> str:
    try:
        import keyring
    except Exception:
        return ""
    try:
        return keyring.get_password(service, username) or ""
    except Exception:
        return ""


def delete_secret(service: str, username: str) -> None:
    try:
        import keyring
        keyring.delete_password(service, username)
    except Exception:
        return
