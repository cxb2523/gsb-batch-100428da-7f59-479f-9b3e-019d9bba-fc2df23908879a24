"""Thin wrapper around bcrypt for password hashing and verification."""

import bcrypt


def hash_password(password: str) -> str:
    """
    Hash a plaintext password using bcrypt.

    Args:
        password: Plaintext password string.

    Returns:
        bcrypt hash string suitable for storage.
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """
    Check a plaintext password against a stored bcrypt hash.

    Args:
        password: Plaintext password to check.
        hashed: Stored bcrypt hash.

    Returns:
        True if the password matches. False on mismatch or any error.
    """
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False
