"""`make password`: set the dashboard password. Only its Argon2 hash is written to `.env`.

`make password SHOW=1` echoes what you type. Use it if sign-in says "wrong password": on Windows,
the hidden prompt reads raw console keystrokes and can capture AltGr / accented characters (e.g.
`@ # { | \\ ~` on an AZERTY keyboard) differently from what the browser receives.
"""

from __future__ import annotations

import argparse
import getpass
import re
import sys
from pathlib import Path

from argon2 import PasswordHasher

ENV = Path(__file__).resolve().parents[1] / ".env"
MIN_LENGTH = 12


def ask(prompt: str, show: bool) -> str:
    return input(prompt) if show else getpass.getpass(prompt)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", action="store_true", help="echo the password while typing")
    args = parser.parse_args()
    if not ENV.exists():
        print("No .env yet: run `make env` first.", file=sys.stderr)
        return 1

    password = ask(f"New dashboard password (min {MIN_LENGTH} characters): ", args.show)
    if password != password.strip():
        print("The password starts or ends with a space; remove it and try again.", file=sys.stderr)
        return 1
    if len(password) < MIN_LENGTH:
        print(f"Too short: use at least {MIN_LENGTH} characters.", file=sys.stderr)
        return 1
    if ask("Repeat: ", args.show) != password:
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

    non_ascii = sum(not ch.isascii() for ch in password)
    print(
        f"Saved the hash of a {len(password)}-character password"
        + (f" ({non_ascii} non-ASCII character(s))" if non_ascii else "")
        + "."
    )
    print("If that length is not what you typed, run `make password SHOW=1` instead.")
    print("Now run `make restart` so the API picks up the new password.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
