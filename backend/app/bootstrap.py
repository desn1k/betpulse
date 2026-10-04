"""Bootstrap CLI.

Usage:
    python -m app.bootstrap create-admin [--force]

Creates the initial admin account from ``ADMIN_EMAIL`` / ``ADMIN_PASSWORD``.
- In **production** ``ADMIN_PASSWORD`` is required and explicit: an empty,
  placeholder (``#...``, a documented example), short or predictable password
  is refused (exit 2) and nothing is created.
- In development an empty ``ADMIN_PASSWORD`` still generates a strong one-time
  password, printed to **stdout only** (never logged, never persisted in plaintext).
- The admin is created with ``must_change_password=True``; admin routes stay
  locked until the password is changed.
- Refuses to run if an admin already exists (or the email is taken) unless
  ``--force`` is given, in which case the target account is promoted/reset.

Exit codes: 0 done, 1 the admin/email already exists, 2 the configuration is
refused (e.g. a production ``ADMIN_PASSWORD`` that is a placeholder: the
settings validation itself rejects it, and only its message is printed).
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import admin_password_problem, get_settings, is_placeholder_secret
from app.core.db import _write_sessionmaker
from app.models.user import User, UserRole


def _generate_password() -> str:
    # ~26 chars of URL-safe entropy; strong and easy to copy once.
    return secrets.token_urlsafe(20)


async def _create_admin(force: bool) -> int:
    # Imported here: app.core.security builds its hasher from the settings at
    # import time, and main() must validate the settings first (exit code 2).
    from app.core.security import hash_password

    settings = get_settings()
    email = settings.admin_email.strip().lower()

    generated: str | None = None
    password = settings.admin_password
    if settings.is_production or (password and is_placeholder_secret(password)):
        problem = admin_password_problem(password)
        if problem:
            print(
                f"Refusing to create the admin: ADMIN_PASSWORD {problem}. Set an explicit, "
                "strong password (at least 12 characters) in .env and re-run.",
                file=sys.stderr,
            )
            return 2
    if not password:
        password = _generate_password()
        generated = password

    async with _write_sessionmaker()() as session:
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        any_admin = (
            await session.execute(select(User).where(User.role == UserRole.admin))
        ).scalar_one_or_none()

        if existing is not None:
            if not force:
                print(
                    f"An account with email {email} already exists. "
                    "Re-run with --force to promote/reset it.",
                    file=sys.stderr,
                )
                return 1
            existing.role = UserRole.admin
            existing.password_hash = hash_password(password)
            existing.is_active = True
            existing.must_change_password = True
        else:
            if any_admin is not None and not force:
                print(
                    "An admin account already exists. Re-run with --force to create another.",
                    file=sys.stderr,
                )
                return 1
            session.add(
                User(
                    email=email,
                    password_hash=hash_password(password),
                    role=UserRole.admin,
                    is_active=True,
                    is_verified=True,
                    must_change_password=True,
                )
            )
        await session.commit()

    print(f"Admin account ready: {email}")
    if generated is not None:
        print("Generated one-time password (store it now, it will not be shown again):")
        print(f"  {generated}")
    print("You must change this password on first login before admin features unlock.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.bootstrap")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin", help="Create the initial admin account")
    create.add_argument("--force", action="store_true", help="Promote/reset if exists")
    args = parser.parse_args(argv)

    if args.command == "create-admin":
        try:
            get_settings()
        except ValidationError as exc:
            # Messages only: never the input values, which hold the secrets.
            for error in exc.errors(include_input=False, include_url=False):
                print(f"Refusing to create the admin: {error['msg']}", file=sys.stderr)
            return 2
        return asyncio.run(_create_admin(force=args.force))
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
