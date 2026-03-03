import email
import email.policy
import hashlib
from pathlib import Path

from mail_hunter.config import load_config

_archive_root: Path | None = None


def _get_archive_root() -> Path:
    global _archive_root
    if _archive_root is None:
        config = load_config()
        archive = config.get("archive_path", "archive")
        root = Path(archive)
        if not root.is_absolute():
            root = Path(__file__).resolve().parent.parent.parent / root
        _archive_root = root
    return _archive_root


def _hash_path(sha: str) -> Path:
    """Return archive path for a given SHA-256 hash."""
    root = _get_archive_root()
    return root / sha[:2] / sha[2:4] / f"{sha}.eml"


def store_message(raw_bytes: bytes) -> tuple[str, str]:
    """Store raw EML bytes in content-addressable archive.

    Returns (sha256_hash, path_str). Idempotent — skips write if file exists.
    """
    sha = hashlib.sha256(raw_bytes).hexdigest()
    path = _hash_path(sha)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw_bytes)
    return sha, str(path)


def read_message(sha: str) -> bytes:
    """Read raw EML bytes from archive by hash."""
    path = _hash_path(sha)
    return path.read_bytes()


def extract_attachment_from_path(raw_path: str, index: int) -> tuple[str, str, bytes]:
    """Extract an attachment from an archived message by file path and index.

    Returns (filename, content_type, data).
    """
    raw = Path(raw_path).read_bytes()
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    idx = 0
    for part in msg.walk():
        content_disposition = part.get("Content-Disposition", "")
        if "attachment" in content_disposition or (
            part.get_content_maintype() not in ("text", "multipart")
            and part.get_filename()
        ):
            if idx == index:
                filename = part.get_filename() or "untitled"
                content_type = part.get_content_type()
                data = part.get_payload(decode=True) or b""
                return filename, content_type, data
            idx += 1
    raise IndexError(f"Attachment index {index} not found")
