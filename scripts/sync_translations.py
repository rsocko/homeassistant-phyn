#!/usr/bin/env python3
"""Generate custom_components/phyn/translations/en.json from strings.json.

strings.json is the source of truth for the integration's English strings
(config flow, options flow, entity names, service names) and may reference
Home Assistant's shared strings via `[%key:...%]`. That reference syntax only
resolves for core-shipped integrations, not custom components, so
translations/en.json — the literal-English template Weblate translates
from — must ship fully-resolved text. This script generates en.json from
strings.json, resolving any `%key%` references along the way, so the two
never drift. Never hand-edit translations/en.json (or the other
translations/*.json files, which Weblate owns) directly.

Usage:
    python scripts/sync_translations.py          # regenerate en.json
    python scripts/sync_translations.py --check  # verify en.json is current
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STRINGS_PATH = REPO_ROOT / "custom_components" / "phyn" / "strings.json"
EN_JSON_PATH = REPO_ROOT / "custom_components" / "phyn" / "translations" / "en.json"

KEY_REF_RE = re.compile(r"^\[%key:(.+)%\]$")

# English text for the Home Assistant common-string keys strings.json refers to.
# Sourced from home-assistant/core's homeassistant/strings.json (common.config_flow).
COMMON_STRINGS = {
    "common::config_flow::data::host": "Host",
    "common::config_flow::data::username": "Username",
    "common::config_flow::data::password": "Password",
    "common::config_flow::error::cannot_connect": "Failed to connect",
    "common::config_flow::error::invalid_auth": "Invalid authentication",
    "common::config_flow::error::unknown": "Unexpected error",
    "common::config_flow::abort::already_configured_device": "Device is already configured",
}


def resolve(value):
    """Recursively resolve `[%key:...%]` references to literal English."""
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v) for v in value]
    if isinstance(value, str):
        match = KEY_REF_RE.match(value)
        if match:
            key = match.group(1)
            if key not in COMMON_STRINGS:
                raise KeyError(
                    f"Unknown common-string key {key!r} referenced in "
                    f"{STRINGS_PATH.relative_to(REPO_ROOT)}. Add it to "
                    "COMMON_STRINGS in scripts/sync_translations.py."
                )
            return COMMON_STRINGS[key]
    return value


def render(data: dict) -> str:
    """Render translation data the way translations/*.json files are formatted."""
    return json.dumps(resolve(data), indent=4, sort_keys=True, ensure_ascii=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify translations/en.json matches strings.json without writing.",
    )
    args = parser.parse_args()

    strings_data = json.loads(STRINGS_PATH.read_text(encoding="utf-8"))
    rendered = render(strings_data)

    if args.check:
        current = EN_JSON_PATH.read_text(encoding="utf-8") if EN_JSON_PATH.exists() else ""
        if current != rendered:
            sys.stderr.write(
                f"{EN_JSON_PATH.relative_to(REPO_ROOT)} is out of sync with "
                f"{STRINGS_PATH.relative_to(REPO_ROOT)}.\n"
                "Run `python scripts/sync_translations.py` to regenerate it.\n"
            )
            return 1
        print(f"{EN_JSON_PATH.relative_to(REPO_ROOT)} is up to date.")
        return 0

    EN_JSON_PATH.write_text(rendered, encoding="utf-8")
    print(f"Wrote {EN_JSON_PATH.relative_to(REPO_ROOT)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
