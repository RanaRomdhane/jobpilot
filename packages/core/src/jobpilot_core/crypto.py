"""Encryption at rest for OAuth tokens and other secrets stored in the database."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from jobpilot_core.config import Settings


class SecretBoxError(RuntimeError):
    pass


class SecretBox:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise SecretBoxError("FERNET_KEY is not a valid Fernet key") from exc

    @classmethod
    def from_settings(cls, settings: Settings) -> SecretBox:
        if settings.fernet_key is None:
            raise SecretBoxError("FERNET_KEY is not set (run `make keys`)")
        return cls(settings.fernet_key.get_secret_value())

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(token).decode()
        except InvalidToken as exc:
            raise SecretBoxError("cannot decrypt: wrong FERNET_KEY or corrupted data") from exc


def generate_key() -> str:
    return Fernet.generate_key().decode()
