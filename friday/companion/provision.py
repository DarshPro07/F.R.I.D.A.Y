"""
Set the companion up: `python -m friday.companion.provision`

Mints the extension key, pins it into the manifest so the id is stable, and
prints what to paste where. Safe to run twice.
"""

from __future__ import annotations

from friday.companion import bridge, pairing


def main() -> int:
    details = pairing.provision()
    token = bridge.load_token()

    print("Friday Companion")
    print("=" * 60)
    print(f"  extension id : {details['extension_id']}")
    print(f"  bridge accepts only: {details['origin']}")
    print(f"  manifest {'updated' if details['manifest_updated'] else 'already pinned'}")
    print()
    print("  1. chrome://extensions -> Developer mode -> Load unpacked")
    print("     select: friday/companion/extension")
    print("  2. Details -> Extension options")
    print("  3. paste this token and Save:")
    print()
    print(f"     {token}")
    print()
    print(f"  (also in {bridge.TOKEN_PATH})")
    print()
    print("  The id above is pinned in the manifest, so it stays the same on")
    print("  every machine and the bridge can accept that one extension only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
