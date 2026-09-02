"""
Linked accounts module -- Fernet encryption + PostgreSQL CRUD.
Passwords are reversibly encrypted so the download team can recover plaintext for browser automation.
"""

import logging
import os
from typing import Optional
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from psycopg.errors import UniqueViolation

from database.connection import connect, init_schema

logger = logging.getLogger(__name__)


class LinkedAccountDecryptionError(RuntimeError):
    """Raised when a stored linked-account ciphertext cannot be decrypted.

    Typical causes: LINKED_ACCOUNTS_KEY rotated/lost, ciphertext corrupted,
    or the key environment variable points at a different key than the one
    used at insert time. The DAO catches this and reports the row as
    decryption_failed instead of crashing the request.
    """


def _get_fernet() -> Fernet:
    key = os.getenv("LINKED_ACCOUNTS_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "LINKED_ACCOUNTS_KEY environment variable is not set. "
            "Generate one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


def encrypt_password(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_password(ciphertext: str) -> str:
    """Decrypt a stored ciphertext.

    Raises LinkedAccountDecryptionError when the underlying Fernet
    operation reports InvalidToken — most often a sign the
    LINKED_ACCOUNTS_KEY env var no longer matches the key used at
    encrypt time. Callers that read multiple rows should catch this
    and surface the row with decryption_failed=True rather than
    aborting the whole listing.
    """
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise LinkedAccountDecryptionError(
            "Failed to decrypt linked-account password (key may have been "
            "rotated, lost, or the ciphertext is corrupted)."
        ) from e


def verify_linked_accounts_key_can_decrypt() -> Optional[str]:
    """Smoke check on backend startup: pick the most recent linked_accounts
    row (if any) and try decrypting. Return None on success or when the
    table is empty; return a short reason string on failure so the
    caller can log a warning without aborting startup.

    This is best-effort observability — we don't fail-fast because the
    operator may already be aware of the rotation and want the service
    up to let users re-enter credentials.
    """
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT encrypted_password FROM linked_accounts "
                    "ORDER BY created_at DESC LIMIT 1"
                )
                row = cur.fetchone()
        if row is None:
            return None
        try:
            decrypt_password(row["encrypted_password"])
            return None
        except LinkedAccountDecryptionError as e:
            return str(e)
    except Exception as e:
        # Table may not exist yet (init_schema runs after this in some
        # bootstrap orders); treat any infrastructure error as inconclusive.
        logger.debug("verify_linked_accounts_key_can_decrypt skipped: %s", e)
        return None


def ensure_table() -> None:
    with connect() as conn:
        init_schema(conn)


def get_linked_accounts(user_email: str) -> list[dict]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, site, credential, created_at FROM linked_accounts "
                "WHERE user_email = %s ORDER BY created_at DESC",
                (user_email.lower(),),
            )
            rows = cur.fetchall()
    return [dict(row) for row in rows]


def _normalize_site_hint(site_hint: str) -> str:
    hint = (site_hint or "").strip().lower()
    if not hint:
        return ""
    if "://" not in hint:
        hint = f"https://{hint}"
    parsed = urlparse(hint)
    return (parsed.netloc or hint).strip().lower()


def get_linked_account_secrets(user_email: str, site_hint: str) -> Optional[dict]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, site, credential, encrypted_password, created_at
                FROM linked_accounts
                WHERE user_email = %s
                ORDER BY created_at DESC
                """,
                (user_email.lower(),),
            )
            rows = cur.fetchall()

    hint = _normalize_site_hint(site_hint)
    if not hint:
        return None

    normalized_accounts = []
    for row in rows:
        acc_site = (row["site"] or "").strip().lower()
        acc_norm = _normalize_site_hint(acc_site)
        normalized_accounts.append((row, acc_site, acc_norm))

    def _build(row) -> dict:
        try:
            password = decrypt_password(row["encrypted_password"])
            return {
                "id": row["id"],
                "site": row["site"],
                "credential": row["credential"],
                "password": password,
                "created_at": row["created_at"],
                "decryption_failed": False,
            }
        except LinkedAccountDecryptionError as e:
            logger.warning(
                "linked_accounts: decryption failed for id=%s site=%r user=%r: %s",
                row.get("id"), row.get("site"), user_email, e,
            )
            return {
                "id": row["id"],
                "site": row["site"],
                "credential": row["credential"],
                "password": None,
                "created_at": row["created_at"],
                "decryption_failed": True,
            }

    for row, acc_site, acc_norm in normalized_accounts:
        if acc_norm == hint or acc_site == hint:
            return _build(row)

    for row, acc_site, acc_norm in normalized_accounts:
        if acc_norm and (acc_norm in hint or hint in acc_norm):
            return _build(row)

    return None


def add_linked_account(
    user_email: str,
    site: str,
    credential: str,
    password: str,
) -> dict:
    encrypted = encrypt_password(password)

    with connect() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO linked_accounts (user_email, site, credential, encrypted_password)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (user_email.lower(), site, credential, encrypted),
                )
                row = cur.fetchone()
                account_id = row["id"] if row else 0

                cur.execute(
                    "SELECT id, site, credential, created_at FROM linked_accounts WHERE id = %s",
                    (account_id,),
                )
                out = cur.fetchone()
            conn.commit()
            return dict(out) if out else {}
        except UniqueViolation:
            conn.rollback()
            raise ValueError(f"An account for '{site}' already exists for this user.") from None


def delete_linked_account(user_email: str, account_id: int) -> bool:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM linked_accounts WHERE id = %s AND user_email = %s",
                (account_id, user_email.lower()),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted
