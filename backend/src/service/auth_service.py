import base64
import hashlib
import hmac
import secrets


PBKDF2_ITERATIONS = 310000


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if not isinstance(password, str) or not password:
        raise ValueError("Password is required")

    use_salt = salt or base64.urlsafe_b64encode(secrets.token_bytes(16)).decode(
        "ascii"
    ).rstrip("=")
    derived_key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        use_salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return base64.b64encode(derived_key).decode("ascii"), use_salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    computed_hash, _ = hash_password(password, salt=salt)
    return hmac.compare_digest(computed_hash, expected_hash)


def create_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()