from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from enum import Enum
from typing import Any, Dict, Optional


class Role(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


ROLE_HIERARCHY = {
    Role.ADMIN: 3,
    Role.OPERATOR: 2,
    Role.VIEWER: 1,
}


def role_satisfies(user_role: str, required_role: str) -> bool:
    try:
        u = Role(user_role)
    except ValueError:
        return False
    try:
        r = Role(required_role)
        return ROLE_HIERARCHY[u] >= ROLE_HIERARCHY[r]
    except ValueError:
        return False


def hash_password(password: str, iterations: int = 100000) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${derived.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        algorithm, iterations_str, salt_hex, hash_hex = hashed.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(derived, expected_hash)
    except Exception:
        return False


def _b64encode_url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64decode_url(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def encode_jwt(payload: Dict[str, Any], secret: str, expires_in_seconds: int = 3600) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    claims = dict(payload)
    now = int(time.time())
    if "iat" not in claims:
        claims["iat"] = now
    if "exp" not in claims:
        claims["exp"] = now + expires_in_seconds

    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    payload_bytes = json.dumps(claims, separators=(",", ":")).encode("utf-8")

    header_b64 = _b64encode_url(header_bytes)
    payload_b64 = _b64encode_url(payload_bytes)

    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    signature_b64 = _b64encode_url(signature)

    return f"{header_b64}.{payload_b64}.{signature_b64}"


def decode_jwt(token: str, secret: str) -> Optional[Dict[str, Any]]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        header_b64, payload_b64, signature_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        actual_sig = _b64decode_url(signature_b64)

        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        payload_bytes = _b64decode_url(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))

        exp = payload.get("exp")
        if exp is not None and time.time() > float(exp):
            return None

        return payload
    except Exception:
        return None


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def generate_secret_token() -> str:
    return secrets.token_hex(32)


def hash_api_key(api_key: str) -> str:
    """Hash an API key using SHA256 for storage."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def verify_api_key_hash(api_key: str, hashed: str) -> bool:
    """Verify an API key against its hash using constant-time comparison."""
    expected = hash_api_key(api_key)
    return hmac.compare_digest(expected, hashed)
