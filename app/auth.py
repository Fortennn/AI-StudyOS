import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

TOKEN_TTL_SECONDS = 60 * 60 * 24 * 14  # 14 days
COOKIE_NAME = "studyos_session"


def _load_or_create_secret() -> str:
    env_secret = os.getenv("AUTH_SECRET", "").strip()
    if env_secret:
        return env_secret
    secret_file = os.path.join("data", ".auth_secret")
    if os.path.exists(secret_file):
        with open(secret_file, "r", encoding="utf-8") as f:
            val = f.read().strip()
            if val:
                return val
    os.makedirs("data", exist_ok=True)
    generated = secrets.token_hex(32)
    with open(secret_file, "w", encoding="utf-8") as f:
        f.write(generated)
    return generated


_SECRET_KEY = _load_or_create_secret().encode("utf-8")


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    use_salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), use_salt.encode("utf-8"), 120_000)
    return dk.hex(), use_salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    computed, _ = hash_password(password, salt=salt)
    return hmac.compare_digest(computed, stored_hash)


def create_session_token(user_id: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    exp = int(time.time()) + ttl
    payload = f"{user_id}|{exp}"
    sig = hmac.new(_SECRET_KEY, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def verify_session_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    parts = token.split("|")
    if len(parts) != 3:
        return None
    user_id, exp_str, sig = parts
    try:
        exp = int(exp_str)
    except ValueError:
        return None
    if exp < int(time.time()):
        return None
    expected_sig = hmac.new(_SECRET_KEY, f"{user_id}|{exp_str}".encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    return user_id
