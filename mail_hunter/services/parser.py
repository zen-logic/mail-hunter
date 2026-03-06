import email
import email.policy
import email.utils
import hashlib
import html
import re
from datetime import timezone


def parse_message(raw_bytes: bytes) -> dict:
    """Parse raw EML bytes into a structured dict."""
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)

    # email.policy.default parses structured headers and can raise
    # ValueError on malformed data (e.g. CR/LF in address parts).
    # Fall back to raw string with CR/LF stripped.
    def _hdr(name, default=""):
        try:
            return msg.get(name, default)
        except (ValueError, AttributeError):
            for key, val in msg.raw_items():
                if key.lower() == name.lower():
                    return re.sub(r"[\r\n]+", " ", val).strip()
            return default

    message_id = _hdr("Message-ID").strip()
    subject = _hdr("Subject")
    from_header = _hdr("From")
    to_header = _hdr("To")
    cc_header = _hdr("Cc")
    reply_to = _hdr("Reply-To")
    in_reply_to = _hdr("In-Reply-To").strip()
    references = _hdr("References").strip()
    date_header = _hdr("Date")

    # Parse from name + addr
    from_name, from_addr = email.utils.parseaddr(from_header)

    # Parse date — fall back to most recent Received header if Date is broken
    date_iso = None
    date_candidates = [date_header] if date_header else []
    try:
        received_headers = msg.get_all("Received", [])
    except ValueError:
        received_headers = []
    for received in received_headers:
        # Received headers have a semicolon before the date portion
        if ";" in received:
            date_candidates.append(received.split(";", 1)[1].strip())
    for candidate in date_candidates:
        try:
            dt = email.utils.parsedate_to_datetime(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            date_iso = dt.isoformat()
            break
        except (ValueError, TypeError):
            continue

    # Extract body text
    body_text = _extract_body(msg)

    # Extract attachments metadata
    attachments = _extract_attachments(msg)
    attachment_count = len(attachments)

    # Content hash for dedup fallback (SHA-256 of from+to+date+subject+body)
    hash_input = f"{from_addr}|{to_header}|{date_header}|{subject}|{body_text or ''}"
    content_hash = hashlib.sha256(
        hash_input.encode("utf-8", errors="replace")
    ).hexdigest()

    return {
        "message_id": message_id,
        "subject": subject,
        "from_name": from_name,
        "from_addr": from_addr,
        "to_addr": to_header,
        "cc_addr": cc_header,
        "reply_to": reply_to,
        "in_reply_to": in_reply_to,
        "references_header": references,
        "date": date_iso,
        "body_text": body_text,
        "attachment_count": attachment_count,
        "attachments": attachments,
        "content_hash": content_hash,
        "size": len(raw_bytes),
    }


def _extract_body_html(msg) -> str | None:
    """Extract HTML body if available."""
    html_part = msg.get_body(preferencelist=("html",))
    if html_part:
        try:
            return html_part.get_content()
        except Exception:
            pass
    return None


def _extract_body(msg) -> str | None:
    """Extract body text — prefer text/plain, fallback to stripped HTML."""
    # Try text/plain first
    plain = msg.get_body(preferencelist=("plain",))
    if plain:
        try:
            return plain.get_content()
        except Exception:
            pass

    # Fallback: text/html stripped of tags
    html_part = msg.get_body(preferencelist=("html",))
    if html_part:
        try:
            raw_html = html_part.get_content()
            return _strip_html(raw_html)
        except Exception:
            pass

    return None


def _strip_html(raw_html: str) -> str:
    """Strip HTML tags and decode entities to plain text."""
    # Remove style and script blocks
    text = re.sub(
        r"<(style|script)[^>]*>.*?</\1>", "", raw_html, flags=re.DOTALL | re.IGNORECASE
    )
    # Replace br/p with newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode entities
    text = html.unescape(text)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_attachments(msg) -> list[dict]:
    """Extract attachment metadata (no content)."""
    attachments = []
    for part in msg.walk():
        content_disposition = part.get("Content-Disposition", "")
        if "attachment" in content_disposition or (
            part.get_content_maintype() not in ("text", "multipart")
            and part.get_filename()
        ):
            filename = part.get_filename() or "untitled"
            content_type = part.get_content_type()
            data = part.get_payload(decode=True)
            size = len(data) if data else 0
            attachments.append(
                {
                    "filename": filename,
                    "content_type": content_type,
                    "size": size,
                }
            )
    return attachments
