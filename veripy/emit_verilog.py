"""Legacy shim — _to_snake is still imported by cli.py and verify.py."""

import re


def _to_snake(name):
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', name).lower()
