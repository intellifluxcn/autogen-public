"""
Authentication module for the AutoGen Web UI.
Supports JWT tokens and bcrypt-hashed passwords stored in PostgreSQL.
Includes role-based access control (admin/user) and password reset.
"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
import psycopg
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from database.connection import connect, init_schema
from utils.demo_user import DEMO_ADMIN_EMAIL, DEMO_ADMIN_NAME

DEMO_PASSWORD = "password12!"  # test fixture only — production seeds a random one-time password
DEMO_EMAIL = DEMO_ADMIN_EMAIL
DEMO_NAME = DEMO_ADMIN_NAME

AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "demo-secret-key-not-for-production")
AUTH_ALGORITHM = "HS256"
AUTH_TOKEN_EXPIRE_HOURS = 24
PASSWORD_RESET_TOKEN_EXPIRE_HOURS = 1

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    email: str
    name: Optional[str] = None


class UserProfileResponse(BaseModel):
    name: str
    email: str


class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str


class RegisterResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    email: str
    name: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ResetPasswordRequest(BaseModel):
    email: str


class ResetPasswordWithTokenRequest(BaseModel):
    token: str
    new_password: str


class AdminCreateUserRequest(BaseModel):
    name: str
    email: str
    password: str
    role: str = "user"


class AdminUpdateUserRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[int] = None


class AdminResetPasswordRequest(BaseModel):
    new_password: str


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: int
    created_at: str


def _users_table_exists(conn: psycopg.Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'users'
            """
        )
        return cur.fetchone() is not None


def _ensure_users_table_columns(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'users'
            """
        )
        cols = {r["column_name"] for r in cur.fetchall()}

    alters = [
        ("role", "ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user'"),
        ("is_active", "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active INTEGER DEFAULT 1"),
        (
            "password_reset_token",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_token TEXT",
        ),
        (
            "password_reset_expires",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_expires TIMESTAMP",
        ),
    ]
    with conn.cursor() as cur:
        for col, sql in alters:
            if col not in cols:
                cur.execute(sql)
    conn.commit()


def create_access_token(email: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(hours=AUTH_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": email,
        "exp": expire,
        "iat": now,
    }
    return jwt.encode(payload, AUTH_SECRET_KEY, algorithm=AUTH_ALGORITHM)


def verify_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, AUTH_SECRET_KEY, algorithms=[AUTH_ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            return None
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT is_active FROM users WHERE email = %s",
                    (email.lower(),),
                )
                row = cur.fetchone()
        if not row or not row["is_active"]:
            return None
    except Exception:
        return None

    return email


def authenticate_user(email: str, password: str) -> bool:
    try:
        with connect() as conn:
            if not _users_table_exists(conn):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Authentication backend not initialized",
                )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT hashed_password FROM users WHERE email = %s",
                    (email.lower(),),
                )
                row = cur.fetchone()
        if row is None:
            return False
        return bcrypt.checkpw(password.encode(), row["hashed_password"].encode())
    except HTTPException:
        raise
    except psycopg.Error as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication backend unavailable",
        ) from e


def register_user(name: str, email: str, password: str) -> dict:
    if not name.strip():
        raise ValueError("Name cannot be empty")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")

    email = email.lower().strip()
    name_clean = name.strip()

    with connect() as conn:
        init_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                raise ValueError("An account with that email already exists")

            hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            cur.execute(
                "INSERT INTO users (name, email, hashed_password) "
                "VALUES (%s, %s, %s) RETURNING id",
                (name_clean, email, hashed),
            )
            row = cur.fetchone()
            user_id = row["id"] if row else 0
        conn.commit()

    return {"id": user_id, "name": name_clean, "email": email}


def seed_demo_user() -> None:
    """Create the demo admin account if absent.

    Gated by SEED_DEMO_USER=true (default off). When enabled and a new account
    is created, a random password is generated and printed once to stdout so
    the operator can record it; the password is NEVER persisted in plaintext
    nor reused across runs.
    """
    if os.getenv("SEED_DEMO_USER", "").strip().lower() not in ("1", "true", "yes"):
        return

    try:
        with connect() as conn:
            init_schema(conn)
            _ensure_users_table_columns(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE email = %s", (DEMO_EMAIL,))
                if not cur.fetchone():
                    one_time_password = secrets.token_urlsafe(16)
                    hashed = bcrypt.hashpw(
                        one_time_password.encode(), bcrypt.gensalt()
                    ).decode()
                    cur.execute(
                        "INSERT INTO users (name, email, hashed_password, role) "
                        "VALUES (%s, %s, %s, %s)",
                        (DEMO_NAME, DEMO_EMAIL, hashed, "admin"),
                    )
                    print(
                        f"[AUTH] Seeded demo admin {DEMO_EMAIL} with one-time "
                        f"password: {one_time_password}\n"
                        "[AUTH] Record this password now — it will not be shown again."
                    )
            conn.commit()
    except Exception as e:
        print(f"[AUTH] Warning: Failed to seed demo user: {e}")


def get_user_profile(email: str) -> dict:
    try:
        with connect() as conn:
            if not _users_table_exists(conn):
                return {"name": "User", "email": email}
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM users WHERE email = %s", (email.lower(),))
                row = cur.fetchone()
            if row is None:
                return {"name": "User", "email": email}
            return {"name": row["name"], "email": email}
    except Exception:
        return {"name": "User", "email": email}


def delete_user(email: str) -> bool:
    with connect() as conn:
        try:
            email_lower = email.lower()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM projects WHERE user_email = %s",
                    (email_lower,),
                )
                project_ids = [r["id"] for r in cur.fetchall()]
                for pid in project_ids:
                    cur.execute("DELETE FROM stages WHERE project_id = %s", (pid,))
                    cur.execute("DELETE FROM messages WHERE project_id = %s", (pid,))
                    cur.execute("DELETE FROM artifacts WHERE project_id = %s", (pid,))
                cur.execute("DELETE FROM projects WHERE user_email = %s", (email_lower,))

                cur.execute(
                    "DELETE FROM linked_accounts WHERE user_email = %s",
                    (email_lower,),
                )
                cur.execute("DELETE FROM users WHERE email = %s", (email_lower,))
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        except Exception:
            conn.rollback()
            return False


def get_user_role(email: str) -> Optional[str]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT role FROM users WHERE email = %s", (email.lower(),))
            row = cur.fetchone()
            return row["role"] if row else None


def is_admin(email: str) -> bool:
    role = get_user_role(email)
    return role == "admin"


def count_admins() -> int:
    """Active admin count (used to forbid deleting / demoting the last one)."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
            )
            row = cur.fetchone()
            return int(row["n"]) if row else 0


def update_user_fields_by_admin(
    email: str,
    *,
    name: Optional[str] = None,
    role: Optional[str] = None,
    is_active: Optional[int] = None,
) -> bool:
    """Patch user fields. Caller (HTTP layer) is responsible for enforcing
    "can't demote / deactivate the last admin" — we just persist."""
    if role is not None and role not in ("admin", "user"):
        raise ValueError("Role must be 'admin' or 'user'")
    if is_active is not None and is_active not in (0, 1):
        raise ValueError("is_active must be 0 or 1")

    updates: List[str] = []
    values: list = []
    if name is not None:
        clean = name.strip()
        if not clean:
            raise ValueError("Name cannot be empty")
        updates.append("name = %s")
        values.append(clean)
    if role is not None:
        updates.append("role = %s")
        values.append(role)
    if is_active is not None:
        updates.append("is_active = %s")
        values.append(is_active)
    if not updates:
        return False
    values.append(email.lower())

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE email = %s",
                tuple(values),
            )
            updated = cur.rowcount
        conn.commit()
    return bool(updated)


def reset_user_password_by_admin(email: str, new_password: str) -> bool:
    """Admin-driven password reset. No current-password check — caller is admin."""
    if len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters")
    new_hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET hashed_password = %s, "
                "password_reset_token = NULL, password_reset_expires = NULL "
                "WHERE email = %s",
                (new_hashed, email.lower()),
            )
            updated = cur.rowcount
        conn.commit()
    return bool(updated)


def get_all_users() -> list:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, email, role, is_active, created_at
                FROM users
                ORDER BY created_at DESC
                """
            )
            rows = cur.fetchall()
            return [dict(row) for row in rows]


def create_user_by_admin(
    name: str, email: str, password: str, role: str = "user"
) -> dict:
    if role not in ("admin", "user"):
        raise ValueError("Role must be 'admin' or 'user'")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")

    email = email.lower().strip()

    with connect() as conn:
        init_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                raise ValueError("An account with that email already exists")

            hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            cur.execute(
                "INSERT INTO users (name, email, hashed_password, role) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (name.strip(), email, hashed, role),
            )
            row = cur.fetchone()
            user_id = row["id"] if row else 0
        conn.commit()

    return {"id": user_id, "name": name.strip(), "email": email, "role": role}


def change_user_password(
    email: str, current_password: str, new_password: str
) -> bool:
    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters")

    email_lower = email.lower()
    with connect() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT hashed_password FROM users WHERE email = %s AND is_active = 1",
                    (email_lower,),
                )
                row = cur.fetchone()

                if not row:
                    return False

                if not bcrypt.checkpw(
                    current_password.encode(), row["hashed_password"].encode()
                ):
                    return False

                new_hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
                cur.execute(
                    "UPDATE users SET hashed_password = %s WHERE email = %s",
                    (new_hashed, email_lower),
                )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False


def generate_password_reset_token(email: str) -> Optional[str]:
    email_lower = email.lower()
    with connect() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM users WHERE email = %s AND is_active = 1",
                    (email_lower,),
                )
                if not cur.fetchone():
                    return None

                token = secrets.token_urlsafe(32)
                expires = datetime.now(timezone.utc) + timedelta(
                    hours=PASSWORD_RESET_TOKEN_EXPIRE_HOURS
                )

                cur.execute(
                    "UPDATE users SET password_reset_token = %s, "
                    "password_reset_expires = %s WHERE email = %s",
                    (token, expires, email_lower),
                )
            conn.commit()
            return token
        except Exception:
            conn.rollback()
            return None


def reset_password_with_token(token: str, new_password: str) -> bool:
    if len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters")

    with connect() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT email FROM users
                    WHERE password_reset_token = %s
                    AND password_reset_expires > NOW()
                    AND is_active = 1
                    """,
                    (token,),
                )
                row = cur.fetchone()

                if not row:
                    return False

                email_addr = row["email"]
                new_hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
                cur.execute(
                    "UPDATE users SET hashed_password = %s, "
                    "password_reset_token = NULL, password_reset_expires = NULL "
                    "WHERE email = %s",
                    (new_hashed, email_addr),
                )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    token = credentials.credentials
    email = verify_token(token)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return email


async def get_current_user_or_token_param(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
) -> str:
    if credentials and credentials.credentials:
        email = verify_token(credentials.credentials)
        if email:
            return email
    token = request.query_params.get("token")
    if token:
        email = verify_token(token)
        if email:
            return email
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_admin(email: str = Depends(get_current_user)) -> str:
    if not is_admin(email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return email
