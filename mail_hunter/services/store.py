import email
import email.policy
import hashlib
from pathlib import Path

import zstandard as zstd

from mail_hunter.config import load_config

_archive_root: Path | None = None
_compressor = zstd.ZstdCompressor(level=19)
_decompressor = zstd.ZstdDecompressor()


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
    """Return archive path for a given SHA-256 hash (new zstd format)."""
    root = _get_archive_root()
    return root / sha[:2] / sha[2:4] / f"{sha}.eml.zst"


def _legacy_hash_path(sha: str) -> Path:
    """Return legacy uncompressed archive path."""
    root = _get_archive_root()
    return root / sha[:2] / sha[2:4] / f"{sha}.eml"


def store_message(raw_bytes: bytes) -> tuple[str, str]:
    """Store raw EML bytes in content-addressable archive.

    Returns (sha256_hash, path_str). Idempotent — skips write if file exists.
    New files are compressed with zstd. Legacy uncompressed files are recognised.
    """
    sha = hashlib.sha256(raw_bytes).hexdigest()
    path = _hash_path(sha)
    legacy = _legacy_hash_path(sha)
    if path.exists() or legacy.exists():
        # Return whichever exists (prefer zstd)
        return sha, str(path if path.exists() else legacy)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_compressor.compress(raw_bytes))
    return sha, str(path)


def read_raw(raw_path: str) -> bytes:
    """Read raw EML bytes from an archive path, decompressing if needed."""
    p = Path(raw_path)
    data = p.read_bytes()
    if p.suffix == ".zst":
        return _decompressor.decompress(data)
    return data


def read_message(sha: str) -> bytes:
    """Read raw EML bytes from archive by hash."""
    path = _hash_path(sha)
    if path.exists():
        return _decompressor.decompress(path.read_bytes())
    return _legacy_hash_path(sha).read_bytes()


def extract_attachment_from_path(raw_path: str, index: int) -> tuple[str, str, bytes]:
    """Extract an attachment from an archived message by file path and index.

    Returns (filename, content_type, data).
    """
    raw = read_raw(raw_path)
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
