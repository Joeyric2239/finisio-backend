"""
FINISIO CLEANS - Authentication Layer
Handles password hashing (bcrypt with sha256 legacy fallback) and JWT tokens.

Designed to run alongside the existing sha256 hashing in logic.py without
breaking old users — they get auto-upgraded to bcrypt on next successful login.
"""

import os
import hashlib
import time
import bcrypt
import jwt

# ---------------------------------------------------------
#  CONFIG (read from environment)
# ---------------------------------------------------------

JWT_SECRET = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET:
    # Loud failure on startup if secret is missing — better than silent insecurity
    print("[AUTH] WARNING: JWT_SECRET environment variable not set. "
          "Tokens will not be secure. Set JWT_SECRET on Railway immediately.")
    JWT_SECRET = "INSECURE_FALLBACK_CHANGE_ME"

JWT_ALGORITHM = "HS256"

# Token expiry (seconds)
TOKEN_EXPIRY_USER  = 90 * 24 * 60 * 60   # 90 days for customers/cleaners
TOKEN_EXPIRY_ADMIN = 24 * 60 * 60        # 24 hours for admin


# ---------------------------------------------------------
#  PASSWORD HASHING
# ---------------------------------------------------------

def hash_password_bcrypt(password: str) -> str:
    """Hash a password with bcrypt (the proper way)."""
    if not password:
        raise ValueError("Password cannot be empty")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def _is_bcrypt_hash(stored_hash: str) -> bool:
    """bcrypt hashes start with $2b$ or $2a$ or $2y$. sha256 hashes are 64 hex chars."""
    return bool(stored_hash) and stored_hash.startswith(("$2a$", "$2b$", "$2y$"))


def _sha256_hash(password: str) -> str:
    """Legacy sha256 hash — only used to verify old passwords, never to create new ones."""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(plain: str, stored_hash: str) -> bool:
    """
    Verify a password against the stored hash.
    Works with both bcrypt (new) and sha256 (legacy) hashes.
    """
    if not plain or not stored_hash:
        return False
    try:
        if _is_bcrypt_hash(stored_hash):
            return bcrypt.checkpw(plain.encode("utf-8"), stored_hash.encode("utf-8"))
        else:
            # Legacy sha256 fallback
            return _sha256_hash(plain) == stored_hash
    except Exception:
        return False


def needs_upgrade(stored_hash: str) -> bool:
    """Returns True if the hash is sha256 and should be upgraded to bcrypt."""
    return bool(stored_hash) and not _is_bcrypt_hash(stored_hash)


# ---------------------------------------------------------
#  JWT TOKENS
# ---------------------------------------------------------

def issue_token(user_id: str, role: str) -> str:
    """
    Create a JWT for a logged-in user.
    Admins get short-lived tokens, regular users get long-lived ones.
    """
    expiry = TOKEN_EXPIRY_ADMIN if role == "admin" else TOKEN_EXPIRY_USER
    now = int(time.time())
    payload = {
        "sub": user_id,           # subject = user id
        "role": role,
        "iat": now,                # issued at
        "exp": now + expiry,       # expires at
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    # PyJWT 2.x returns str, PyJWT 1.x returns bytes — normalise
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


def verify_token(token: str):
    """
    Verify a JWT and return its payload.
    Returns None if the token is missing, invalid, or expired.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        # Sanity-check required claims exist
        if "sub" not in payload or "role" not in payload:
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
    except Exception:
        return None


def extract_token_from_header(auth_header: str):
    """
    Extract the token from an "Authorization: Bearer <token>" header.
    Returns None if header is missing or malformed.
    """
    if not auth_header:
        return None
    parts = auth_header.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None