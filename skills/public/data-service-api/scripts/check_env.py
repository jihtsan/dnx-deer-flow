#!/usr/bin/env python3
"""Validate the data service .env without printing credentials."""

from __future__ import annotations

import sys

from data_service import ConfigurationError, Settings


def main() -> int:
    try:
        settings = Settings.load(require_credentials=True)
    except ConfigurationError as exc:
        print(str(exc))
        return 1
    print("Data service environment is configured.")
    print(f"DATA_SERVICE_BASE_URL={settings.base_url}")
    print(f"DATA_SERVICE_TIMEOUT_SECONDS={settings.timeout_seconds:g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
