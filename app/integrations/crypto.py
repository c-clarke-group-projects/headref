"""Encryption for connection tokens stored in PostgreSQL (DEC-023).

Fernet (AES-128-CBC with HMAC-SHA256) with the key from CREDENTIAL_ENCRYPTION_KEY. The key
only ever comes from the environment. Decrypted values are returned to the caller inside
app/integrations/ and never logged.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class CredentialError(RuntimeError):
    """A stored credential cannot be decrypted: wrong or rotated key, or corrupted data."""


def _fernet() -> Fernet:
    key = get_settings().credential_encryption_key
    if not key:
        raise CredentialError("CREDENTIAL_ENCRYPTION_KEY is not set")
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise CredentialError("CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key") from exc


def encrypt_secret(plain: str) -> str:
    """Encrypt a token for storage."""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(stored: str) -> str:
    """Decrypt a stored token. Raises CredentialError if it cannot be read."""
    try:
        return _fernet().decrypt(stored.encode()).decode()
    except InvalidToken as exc:
        raise CredentialError("A stored credential could not be decrypted") from exc
