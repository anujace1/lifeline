"""Reads unread emails from an inbox (IMAP) and forwards them to Lifeline.
Run while the Lifeline server is running:   python channels/email_poll.py
Set: IMAP_HOST (e.g. imap.gmail.com), IMAP_USER, IMAP_PASSWORD (an app password), LIFELINE_URL (default http://localhost:8000)
"""

import email
import base64
import imaplib
import json
import os
import time
import urllib.request
from email.header import decode_header, make_header
from email.utils import parseaddr

API = os.getenv("LIFELINE_URL", "http://localhost:8000")


def body_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                return part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", "replace"
                )
        return ""
    return (msg.get_payload(decode=True) or b"").decode(
        msg.get_content_charset() or "utf-8", "replace"
    )


def to_payload(msg):
    subject = str(make_header(decode_header(msg.get("Subject", ""))))
    text = (subject + "\n" + body_text(msg)).strip()[
        :2000
    ]  # cap length: emails can be huge
    return {
        "channel": "email",
        "sender": parseaddr(msg.get("From", ""))[1],
        "text": text,
    }


def post(payload):
    headers = {"Content-Type": "application/json"}
    if os.getenv(
        "LIFELINE_PASSWORD"
    ):  # needed when the hosted dashboard is password protected
        headers["Authorization"] = (
            "Basic "
            + base64.b64encode(
                b"lifeline:" + os.environ["LIFELINE_PASSWORD"].encode()
            ).decode()
        )
    req = urllib.request.Request(
        API + "/api/ingest", json.dumps(payload).encode(), headers
    )
    return json.load(urllib.request.urlopen(req, timeout=60))


def poll_once():
    box = imaplib.IMAP4_SSL(os.environ["IMAP_HOST"])
    box.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
    box.select("INBOX")
    _, ids = box.search(None, "UNSEEN")
    for i in ids[0].split():
        _, data = box.fetch(
            i, "(RFC822)"
        )  # fetching marks the email as read, so it is not processed twice
        payload = to_payload(email.message_from_bytes(data[0][1]))
        print(
            "forwarding email from",
            payload["sender"],
            "->",
            post(payload).get("alert") and "alert created" or "logged",
        )
    box.logout()


if __name__ == "__main__":
    while True:
        try:
            poll_once()
        except Exception as e:
            print("poll failed:", e)
        time.sleep(30)
