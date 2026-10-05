#!/usr/bin/env python3
"""Atajo para correr desde un clon del repo: `python3 cli.py`. Instalado con pip/brew el comando es `ia-router`."""
import sys

from ia_router.cli import main

if __name__ == "__main__":
    sys.exit(main())
