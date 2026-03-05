#!/usr/bin/env python3
"""Generate a demo database with ~5,000 realistic fictional messages.

Writes to mail_hunter_demo.db — never touches the production database.
"""

import asyncio
import hashlib
import random
from datetime import datetime, timedelta

import aiosqlite

from mail_hunter.db import SCHEMA, MIGRATIONS, SYNC_STATE_TABLE, SYNC_QUEUE_TABLE, INDEXES
from mail_hunter.services.dedup import recalculate_dup_counts

DB_PATH = "mail_hunter_demo.db"

# ── Servers ──────────────────────────────────────────────────────────────

SERVERS = [
    {
        "name": "Acme Corp",
        "host": "imap.acmecorp.com",
        "port": 993,
        "username": "j.reynolds@acmecorp.com",
        "password": "demo",
        "is_gmail": 0,
        "target": 2200,
        "folders": [
            "INBOX", "Sent", "Drafts", "Trash", "Archive", "Junk",
            "Projects/Alpha", "Projects/Beta", "Projects/Gamma",
        ],
    },
    {
        "name": "Personal Gmail",
        "host": "imap.gmail.com",
        "port": 993,
        "username": "jamie.reynolds42@gmail.com",
        "password": "demo",
        "is_gmail": 1,
        "target": 1400,
        "folders": [
            "INBOX", "Sent", "[Gmail]/All Mail", "[Gmail]/Starred",
            "Receipts", "Travel", "Family",
        ],
    },
    {
        "name": "Northwind Consulting",
        "host": "imap.northwind.io",
        "port": 993,
        "username": "jamie@northwind.io",
        "password": "demo",
        "is_gmail": 0,
        "target": 900,
        "folders": [
            "INBOX", "Sent", "Drafts",
            "Clients/Meridian", "Clients/Solaris", "Clients/Apex",
            "Invoices",
        ],
    },
    {
        "name": "University Archive",
        "host": "imap.bridgeford.ac.uk",
        "port": 993,
        "username": "jr2019@bridgeford.ac.uk",
        "password": "demo",
        "is_gmail": 0,
        "target": 500,
        "folders": [
            "INBOX", "Sent", "Courses/CS301", "Courses/CS450", "Admin",
        ],
    },
]

# ── Senders ──────────────────────────────────────────────────────────────

SENDERS = [
    ("Alice Cooper", "alice.cooper@example.com"),
    ("Bob Martinez", "bob.martinez@widgets.io"),
    ("Carol Zhang", "carol.zhang@techstart.dev"),
    ("Dave Patel", "dave@freelance.me"),
    ("Emily Watson", "e.watson@university.edu"),
    ("Frank Okafor", "frank.okafor@bigbank.com"),
    ("Grace Kim", "grace.kim@design.studio"),
    ("Henry Liu", "henry.liu@cloudops.net"),
    ("Irene Novak", "irene@consulting.group"),
    ("James Brown", "j.brown@logistics.co"),
    ("Karen White", "karen.white@healthcare.org"),
    ("Lars Eriksson", "lars@nordic-tech.se"),
    ("Maria Santos", "maria.santos@travel.com"),
    ("Nate Thompson", "nate.t@devshop.io"),
    ("Olivia Chen", "olivia.chen@research.ac.uk"),
    ("Paul Dubois", "paul.dubois@eu-corp.fr"),
    ("Quinn Murphy", "quinn@startup.vc"),
    ("Rachel Green", "rachel.green@media.com"),
    ("Sam Wilson", "sam.wilson@engineering.co"),
    ("Tina Rossi", "tina.rossi@fashion.it"),
    ("Derek Marsh", "derek.marsh@finserv.com"),
    ("Priya Sharma", "priya.sharma@datavault.in"),
    ("Oscar Reyes", "oscar.reyes@buildright.mx"),
    ("Fiona Gallagher", "fiona.g@legaledge.ie"),
    ("Tomasz Kowalski", "tomasz.k@infraworks.pl"),
    ("Sophie Laurent", "sophie.laurent@arthaus.fr"),
    ("Raj Anand", "raj.anand@quantumleap.co"),
    ("Nina Petrova", "nina.petrova@energex.ru"),
    ("William Osei", "w.osei@greengrid.gh"),
    ("Hannah Berg", "hannah.berg@medisync.de"),
    ("Liam Fitzgerald", "liam.fitz@codemill.io"),
    ("Yuki Tanaka", "yuki.tanaka@nexwave.jp"),
]

# ── Subjects ─────────────────────────────────────────────────────────────

SUBJECTS = [
    # Work / admin
    "Q4 Budget Review — Action Required",
    "Re: Project timeline update",
    "Meeting notes from Tuesday",
    "Invoice #4821 attached",
    "Quick question about the API",
    "Weekly status report",
    "New office policy — please read",
    "Conference registration confirmed",
    "Fw: Interesting article on ML",
    "Can you review this PR?",
    "Team outing next Friday",
    "Re: Re: Database migration plan",
    "Security alert: new login detected",
    "Contract renewal — signature needed",
    "Re: Feedback on the mockups",
    "Server maintenance window — Saturday 2am",
    "Welcome to the team!",
    "Quarterly earnings call invite",
    "Thoughts on the new design?",
    "Expense report for March",
    "Updated project roadmap attached",
    "Out of office: back Monday",
    "Re: Vendor selection",
    "Board meeting agenda",
    "Customer feedback summary",
    "New feature request from client",
    "Release v2.4.1 — changelog",
    "Training session: Thursday 3pm",
    "Urgent: production issue",
    "Sprint retrospective notes",
    "Re: Pricing proposal",
    "System upgrade notification",
    "Performance review scheduled",
    "Backup completed successfully",
    # Personal
    "Lunch tomorrow?",
    "Your order has shipped",
    "Reminder: dentist appointment",
    "Happy birthday!",
    "Flight itinerary: London to NYC",
    "Photo gallery from the event",
    "Your subscription is expiring",
    "Re: Holiday schedule",
    "Parking pass renewal",
    "Your refund has been processed",
    "Invitation: product launch event",
    "Password reset request",
    "Travel policy update",
    "Monthly newsletter — March 2026",
    "Referral bonus — don't miss out",
    "Job application received",
    # Client / consulting
    "Meridian project kickoff — agenda",
    "Solaris Q1 deliverables review",
    "Apex integration: API credentials",
    "Re: Meridian UAT sign-off",
    "Invoice #NW-0073 — Solaris retainer",
    "Scope change request — Apex phase 2",
    "Clients dinner: Thursday 7pm",
    "NDA countersigned — attached",
    "Re: Apex go-live checklist",
    "Northwind capacity planning",
    # University / academic
    "CS301 Assignment 3 — due Friday",
    "CS450 lecture slides posted",
    "Re: Group project roles",
    "Library account renewal",
    "Exam timetable published",
    "Alumni newsletter — Winter 2025",
    "Transcript request confirmation",
    "Campus Wi-Fi maintenance notice",
    "Graduation ceremony details",
    "Re: Research assistant position",
    # Finance / legal
    "Wire transfer confirmation",
    "Tax return documents ready",
    "Insurance renewal — action needed",
    "Re: Lease agreement amendments",
    "Payroll adjustment notification",
    "Audit documentation request",
    "Compliance training reminder",
    "Re: Escrow release schedule",
    "Annual report — draft for review",
    "Regulatory filing deadline — March 15",
    # Tech
    "CI pipeline failure — main branch",
    "Dependency update: security patch",
    "Re: Kubernetes cluster scaling",
    "Terraform plan review needed",
    "Incident postmortem — Feb 28",
    "Grafana alert: high latency",
]

# ── Body snippets ────────────────────────────────────────────────────────

BODY_SNIPPETS = [
    "Hi team,\n\nPlease find the updated figures attached. Let me know if you have any questions.\n\nBest regards",
    "Thanks for getting back to me so quickly. I've reviewed the changes and everything looks good. Let's proceed with the next phase.",
    "Just a quick reminder that the deadline is this Friday. Please make sure all deliverables are submitted by end of day.",
    "I wanted to follow up on our conversation from last week. Have you had a chance to look into the pricing options?",
    "The server migration is scheduled for this weekend. Please save all your work before Saturday morning.",
    "Congratulations on the successful launch! The metrics are looking really promising — traffic is up 40% from last month.",
    "I've attached the signed contract for your records. Please confirm receipt at your earliest convenience.",
    "Could you send me the latest version of the spreadsheet? The one I have seems to be outdated.",
    "We're planning a team dinner next Thursday at 7pm. Let me know if you can make it!",
    "The client has requested some changes to the proposal. I've outlined the key points below for discussion.",
    "FYI — the CI pipeline is broken on main. Looks like a flaky test. I'll take a look after lunch.",
    "Thanks for the referral! I spoke with them yesterday and it went really well. Interview is next week.",
    "Please review the attached document and provide your feedback by Wednesday. We need to finalize before the board meeting.",
    "The annual report is ready for your sign-off. I've highlighted the sections that need your attention.",
    "Reminder: mandatory security training must be completed by March 31st. Link below.",
    "Quick update — the vendor confirmed delivery for next Tuesday. I'll coordinate with the warehouse team.",
    "Apologies for the delay. I was out sick last week. Catching up now and will have the report to you by EOD.",
    "Great news — the client approved the revised scope. We can start phase 2 immediately.",
    "I've set up the staging environment. Here are the credentials. Please test and let me know if anything looks off.",
    "Per our discussion, I've drafted the SOW. Attached for your review. Happy to jump on a call to walk through it.",
    "Heads up — there's a known issue with the payment gateway today. Engineering is on it. ETA for fix is 4pm.",
    "Thanks for your patience during the onboarding process. Your accounts are now fully provisioned.",
    "The research paper is ready for submission. I've incorporated all the reviewer comments from the last round.",
    "Please note the change of venue for Friday's meeting. We'll be in Conference Room B instead.",
]

# ── Attachments ──────────────────────────────────────────────────────────

ATTACHMENTS = [
    ("Q4_Budget_2026.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 245_000),
    ("invoice_4821.pdf", "application/pdf", 128_000),
    ("meeting_notes.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 45_000),
    ("screenshot.png", "image/png", 890_000),
    ("project_plan.pdf", "application/pdf", 2_100_000),
    ("photo_001.jpg", "image/jpeg", 3_400_000),
    ("report_final.pdf", "application/pdf", 560_000),
    ("contract_v2.pdf", "application/pdf", 312_000),
    ("logo_redesign.svg", "image/svg+xml", 18_000),
    ("backup_log.txt", "text/plain", 4_200),
    ("presentation.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", 5_600_000),
    ("data_export.csv", "text/csv", 1_200_000),
    ("architecture_diagram.png", "image/png", 1_450_000),
    ("NDA_signed.pdf", "application/pdf", 198_000),
    ("timesheet_march.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 67_000),
    ("headshot.jpg", "image/jpeg", 2_800_000),
    ("release_notes.md", "text/markdown", 12_000),
    ("database_schema.sql", "text/plain", 8_500),
    ("client_brief.pdf", "application/pdf", 430_000),
    ("wireframes_v3.fig", "application/octet-stream", 7_200_000),
]

# ── Tags ─────────────────────────────────────────────────────────────────

TAGS = [
    "important", "follow-up", "personal", "finance", "project-x",
    "archived", "client", "urgent", "travel", "receipts",
    "review", "waiting", "NDA", "confidential",
]


# ── Helpers ──────────────────────────────────────────────────────────────

def make_content_hash(from_addr: str, to_addr: str, date: str, subject: str, body: str) -> str:
    """SHA-256(from_addr|to_addr|date|subject|body_text)."""
    payload = f"{from_addr}|{to_addr}|{date}|{subject}|{body}"
    return hashlib.sha256(payload.encode()).hexdigest()


def make_message_id(ts: int, host: str) -> str:
    """<TIMESTAMP.RANDOM@host>."""
    return f"<{ts}.{random.randint(100000, 999999)}@{host}>"


def random_size() -> int:
    """Realistic email size distribution: most 2-15KB, occasional large."""
    r = random.random()
    if r < 0.70:
        return random.randint(2_000, 15_000)
    if r < 0.90:
        return random.randint(15_000, 150_000)
    if r < 0.97:
        return random.randint(150_000, 1_500_000)
    return random.randint(1_500_000, 8_000_000)


# ── Main ─────────────────────────────────────────────────────────────────

async def seed():
    import os

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"Removed existing {DB_PATH}")

    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute("PRAGMA busy_timeout=30000")

    # Create schema
    for statement in SCHEMA.split(";"):
        stmt = statement.strip()
        if stmt:
            await db.execute(stmt)
    for _table, _col, sql in MIGRATIONS:
        try:
            await db.execute(sql)
        except Exception:
            pass
    await db.execute(SYNC_STATE_TABLE)
    await db.execute(SYNC_QUEUE_TABLE)
    for idx_sql in INDEXES:
        await db.execute(idx_sql)
    await db.commit()

    now = datetime.now()
    two_years_ago = now - timedelta(days=730)
    total_seconds = int((now - two_years_ago).total_seconds())
    last_sync = (now - timedelta(hours=2)).isoformat()

    all_mail_ids: list[int] = []          # every mail row id
    dup_message_ids: list[str] = []       # message_ids inserted as duplicates
    grand_total = 0

    for srv in SERVERS:
        # Insert server
        cursor = await db.execute(
            "INSERT INTO servers (name, host, port, username, password, is_gmail, last_sync) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (srv["name"], srv["host"], srv["port"], srv["username"],
             srv["password"], srv["is_gmail"], last_sync),
        )
        server_id = cursor.lastrowid

        # Insert folders
        folder_ids: dict[str, int] = {}
        for fname in srv["folders"]:
            cur = await db.execute(
                "INSERT INTO folders (server_id, name) VALUES (?, ?)",
                (server_id, fname),
            )
            folder_ids[fname] = cur.lastrowid

        # Weighted folder distribution — INBOX and Sent get most mail
        folder_weights: list[tuple[str, float]] = []
        for fname in srv["folders"]:
            if fname == "INBOX":
                folder_weights.append((fname, 0.35))
            elif fname == "Sent":
                folder_weights.append((fname, 0.20))
            elif fname in ("Drafts", "Trash", "Junk"):
                folder_weights.append((fname, 0.03))
            elif fname.startswith("[Gmail]"):
                folder_weights.append((fname, 0.05))
            else:
                folder_weights.append((fname, 0.10))
        # Normalise
        total_weight = sum(w for _, w in folder_weights)
        folder_weights = [(f, w / total_weight) for f, w in folder_weights]

        target = srv["target"]
        uid_counter = 1

        # Pre-pick which indices will be duplicates (~5% of target)
        dup_count_target = int(target * 0.05)
        # We'll insert target messages, then duplicate dup_count_target of them
        # So total for this server = target + dup_count_target

        mail_rows: list[tuple] = []
        mail_meta: list[dict] = []  # parallel list for post-insert work

        for _ in range(target):
            sender = random.choice(SENDERS)
            subject = random.choice(SUBJECTS)
            body = random.choice(BODY_SNIPPETS)

            # Weighted folder pick
            r = random.random()
            cumulative = 0.0
            folder = srv["folders"][0]
            for fname, w in folder_weights:
                cumulative += w
                if r <= cumulative:
                    folder = fname
                    break

            offset_seconds = random.randint(0, total_seconds)
            date = two_years_ago + timedelta(seconds=offset_seconds)
            date_str = date.isoformat()

            size = random_size()
            # Unread ~15%, primarily in INBOX
            if folder == "INBOX":
                unread = 1 if random.random() < 0.25 else 0
            else:
                unread = 1 if random.random() < 0.05 else 0

            has_att = random.random() < 0.30
            att_count = random.randint(1, 4) if has_att else 0

            ts = int(date.timestamp())
            message_id = make_message_id(ts, srv["host"])
            content_hash = make_content_hash(
                sender[1], srv["username"], date_str, subject, body,
            )

            mail_rows.append((
                server_id, folder_ids[folder], str(uid_counter), message_id,
                subject, sender[0], sender[1], srv["username"], date_str,
                size, unread, att_count, body, content_hash,
            ))
            mail_meta.append({
                "att_count": att_count,
                "message_id": message_id,
                "folder": folder,
            })
            uid_counter += 1

        # Bulk insert messages
        insert_sql = (
            "INSERT INTO mails (server_id, folder_id, uid, message_id, subject, "
            "from_name, from_addr, to_addr, date, size, unread, attachment_count, "
            "body_text, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        await db.executemany(insert_sql, mail_rows)
        await db.commit()

        # Get the inserted IDs (they're sequential)
        row = await db.execute_fetchall(
            "SELECT id FROM mails WHERE server_id = ? ORDER BY id", (server_id,),
        )
        server_mail_ids = [r["id"] for r in row]
        all_mail_ids.extend(server_mail_ids)

        # ── Duplicates ───────────────────────────────────────────────
        dup_source_indices = random.sample(range(len(mail_rows)), min(dup_count_target, len(mail_rows)))
        dup_rows = []
        for idx in dup_source_indices:
            orig = mail_rows[idx]
            # Same message_id, different uid, possibly different folder
            other_folders = [f for f in srv["folders"] if f != mail_meta[idx]["folder"]]
            dup_folder = random.choice(other_folders) if other_folders else mail_meta[idx]["folder"]
            dup_rows.append((
                server_id, folder_ids[dup_folder], str(uid_counter),
                orig[3],  # same message_id
                orig[4], orig[5], orig[6], orig[7], orig[8],
                orig[9], orig[10], orig[11], orig[12], orig[13],
            ))
            dup_message_ids.append(orig[3])
            uid_counter += 1
        if dup_rows:
            await db.executemany(insert_sql, dup_rows)
            await db.commit()

        # Get all IDs for this server again (including dups)
        row = await db.execute_fetchall(
            "SELECT id FROM mails WHERE server_id = ? ORDER BY id", (server_id,),
        )
        server_all_ids = [r["id"] for r in row]
        # Add the dup IDs that weren't in the first batch
        dup_ids = server_all_ids[len(server_mail_ids):]
        all_mail_ids.extend(dup_ids)

        server_total = len(server_all_ids)
        grand_total += server_total
        print(f"  {srv['name']}: {server_total} messages across {len(srv['folders'])} folders")

        # ── Attachments (~30% of messages have them) ─────────────────
        att_rows = []
        for i, mail_id in enumerate(server_mail_ids):
            att_count = mail_meta[i]["att_count"]
            for _ in range(att_count):
                att = random.choice(ATTACHMENTS)
                att_rows.append((mail_id, att[0], att[1], att[2]))
        # Dup messages also get attachments matching their originals
        for j, idx in enumerate(dup_source_indices):
            att_count = mail_meta[idx]["att_count"]
            dup_mail_id = dup_ids[j] if j < len(dup_ids) else None
            if dup_mail_id:
                for _ in range(att_count):
                    att = random.choice(ATTACHMENTS)
                    att_rows.append((dup_mail_id, att[0], att[1], att[2]))
        if att_rows:
            await db.executemany(
                "INSERT INTO attachments (mail_id, filename, content_type, size) VALUES (?, ?, ?, ?)",
                att_rows,
            )
            await db.commit()

        # ── Legal holds (~3%) ────────────────────────────────────────
        hold_count = int(len(server_all_ids) * 0.03)
        hold_ids = random.sample(server_all_ids, min(hold_count, len(server_all_ids)))
        if hold_ids:
            placeholders = ",".join("?" for _ in hold_ids)
            await db.execute(
                f"UPDATE mails SET legal_hold = 1 WHERE id IN ({placeholders})",
                hold_ids,
            )
            await db.commit()

        # ── Tags (~20%) ─────────────────────────────────────────────
        tag_count = int(len(server_all_ids) * 0.20)
        tag_ids = random.sample(server_all_ids, min(tag_count, len(server_all_ids)))
        tag_rows = []
        for mail_id in tag_ids:
            for tag in random.sample(TAGS, k=random.randint(1, 3)):
                tag_rows.append((mail_id, tag))
        if tag_rows:
            await db.executemany(
                "INSERT OR IGNORE INTO tags (mail_id, tag) VALUES (?, ?)",
                tag_rows,
            )
            await db.commit()

    # ── Recalculate dup counts ───────────────────────────────────────
    if dup_message_ids:
        unique_dup_mids = list(set(dup_message_ids))
        await recalculate_dup_counts(db, unique_dup_mids)

    await db.close()
    print(f"\nTotal: {grand_total} messages in {DB_PATH}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(seed())
