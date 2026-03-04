#!/usr/bin/env python3
"""One-off script to compress existing .eml archive files to .eml.zst
and update raw_path in the database.

Run with the server stopped:
    conda run -n mail-hunter python compress_archive.py
"""

import sqlite3
from pathlib import Path

import zstandard as zstd

from mail_hunter.config import load_config

ZSTD_LEVEL = 19


def main():
    config = load_config()

    # Resolve archive root
    archive = config.get("archive_path", "archive")
    root = Path(archive)
    if not root.is_absolute():
        root = Path(__file__).resolve().parent / root

    # Resolve DB path
    db_path = config.get("db_path", "mail_hunter.db")
    db_file = Path(db_path)
    if not db_file.is_absolute():
        db_file = Path(__file__).resolve().parent / db_file

    print(f"Archive root: {root}")
    print(f"Database: {db_file}")

    # Find all uncompressed .eml files
    eml_files = list(root.rglob("*.eml"))
    if not eml_files:
        print("No uncompressed .eml files found.")
        return

    print(f"Found {len(eml_files)} .eml files to compress.")

    compressor = zstd.ZstdCompressor(level=ZSTD_LEVEL)
    compressed = 0
    bytes_before = 0
    bytes_after = 0

    db = sqlite3.connect(str(db_file))
    db.execute("PRAGMA journal_mode=WAL")

    for eml_path in eml_files:
        zst_path = eml_path.with_suffix(".eml.zst")
        if zst_path.exists():
            # Already has a compressed copy — skip
            continue

        raw = eml_path.read_bytes()
        data = compressor.compress(raw)
        zst_path.write_bytes(data)

        # Verify round-trip before removing original
        decompressor = zstd.ZstdDecompressor()
        if decompressor.decompress(zst_path.read_bytes()) != raw:
            print(f"  VERIFY FAILED: {eml_path} — keeping original")
            zst_path.unlink()
            continue

        # Update DB rows that reference this path
        old_path = str(eml_path)
        new_path = str(zst_path)
        db.execute(
            "UPDATE mails SET raw_path = ? WHERE raw_path = ?",
            (new_path, old_path),
        )

        eml_path.unlink()
        compressed += 1
        bytes_before += len(raw)
        bytes_after += len(data)

        if compressed % 500 == 0:
            db.commit()
            print(f"  {compressed}/{len(eml_files)} compressed...")

    db.commit()
    db.close()

    print(f"\nDone. Compressed {compressed} files.")
    if bytes_before:
        ratio = (1 - bytes_after / bytes_before) * 100
        print(f"  {bytes_before:,} -> {bytes_after:,} bytes ({ratio:.1f}% reduction)")


if __name__ == "__main__":
    main()
