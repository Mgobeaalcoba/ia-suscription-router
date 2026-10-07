#!/usr/bin/env python3
"""Shortcut to run from a clone of the repo: `python3 cli.py`. When installed with pip/brew the command is `ia-router`."""
import sys

from ia_router.cli import main

if __name__ == "__main__":
    sys.exit(main())
