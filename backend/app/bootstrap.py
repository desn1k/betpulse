"""Bootstrap CLI.

Usage:
    python -m app.bootstrap create-admin [--force]

Creates the initial admin account from ``ADMIN_EMAIL`` / ``ADMIN_PASSWORD``.
- If ``ADMIN_PASSWORD`` is empty a strong one-time password is generated and
  printed to **stdout only** (never logged, never persisted in plaintext).
- The admin is created with ``must_change_password=True``; admin routes stay
  locked until the password is changed.
- Refuses to run if an admin already exists (or the email is taken) unless
  ``--force`` is given, in which case the target account is promoted/reset.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import _write_sessionmaker
from app.core.security import hash_password
from app.models.user import User, UserRole


def _generate_password() -> str:
    # ~26 chars of URL-safe entropy; strong and easy to copy once.
    return secrets.token_urlsafe(20)


async def _create_admin(force: bool) -> int:
    settings = get_settings()
    email = settings.admin_email.strip().lower()

    generated: str | None = None
    password = settings.admin_password
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
        return asyncio.run(_create_admin(force=args.force))
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
