#!/usr/bin/env python3
"""
cloud_login.py — Authenticate with the Yarbo cloud API and list bound robots.

Requires:
    pip install "python-yarbo[cloud]"
    RSA public key from the Yarbo APK (assets/rsa_key/rsa_public_key.pem)
    See: https://github.com/markus-lassfolk/yarbo-reversing

Usage:
    python examples/cloud_login.py --username you@example.com --password secret \\
        --key /path/to/rsa_public_key.pem
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from yarbo import YarboCloudClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")


async def main(username: str, password: str, key_path: str | None) -> None:
    print(f"\n☁️  Logging in as {username}...")

    async with YarboCloudClient(
        username=username,
        password=password,
        rsa_key_path=key_path,
    ) as client:
        print("✅ Login successful!\n")

        # App version
        version = await client.get_latest_version()
        print(f"📱 App version:      {version.get('appVersion', 'N/A')}")
        print(f"⚙️  Firmware version: {version.get('firmwareVersion', 'N/A')}")
        print(f"🔌 DC version:       {version.get('dcVersion', 'N/A')}\n")

        # Robots
        robots = await client.list_robots()
        if robots:
            print(f"🤖 Bound robots ({len(robots)}):")
            for robot in robots:
                status = "🟢 online" if robot.is_online else "⚫ offline"
                print(f"   {robot.sn}: {robot.name or '(unnamed)'} — {status}")
                print(f"       Model: {robot.model or 'unknown'}")
                print(f"       Firmware: {robot.firmware or 'unknown'}")
        else:
            print("🤖 No robots bound to this account.")

        # Notifications
        settings = await client.get_notification_settings()
        print(f"\n🔔 Notification settings: {settings}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Yarbo cloud login")
    ap.add_argument(
        "--username",
        default=os.environ.get("YARBO_USERNAME", ""),
        help="Email (or set YARBO_USERNAME env var)",
    )
    ap.add_argument(
        "--password",
        default=os.environ.get("YARBO_PASSWORD", ""),
        help="Password (or set YARBO_PASSWORD env var)",
    )
    ap.add_argument(
        "--key",
        default=os.environ.get("YARBO_RSA_KEY_PATH"),
        help="Path to RSA public key PEM (from APK)",
    )
    args = ap.parse_args()

    if not args.username or not args.password:
        ap.error("--username and --password are required (or set YARBO_USERNAME/YARBO_PASSWORD)")

    asyncio.run(main(username=args.username, password=args.password, key_path=args.key))
