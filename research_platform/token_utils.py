"""Token generation — port từ clender/utils/token.py."""

import secrets
import string

ALPHABET = string.ascii_letters + string.digits


def generate_token(length: int = 12) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def generate_numeric_token(length: int = 16) -> str:
    """t.me/bot?start=7754133249527383 style."""
    first = secrets.choice("123456789")
    rest = "".join(secrets.choice(string.digits) for _ in range(length - 1))
    return first + rest
