import aiosqlite


async def recalculate_dup_counts(db: aiosqlite.Connection, message_ids: list[str]):
    """Recalculate dup_count for a batch of message_ids.

    dup_count = total copies sharing the same message_id (or content_hash
    when message_id is empty), minus 1 (so 0 = unique, 1 = one duplicate, etc).
    """
    if not message_ids:
        return

    # Group by message_id — only non-empty ones
    non_empty = [mid for mid in message_ids if mid]
    if non_empty:
        placeholders = ",".join("?" for _ in non_empty)
        await db.execute(
            f"UPDATE mails SET dup_count = ("
            f"  SELECT COUNT(*) - 1 FROM mails m2 "
            f"  WHERE m2.message_id = mails.message_id AND m2.message_id != ''"
            f") WHERE message_id IN ({placeholders}) AND message_id != ''",
            non_empty,
        )

    # For messages with empty message_id, use content_hash
    await db.execute(
        "UPDATE mails SET dup_count = ("
        "  SELECT COUNT(*) - 1 FROM mails m2 "
        "  WHERE m2.content_hash = mails.content_hash"
        ") WHERE (message_id IS NULL OR message_id = '') "
        "AND content_hash IS NOT NULL AND id IN ("
        "  SELECT id FROM mails WHERE (message_id IS NULL OR message_id = '') "
        "  AND content_hash IN ("
        "    SELECT content_hash FROM mails "
        "    WHERE (message_id IS NULL OR message_id = '')"
        "    AND content_hash IS NOT NULL"
        "    GROUP BY content_hash HAVING COUNT(*) > 1"
        "  )"
        ")"
    )

    await db.commit()
