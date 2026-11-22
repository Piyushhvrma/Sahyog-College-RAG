"""Shared helpers for CLI scripts. Run scripts as modules from the project root:  python -m scripts.<name>"""
import logging
import sys

from app.config.settings import Settings, get_settings


def bootstrap(*groups: str) -> Settings:
    s = get_settings()
    logging.basicConfig(level=s.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")
    missing = s.missing_for(*groups) if groups else []
    if missing:
        print(f"ERROR: missing required .env values: {', '.join(missing)}")
        print("Copy .env.example to .env and fill them in (see README).")
        sys.exit(2)
    return s
