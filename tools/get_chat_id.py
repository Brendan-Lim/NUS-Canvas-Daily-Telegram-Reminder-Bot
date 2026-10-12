"""Compatibility notice for the retired, unverified chat lookup helper."""

if __name__ == "__main__":
    print(
        "Use python tools/setup_telegram.py --repo OWNER/REPOSITORY for secure automatic pairing. Existing TELEGRAM_CHAT_ID secrets still work."
    )
    raise SystemExit(1)
