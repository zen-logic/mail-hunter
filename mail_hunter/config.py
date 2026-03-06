import json
from pathlib import Path

from cryptography.fernet import Fernet

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

_fernet = None


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        config = {
            "host": "127.0.0.1",
            "port": 8700,
            "database": "mail_hunter.db",
            "archive_path": "archive",
        }
        with open(path, "w") as f:
            json.dump(config, f, indent=2)
        return config


def _load_or_create_key(path: Path = DEFAULT_CONFIG_PATH) -> bytes:
    """Read encryption_key from config.json; generate + write back if missing."""
    try:
        with open(path) as f:
            config = json.load(f)
    except FileNotFoundError:
        config = {}

    key = config.get("encryption_key")
    if key:
        return key.encode()

    key = Fernet.generate_key()
    config["encryption_key"] = key.decode()
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt_password(plaintext: str) -> str:
    """Encrypt a password. Returns empty string unchanged."""
    if not plaintext:
        return plaintext
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_password(ciphertext: str) -> str:
    """Decrypt a password. Returns empty string unchanged.

    Raises InvalidToken if ciphertext is not valid Fernet data.
    """
    if not ciphertext:
        return ciphertext
    return _get_fernet().decrypt(ciphertext.encode()).decode()
