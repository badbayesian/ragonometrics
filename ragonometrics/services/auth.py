"""Authentication helpers shared by UI surfaces."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from ragonometrics.db.connection import pooled_connection

_HASH_PREFIX = "pbkdf2_sha256"
_HASH_MIN_ITERATIONS = 100_000
_HASH_DEFAULT_ITERATIONS = 390_000


def load_env_credentials() -> Dict[str, str]:
    """Load fallback credentials from environment variables."""
    raw_users_json = (os.getenv("STREAMLIT_USERS_JSON") or "").strip()
    credentials: Dict[str, str] = {}
    if raw_users_json:
        try:
            parsed = json.loads(raw_users_json)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            for raw_user, raw_password in parsed.items():
                user = str(raw_user or "").strip()
                password = str(raw_password or "").strip()
                if user and password:
                    credentials[user] = password
        if credentials:
            return credentials
    expected_user = (os.getenv("STREAMLIT_USERNAME") or "").strip()
    expected_pass = (os.getenv("STREAMLIT_PASSWORD") or "").strip()
    if expected_user and expected_pass:
        credentials[expected_user] = expected_pass
    return credentials


def password_hash(password: str, *, iterations: int | None = None, salt: bytes | None = None) -> str:
    """Build a PBKDF2-SHA256 hash string."""
    secret = str(password or "")
    if not secret:
        return ""
    iter_count = int(iterations or os.getenv("STREAMLIT_AUTH_PBKDF2_ITERATIONS", _HASH_DEFAULT_ITERATIONS))
    iter_count = max(_HASH_MIN_ITERATIONS, iter_count)
    salt_bytes = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt_bytes, iter_count)
    salt_text = base64.urlsafe_b64encode(salt_bytes).decode("ascii").rstrip("=")
    digest_text = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{_HASH_PREFIX}${iter_count}${salt_text}${digest_text}"


def password_verify(password: str, password_hash_value: str) -> bool:
    """Verify plaintext password against PBKDF2 hash (or legacy plaintext)."""
    secret = str(password or "")
    stored = str(password_hash_value or "")
    if not secret or not stored:
        return False
    if not stored.startswith(f"{_HASH_PREFIX}$"):
        return hmac.compare_digest(secret, stored)
    parts = stored.split("$", 3)
    if len(parts) != 4:
        return False
    _, iter_text, salt_text, digest_text = parts
    try:
        iter_count = max(_HASH_MIN_ITERATIONS, int(iter_text))
        salt_pad = "=" * (-len(salt_text) % 4)
        digest_pad = "=" * (-len(digest_text) % 4)
        salt_bytes = base64.urlsafe_b64decode((salt_text + salt_pad).encode("ascii"))
        expected = base64.urlsafe_b64decode((digest_text + digest_pad).encode("ascii"))
    except Exception:
        return False
    computed = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt_bytes, iter_count)
    return hmac.compare_digest(computed, expected)


def auth_db_url() -> str:
    """Return configured auth DB URL (empty when unset)."""
    return (os.environ.get("DATABASE_URL") or "").strip()


def auth_tables_ready(db_url: str) -> bool:
    """Return whether auth tables exist."""
    if not db_url:
        return False
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT
                        to_regclass('auth.streamlit_users'),
                        to_regclass('auth.streamlit_sessions')
                    """
                )
                row = cur.fetchone()
                if bool(row and row[0] is not None and row[1] is not None):
                    return True
            except Exception:
                pass
            # sqlite-backed tests: verify tables via simple probe queries.
            cur.execute("SELECT COUNT(*) FROM auth.streamlit_users")
            cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM auth.streamlit_sessions")
            cur.fetchone()
            return True
    except Exception:
        return False


def active_db_user_count(db_url: str) -> int:
    """Return count of active DB users."""
    if not db_url:
        return 0
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM auth.streamlit_users WHERE is_active = TRUE")
            row = cur.fetchone()
            return int((row or [0])[0] or 0)
    except Exception:
        return 0


def upsert_users_from_env(db_url: str, credentials: Dict[str, str]) -> int:
    """Upsert env credentials as hashed DB users."""
    if not db_url or not credentials:
        return 0
    upserted = 0
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            for raw_username, raw_password in credentials.items():
                username = str(raw_username or "").strip()
                password = str(raw_password or "")
                if not username or not password:
                    continue
                hashed = password_hash(password)
                if not hashed:
                    continue
                cur.execute(
                    """
                    INSERT INTO auth.streamlit_users
                        (username, password_hash, is_active, updated_at)
                    VALUES
                        (%s, %s, TRUE, NOW())
                    ON CONFLICT ((lower(username)))
                    DO UPDATE SET
                        password_hash = EXCLUDED.password_hash,
                        is_active = TRUE,
                        updated_at = NOW()
                    """,
                    (username, hashed),
                )
                upserted += 1
            conn.commit()
    except Exception:
        return 0
    return upserted


def verify_db_login(db_url: str, *, username: str, password: str) -> Tuple[bool, Dict[str, Any]]:
    """Verify DB-backed credentials by username/email and return canonical user payload."""
    identifier = str(username or "").strip()
    if not db_url or not identifier:
        return False, {}
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, username, password_hash, email
                FROM auth.streamlit_users
                WHERE (lower(username) = lower(%s) OR lower(COALESCE(email, '')) = lower(%s))
                  AND is_active = TRUE
                LIMIT 1
                """,
                (identifier, identifier),
            )
            row = cur.fetchone()
            if not row:
                return False, {}
            user_id = int(row[0])
            canonical_username = str(row[1] or identifier).strip() or identifier
            stored_hash = str(row[2] or "")
            email = str(row[3] or "").strip() or None
            if not password_verify(password, stored_hash):
                return False, {}
            cur.execute(
                "UPDATE auth.streamlit_users SET last_login_at = NOW(), updated_at = NOW() WHERE id = %s",
                (user_id,),
            )
            conn.commit()
            return True, {"user_id": user_id, "username": canonical_username, "email": email}
    except Exception:
        return False, {}


def register_db_user(
    db_url: str,
    *,
    username: str,
    email: Optional[str],
    password: str,
) -> Tuple[bool, Dict[str, Any]]:
    """Create one active DB-backed auth user."""
    user_name = str(username or "").strip()
    user_email = str(email or "").strip() or None
    raw_password = str(password or "")
    if not db_url:
        return False, {"code": "auth_unavailable", "message": "Database auth is unavailable."}
    if len(user_name) < 3:
        return False, {"code": "validation_error", "message": "Username must be at least 3 characters."}
    if len(raw_password) < 8:
        return False, {"code": "validation_error", "message": "Password must be at least 8 characters."}

    if not auth_tables_ready(db_url):
        return False, {"code": "auth_unavailable", "message": "Auth tables are not ready."}

    password_hash_value = password_hash(raw_password)
    if not password_hash_value:
        return False, {"code": "validation_error", "message": "Password is required."}

    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id
                FROM auth.streamlit_users
                WHERE lower(username) = lower(%s)
                LIMIT 1
                """,
                (user_name,),
            )
            if cur.fetchone():
                return False, {"code": "username_taken", "message": "Username is already in use."}
            if user_email:
                cur.execute(
                    """
                    SELECT id
                    FROM auth.streamlit_users
                    WHERE lower(COALESCE(email, '')) = lower(%s)
                    LIMIT 1
                    """,
                    (user_email,),
                )
                if cur.fetchone():
                    return False, {"code": "email_taken", "message": "Email is already in use."}

            cur.execute(
                """
                INSERT INTO auth.streamlit_users
                    (username, email, password_hash, is_active, created_at, updated_at)
                VALUES
                    (%s, %s, %s, TRUE, NOW(), NOW())
                """,
                (user_name, user_email, password_hash_value),
            )
            conn.commit()
    except Exception:
        return False, {"code": "register_failed", "message": "Could not create account."}

    return True, {"username": user_name, "email": user_email}


def _lookup_active_user_by_identifier(db_url: str, *, identifier: str) -> Dict[str, Any]:
    """Return one active user row by username/email identifier."""
    ident = str(identifier or "").strip()
    if not db_url or not ident:
        return {}
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, username, email
                FROM auth.streamlit_users
                WHERE (lower(username) = lower(%s) OR lower(COALESCE(email, '')) = lower(%s))
                  AND is_active = TRUE
                LIMIT 1
                """,
                (ident, ident),
            )
            row = cur.fetchone()
            if not row:
                return {}
            return {
                "user_id": int(row[0]),
                "username": str(row[1] or "").strip(),
                "email": str(row[2] or "").strip() or None,
            }
    except Exception:
        return {}


def request_password_reset(
    db_url: str,
    *,
    identifier: str,
    request_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    ttl_minutes: int = 30,
) -> Dict[str, Any]:
    """Create one password reset token for an active user.

    Returns empty dict when user is not found (do not reveal account existence).
    """
    user = _lookup_active_user_by_identifier(db_url, identifier=identifier)
    if not user:
        return {}
    plain_token = uuid4().hex + uuid4().hex
    token_hash = hashlib.sha256(plain_token.encode("utf-8")).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=max(5, int(ttl_minutes)))
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO auth.password_reset_tokens
                (user_id, email, token_hash, expires_at, request_ip, user_agent, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    int(user.get("user_id")),
                    str(user.get("email") or ""),
                    token_hash,
                    expires_at.isoformat(),
                    str(request_ip or "") or None,
                    str(user_agent or "") or None,
                ),
            )
            conn.commit()
    except Exception:
        return {}
    return {
        "token": plain_token,
        "username": str(user.get("username") or ""),
        "email": str(user.get("email") or "") or "",
        "expires_at": expires_at.isoformat(),
    }


def reset_password_with_token(
    db_url: str,
    *,
    token: str,
    new_password: str,
) -> bool:
    """Consume one reset token and update user password."""
    token_text = str(token or "").strip()
    if not db_url or not token_text or not new_password:
        return False
    token_hash = hashlib.sha256(token_text.encode("utf-8")).hexdigest()
    new_hash = password_hash(new_password)
    if not new_hash:
        return False
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, user_id, expires_at, used_at
                FROM auth.password_reset_tokens
                WHERE token_hash = %s
                LIMIT 1
                """,
                (token_hash,),
            )
            row = cur.fetchone()
            if not row:
                return False
            token_id = int(row[0])
            user_id = int(row[1]) if row[1] is not None else None
            expires_at = row[2]
            used_at = row[3]
            if not user_id or used_at is not None:
                return False
            expires_iso = expires_at.isoformat() if hasattr(expires_at, "isoformat") else str(expires_at or "")
            if expires_iso:
                try:
                    exp_dt = datetime.fromisoformat(expires_iso.replace("Z", "+00:00"))
                except Exception:
                    return False
                if exp_dt < datetime.now(timezone.utc):
                    return False
            cur.execute(
                """
                UPDATE auth.streamlit_users
                SET password_hash = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (new_hash, user_id),
            )
            cur.execute(
                """
                UPDATE auth.password_reset_tokens
                SET used_at = NOW()
                WHERE id = %s
                """,
                (token_id,),
            )
            conn.commit()
            return True
    except Exception:
        return False


def persist_session(
    db_url: str,
    *,
    user_id: int | None,
    username: str,
    source: str = "flask_ui",
    session_id: str | None = None,
    current_project_id: str | None = None,
    current_persona_id: str | None = None,
) -> str:
    """Insert or refresh one auth session row and return session id."""
    if not db_url:
        return str(session_id or uuid4().hex)
    sid = str(session_id or uuid4().hex)
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO auth.streamlit_sessions
                    (
                        session_id, user_id, username, source,
                        current_project_id, current_persona_id,
                        authenticated_at, revoked_at, updated_at
                    )
                VALUES
                    (%s, %s, %s, %s, %s, %s, NOW(), NULL, NOW())
                ON CONFLICT (session_id)
                DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    username = EXCLUDED.username,
                    source = EXCLUDED.source,
                    current_project_id = COALESCE(EXCLUDED.current_project_id, auth.streamlit_sessions.current_project_id),
                    current_persona_id = COALESCE(EXCLUDED.current_persona_id, auth.streamlit_sessions.current_persona_id),
                    authenticated_at = EXCLUDED.authenticated_at,
                    revoked_at = NULL,
                    updated_at = NOW()
                """,
                (
                    sid,
                    user_id,
                    str(username or "").strip(),
                    source,
                    str(current_project_id or "").strip() or None,
                    str(current_persona_id or "").strip() or None,
                ),
            )
            conn.commit()
    except Exception:
        pass
    return sid


def revoke_session(db_url: str, *, session_id: str) -> None:
    """Mark a session as revoked."""
    sid = str(session_id or "").strip()
    if not db_url or not sid:
        return
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE auth.streamlit_sessions
                SET revoked_at = NOW(), updated_at = NOW()
                WHERE session_id = %s
                """,
                (sid,),
            )
            conn.commit()
    except Exception:
        return


def get_session_user(db_url: str, *, session_id: str) -> Optional[Dict[str, Any]]:
    """Return active user info for a non-revoked session."""
    sid = str(session_id or "").strip()
    if not db_url or not sid:
        return None
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT
                        s.session_id,
                        s.username,
                        s.user_id,
                        s.authenticated_at,
                        COALESCE(s.current_project_id, ''),
                        COALESCE(s.current_persona_id, '')
                    FROM auth.streamlit_sessions s
                    LEFT JOIN auth.streamlit_users u ON u.id = s.user_id
                    WHERE s.session_id = %s
                      AND s.revoked_at IS NULL
                      AND (s.user_id IS NULL OR COALESCE(u.is_active, TRUE) = TRUE)
                    LIMIT 1
                    """,
                    (sid,),
                )
            except Exception:
                cur.execute(
                    """
                    SELECT s.session_id, s.username, s.user_id, s.authenticated_at
                    FROM auth.streamlit_sessions s
                    LEFT JOIN auth.streamlit_users u ON u.id = s.user_id
                    WHERE s.session_id = %s
                      AND s.revoked_at IS NULL
                      AND (s.user_id IS NULL OR COALESCE(u.is_active, TRUE) = TRUE)
                    LIMIT 1
                    """,
                    (sid,),
                )
            row = cur.fetchone()
            if not row:
                return None
            project_id = ""
            persona_id = ""
            if len(row) >= 6:
                project_id = str(row[4] or "")
                persona_id = str(row[5] or "")
            return {
                "session_id": str(row[0] or ""),
                "username": str(row[1] or ""),
                "user_id": int(row[2]) if row[2] is not None else None,
                "authenticated_at": row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3] or ""),
                "current_project_id": project_id,
                "current_persona_id": persona_id,
            }
    except Exception:
        return None


def authenticate_user(username: str, password: str) -> Tuple[bool, Dict[str, Any]]:
    """Authenticate against DB users with env fallback when DB users are absent."""
    db_url = auth_db_url()
    env_credentials = load_env_credentials()
    ready = auth_tables_ready(db_url)
    if ready and env_credentials and str(os.getenv("STREAMLIT_AUTH_BOOTSTRAP_FROM_ENV", "1")).strip() != "0":
        upsert_users_from_env(db_url, env_credentials)
    db_users = active_db_user_count(db_url) if ready else 0
    if ready and db_users > 0:
        ok, user = verify_db_login(db_url, username=username, password=password)
        if not ok:
            return False, {}
        user["source"] = "db"
        return True, user
    entered = str(username or "").strip()
    expected = env_credentials.get(entered)
    if expected and hmac.compare_digest(str(expected), str(password or "")):
        return True, {"user_id": None, "username": entered, "source": "env"}
    return False, {}
