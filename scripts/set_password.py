"""`make password`: set the dashboard password. Only its Argon2 hash is written to `.env`."""

from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

from argon2 import PasswordHasher

ENV = Path(__file__).resolve().parents[1] / ".env"
MIN_LENGTH = 12


def main() -> int:
    if not ENV.exists():
        print("No .env yet: run `make env` first.", file=sys.stderr)
        return 1
    password = getpass.getpass("New dashboard password (min 12 chars): ")
    if len(password) < MIN_LENGTH:
        print(f"Too short: use at least {MIN_LENGTH} characters.", file=sys.stderr)
        return 1
    if getpass.getpass("Repeat: ") != password:
        print("The passwords do not match.", file=sys.stderr)
        return 1
    # single quotes: dotenv and docker compose then read the `$` signs of the hash literally
    line = f"DASHBOARD_PASSWORD_HASH='{PasswordHasher().hash(password)}'"
    text = ENV.read_text(encoding="utf-8")
    if re.search(r"^DASHBOARD_PASSWORD_HASH=.*$", text, flags=re.M):
        text = re.sub(r"^DASHBOARD_PASSWORD_HASH=.*$", lambda _: line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n\n# dashboard login (set by `make password`)\n" + line + "\n"
    ENV.write_text(text, encoding="utf-8")
    print("Password hash saved to .env. Restart the API (`make restart`) to apply it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
