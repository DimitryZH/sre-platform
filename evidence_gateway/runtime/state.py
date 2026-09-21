"""Bounded durable replay, rate, and sanitized audit state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..canonical import canonical_json
from ..policy import EvidenceError


class RuntimeStateError(EvidenceError):
    """A durable state failure that must never release evidence."""

    def __init__(self, code: str = "audit_unavailable") -> None:
        super().__init__(code, "approved runtime state is unavailable")


@dataclass(frozen=True)
class RuntimeStateLimits:
    max_replay_entries: int = 128
    max_rate_subjects: int = 128
    max_requests_per_hour: int = 8
    max_audit_records: int = 10_000
    max_audit_bytes: int = 64 * 1024 * 1024
    max_database_bytes: int = 64 * 1024 * 1024
    audit_retention: timedelta = timedelta(days=30)


class RuntimeState:
    """SQLite state with bounded retention and no raw evidence persistence."""

    def __init__(self, path: str | Path, limits: RuntimeStateLimits | None = None) -> None:
        self.path = Path(path)
        self.limits = limits or RuntimeStateLimits()
        self._connection: sqlite3.Connection | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(str(self.path), isolation_level=None)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._initialize()
            self._assert_database_size()
        except (OSError, sqlite3.Error) as exc:
            if self._connection is not None:
                self._connection.close()
            raise RuntimeStateError() from exc
        except RuntimeStateError:
            if self._connection is not None:
                self._connection.close()
            raise

    def close(self) -> None:
        try:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
        except sqlite3.Error as exc:
            raise RuntimeStateError() from exc

    def check_and_record(self, *, subject: str, nonce: str, now: datetime, expires_at: datetime) -> None:
        """Atomically reserve a nonce and one authenticated request slot."""

        nonce_hash = sha256(nonce.encode("utf-8")).hexdigest()
        current = _timestamp(now)
        expiry = _timestamp(expires_at)
        cutoff = _timestamp(now - timedelta(hours=1))
        try:
            with self._require_connection():
                self._prune_locked(now)
                if self._connection.execute("SELECT 1 FROM replay WHERE nonce_hash = ?", (nonce_hash,)).fetchone():
                    raise EvidenceError("replay_rejected", "request nonce was already used")
                replay_count = self._connection.execute("SELECT COUNT(*) FROM replay").fetchone()[0]
                if replay_count >= self.limits.max_replay_entries:
                    raise EvidenceError("replay_capacity_exceeded", "replay protection state is at capacity")
                subject_exists = self._connection.execute(
                    "SELECT 1 FROM rate_events WHERE subject = ? LIMIT 1", (subject,)
                ).fetchone()
                subject_count = self._connection.execute("SELECT COUNT(DISTINCT subject) FROM rate_events").fetchone()[0]
                if subject_exists is None and subject_count >= self.limits.max_rate_subjects:
                    raise EvidenceError("rate_limit_unavailable", "rate limit state is at capacity")
                request_count = self._connection.execute(
                    "SELECT COUNT(*) FROM rate_events WHERE subject = ? AND created_at > ?", (subject, cutoff)
                ).fetchone()[0]
                if request_count >= self.limits.max_requests_per_hour:
                    raise EvidenceError("rate_limited", "caller exceeded the approved runtime request rate")
                self._connection.execute("INSERT INTO replay(nonce_hash, expires_at) VALUES (?, ?)", (nonce_hash, expiry))
                self._connection.execute("INSERT INTO rate_events(subject, created_at) VALUES (?, ?)", (subject, current))
                self._assert_database_size()
        except EvidenceError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise RuntimeStateError() from exc

    def write_audit(self, audit: dict[str, Any], *, now: datetime) -> None:
        try:
            safe_audit = sanitize_audit(audit)
            payload = canonical_json(safe_audit)
            payload_bytes = len(payload.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise RuntimeStateError() from exc
        try:
            with self._require_connection():
                self._prune_locked(now)
                count = self._connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
                total_bytes = self._connection.execute("SELECT COALESCE(SUM(payload_bytes), 0) FROM audit_events").fetchone()[0]
                if count >= self.limits.max_audit_records or total_bytes + payload_bytes > self.limits.max_audit_bytes:
                    raise RuntimeStateError("audit_capacity_exceeded")
                self._connection.execute(
                    "INSERT INTO audit_events(created_at, payload, payload_bytes) VALUES (?, ?, ?)",
                    (_timestamp(now), payload, payload_bytes),
                )
                self._assert_database_size()
        except RuntimeStateError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise RuntimeStateError() from exc

    def audit_events(self) -> list[dict[str, Any]]:
        try:
            rows = self._require_connection().execute("SELECT payload FROM audit_events ORDER BY id").fetchall()
            return [json.loads(row[0]) for row in rows]
        except (json.JSONDecodeError, sqlite3.Error) as exc:
            raise RuntimeStateError() from exc

    def _initialize(self) -> None:
        self._require_connection().executescript(
            """
            CREATE TABLE IF NOT EXISTS replay (
              nonce_hash TEXT PRIMARY KEY,
              expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rate_events (
              subject TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS rate_events_subject_created_at ON rate_events(subject, created_at);
            CREATE TABLE IF NOT EXISTS audit_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at INTEGER NOT NULL,
              payload TEXT NOT NULL,
              payload_bytes INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS audit_events_created_at ON audit_events(created_at);
            """
        )

    def _prune_locked(self, now: datetime) -> None:
        connection = self._require_connection()
        connection.execute("DELETE FROM replay WHERE expires_at <= ?", (_timestamp(now),))
        connection.execute("DELETE FROM rate_events WHERE created_at <= ?", (_timestamp(now - timedelta(hours=1)),))
        connection.execute(
            "DELETE FROM audit_events WHERE created_at <= ?", (_timestamp(now - self.limits.audit_retention),)
        )

    def _assert_database_size(self) -> None:
        total = sum(candidate.stat().st_size for candidate in self._database_files() if candidate.exists())
        if total > self.limits.max_database_bytes:
            raise RuntimeStateError("state_capacity_exceeded")

    def _database_files(self) -> tuple[Path, Path, Path]:
        return self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeStateError()
        return self._connection


def sanitize_audit(audit: dict[str, Any]) -> dict[str, Any]:
    """Persist only deterministic audit fields, never evidence or transport detail."""

    allowed = {
        "request_id",
        "caller_identity",
        "kubernetes_subject",
        "policy_subject",
        "authentication_method",
        "token_audience",
        "nonce_sha256",
        "operation",
        "target",
        "time_range",
        "decision",
        "denial_reason",
        "request_fingerprint",
        "evidence_id",
        "result_digest",
        "revision",
        "counts",
        "provider_calls",
        "provider_call_count",
        "result_bytes",
        "response_bytes",
    }
    if not isinstance(audit, dict):
        raise RuntimeStateError()
    sanitized = {key: audit[key] for key in sorted(allowed & set(audit))}
    _assert_safe_audit_value(sanitized)
    return sanitized


def _assert_safe_audit_value(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str) or key in {"token", "authorization", "content", "message", "provider_call_params"}:
                raise RuntimeStateError()
            _assert_safe_audit_value(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_safe_audit_value(nested)
    elif value is None or isinstance(value, (bool, int, float)):
        return
    elif isinstance(value, str):
        if len(value) > 512 or any(marker in value.lower() for marker in ("bearer ", "password=", "token=")):
            raise RuntimeStateError()
    else:
        raise RuntimeStateError()


def _timestamp(value: datetime) -> int:
    if value.tzinfo is None:
        raise RuntimeStateError()
    return int(value.astimezone(UTC).timestamp())
