"""Create `.env` from `.env.example` with generated local secrets (never overwrites `.env`)."""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    target = ROOT / ".env"
    if target.exists():
        print(".env already exists; leaving it untouched.")
        return 0
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    password = secrets.token_urlsafe(24)
    text = re.sub(r"^POSTGRES_PASSWORD=.*$", f"POSTGRES_PASSWORD={password}", text, flags=re.M)
    text = text.replace("jobpilot:change-me@", f"jobpilot:{password}@")
    fernet_key = Fernet.generate_key().decode()
    text = re.sub(r"^FERNET_KEY=.*$", f"FERNET_KEY={fernet_key}", text, flags=re.M)
    target.write_text(text, encoding="utf-8")
    print("Created .env with a generated POSTGRES_PASSWORD and FERNET_KEY.")
    print("Next: open .env and paste your GROQ_API_KEY.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
