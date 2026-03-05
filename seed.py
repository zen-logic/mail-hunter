#!/usr/bin/env python3
"""Seed the database with test servers, folders, and messages."""

import asyncio
import random
from datetime import datetime, timedelta

from mail_hunter.db import get_db, close_db

SERVERS = [
    {
        "name": "Work Email",
        "host": "imap.megacorp.com",
        "port": 993,
        "username": "j.smith@megacorp.com",
        "password": "test",
    },
    {
        "name": "Personal Gmail",
        "host": "imap.gmail.com",
        "port": 993,
        "username": "john.smith42@gmail.com",
        "password": "test",
    },
    {
        "name": "Legacy POP3",
        "host": "pop3.oldmail.net",
        "port": 995,
        "username": "jsmith@oldmail.net",
        "password": "test",
    },
]

IMAP_FOLDERS = ["INBOX", "Sent", "Drafts", "Trash", "Archive", "Junk"]
POP3_FOLDERS = ["INBOX"]

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
]

SUBJECTS = [
    "Q4 Budget Review — Action Required",
    "Re: Project timeline update",
    "Meeting notes from Tuesday",
    "Invoice #4821 attached",
    "Lunch tomorrow?",
    "Quick question about the API",
    "Weekly status report",
    "New office policy — please read",
    "Your order has shipped",
    "Conference registration confirmed",
    "Fw: Interesting article on ML",
    "Reminder: dentist appointment",
    "Can you review this PR?",
    "Team outing next Friday",
    "Re: Re: Database migration plan",
    "Security alert: new login detected",
    "Happy birthday!",
    "Contract renewal — signature needed",
    "Flight itinerary: London to NYC",
    "Re: Feedback on the mockups",
    "Server maintenance window — Saturday 2am",
    "Welcome to the team!",
    "Photo gallery from the event",
    "Quarterly earnings call invite",
    "Your subscription is expiring",
    "Thoughts on the new design?",
    "Re: Holiday schedule",
    "Expense report for March",
    "Job application received",
    "Parking pass renewal",
    "Updated project roadmap attached",
    "Out of office: back Monday",
    "Re: Vendor selection",
    "Board meeting agenda",
    "Customer feedback summary",
    "New feature request from client",
    "Release v2.4.1 — changelog",
    "Training session: Thursday 3pm",
    "Urgent: production issue",
    "Referral bonus — don't miss out",
    "Password reset request",
    "Travel policy update",
    "Sprint retrospective notes",
    "Re: Pricing proposal",
    "Monthly newsletter — March 2026",
    "System upgrade notification",
    "Your refund has been processed",
    "Invitation: product launch event",
    "Performance review scheduled",
    "Backup completed successfully",
]

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
]

ATTACHMENTS = [
    (
        "Q4_Budget_2026.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        245_000,
    ),
    ("invoice_4821.pdf", "application/pdf", 128_000),
    (
        "meeting_notes.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        45_000,
    ),
    ("screenshot.png", "image/png", 890_000),
    ("project_plan.pdf", "application/pdf", 2_100_000),
    ("photo_001.jpg", "image/jpeg", 3_400_000),
    ("report_final.pdf", "application/pdf", 560_000),
    ("contract_v2.pdf", "application/pdf", 312_000),
    ("logo_redesign.svg", "image/svg+xml", 18_000),
    ("backup_log.txt", "text/plain", 4_200),
    (
        "presentation.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        5_600_000,
    ),
    ("data_export.csv", "text/csv", 1_200_000),
]

TAGS = [
    "important",
    "follow-up",
    "personal",
    "finance",
    "project-x",
    "archived",
    "client",
    "urgent",
    "travel",
    "receipts",
]


async def seed():
    db = await get_db()

    # Check if already seeded
    row = await db.execute_fetchall("SELECT COUNT(*) as c FROM servers")
    if row[0]["c"] > 0:
        print("Database already has data. Delete mail_hunter.db to re-seed.")
        return

    now = datetime.now()

    for si, srv in enumerate(SERVERS):
        cursor = await db.execute(
            "INSERT INTO servers (name, host, port, username, password) VALUES (?, ?, ?, ?, ?)",
            (srv["name"], srv["host"], srv["port"], srv["username"], srv["password"]),
        )
        server_id = cursor.lastrowid

        folders = POP3_FOLDERS if "pop3" in srv["host"] else IMAP_FOLDERS
        folder_ids = {}
        for fname in folders:
            cur = await db.execute(
                "INSERT INTO folders (server_id, name) VALUES (?, ?)",
                (server_id, fname),
            )
            folder_ids[fname] = cur.lastrowid

        # Generate 50-70 messages per server
        num_mails = random.randint(50, 70)
        for mi in range(num_mails):
            sender = random.choice(SENDERS)
            subject = random.choice(SUBJECTS)
            body = random.choice(BODY_SNIPPETS)
            folder = random.choice(folders)
            date = now - timedelta(
                days=random.randint(0, 90),
                hours=random.randint(0, 23),
                minutes=random.randint(0, 59),
            )
            size = random.randint(1_200, 8_000_000)
            unread = 1 if random.random() < 0.3 else 0
            has_attachment = random.random() < 0.25
            att_count = random.randint(1, 3) if has_attachment else 0

            cur = await db.execute(
                "INSERT INTO mails (server_id, folder_id, uid, message_id, subject, "
                "from_name, from_addr, to_addr, date, size, unread, body_text, attachment_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    server_id,
                    folder_ids[folder],
                    f"{mi + 1}",
                    f"<{random.randint(100000, 999999)}@{srv['host']}>",
                    subject,
                    sender[0],
                    sender[1],
                    srv["username"],
                    date.isoformat(),
                    size,
                    unread,
                    body,
                    att_count,
                ),
            )
            mail_id = cur.lastrowid

            # Add attachments
            for _ in range(att_count):
                att = random.choice(ATTACHMENTS)
                await db.execute(
                    "INSERT INTO attachments (mail_id, filename, content_type, size) VALUES (?, ?, ?, ?)",
                    (mail_id, att[0], att[1], att[2]),
                )

            # Randomly tag ~15% of messages
            if random.random() < 0.15:
                for tag in random.sample(TAGS, k=random.randint(1, 3)):
                    await db.execute(
                        "INSERT OR IGNORE INTO tags (mail_id, tag) VALUES (?, ?)",
                        (mail_id, tag),
                    )

        print(f"  {srv['name']}: {num_mails} messages across {len(folders)} folders")

    await db.commit()
    print("\nSeed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
    asyncio.run(close_db())
