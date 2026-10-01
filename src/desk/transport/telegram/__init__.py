"""Telegram Bot API — owner-approved TEST transport (not a product channel; WhatsApp is).

Long polling only (getUpdates): no public endpoint, no new HTTP route. The bot token is
a full-control secret: it lives in the URL of every Bot API call, so URLs are never
logged (see desk.logsafe) and the token is never stored, echoed or committed.
"""
