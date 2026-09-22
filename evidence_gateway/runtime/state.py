"""Bounded durable replay, rate, and sanitized audit state."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator

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


@dataclass
class RequestStateTransaction:
    """One serialized request transaction, including its guard decision and audit."""

    state: RuntimeState
    denial: EvidenceError | None

    def write_audit(self, audit: dict[str, Any], *, now: datetime) -> None:
        self.state._write_audit_locked(audit, now=now)


class RuntimeState:
    """SQLite state with bounded retention and no raw evidence persistence."""

    def __init__(self, path: str | Path, limits: RuntimeStateLimits | None = None) -> None:
        self.path = Path(path)
        self.limits = limits or RuntimeStateLimits()
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._initialize()
            self._check_integrity()
            self._assert_database_size()
        except (OSError, sqlite3.Error) as exc:
            self._close_on_error()
            raise RuntimeStateError() from exc
        except RuntimeStateError:
            self._close_on_error()
            raise

    def close(self) -> None:
        with self._lock:
            try:
                if self._connection is not None:
                    self._connection.close()
                    self._connection = None
            except sqlite3.Error as exc:
                raise RuntimeStateError() from exc

    @contextmanager
    def request_transaction(
        self,
        *,
        subject: str,
        nonce_hash: str,
        now: datetime,
        expires_at: datetime,
    ) -> Iterator[RequestStateTransaction]:
        """Reserve guard state and durable audit atomically under one lock."""

        with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._prune_locked(now)
                self._assert_database_size()
                denial = self._guard_denial_locked(subject=subject, nonce_hash=nonce_hash, now=now)
                if denial is None:
                    connection.execute(
                        "INSERT INTO replay(nonce_hash, expires_at) VALUES (?, ?)",
                        (nonce_hash, _timestamp(expires_at)),
                    )
                    connection.execute(
                        "INSERT INTO rate_events(subject, created_at) VALUES (?, ?)",
                        (subject, _timestamp(now)),
                    )
                yield RequestStateTransaction(self, denial)
                self._assert_database_size()
                connection.execute("COMMIT")
            except BaseException:
                self._rollback_locked()
                raise

    def check_and_record(self, *, subject: str, nonce: str, now: datetime, expires_at: datetime) -> None:
        """Compatibility helper for tests that reserve guard state without an audit."""

        nonce_hash = sha256(nonce.encode("utf-8")).hexdigest()
        with self.request_transaction(
            subject=subject,
            nonce_hash=nonce_hash,
            now=now,
            expires_at=expires_at,
        ) as transaction:
            if transaction.denial is not None:
                raise transaction.denial

    def write_audit(self, audit: dict[str, Any], *, now: datetime) -> None:
        with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._prune_locked(now)
                self._write_audit_locked(audit, now=now)
                self._assert_database_size()
                connection.execute("COMMIT")
            except BaseException:
                self._rollback_locked()
                raise

    def audit_events(self) -> list[dict[str, Any]]:
        with self._lock:
            try:
                rows = self._require_connection().execute("SELECT payload FROM audit_events ORDER BY id").fetchall()
                return [json.loads(row[0]) for row in rows]
            except (json.JSONDecodeError, sqlite3.Error) as exc:
                raise RuntimeStateError() from exc

    def _guard_denial_locked(self, *, subject: str, nonce_hash: str, now: datetime) -> EvidenceError | None:
        connection = self._require_connection()
        if connection.execute("SELECT 1 FROM replay WHERE nonce_hash = ?", (nonce_hash,)).fetchone():
            return EvidenceError("replay_rejected", "request nonce was already used")
        replay_count = connection.execute("SELECT COUNT(*) FROM replay").fetchone()[0]
        if replay_count >= self.limits.max_replay_entries:
            return EvidenceError("replay_capacity_exceeded", "replay protection state is at capacity")
        subject_exists = connection.execute("SELECT 1 FROM rate_events WHERE subject = ? LIMIT 1", (subject,)).fetchone()
        subject_count = connection.execute("SELECT COUNT(DISTINCT subject) FROM rate_events").fetchone()[0]
        if subject_exists is None and subject_count >= self.limits.max_rate_subjects:
            return EvidenceError("rate_limit_unavailable", "rate limit state is at capacity")
        cutoff = _timestamp(now - timedelta(hours=1))
        request_count = connection.execute(
            "SELECT COUNT(*) FROM rate_events WHERE subject = ? AND created_at > ?", (subject, cutoff)
        ).fetchone()[0]
        if request_count >= self.limits.max_requests_per_hour:
            return EvidenceError("rate_limited", "caller exceeded the approved runtime request rate")
        return None

    def _write_audit_locked(self, audit: dict[str, Any], *, now: datetime) -> None:
        try:
            safe_audit = sanitize_audit(audit)
            payload = canonical_json(safe_audit)
            payload_bytes = len(payload.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise RuntimeStateError() from exc
        connection = self._require_connection()
        count = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        total_bytes = connection.execute("SELECT COALESCE(SUM(payload_bytes), 0) FROM audit_events").fetchone()[0]
        if count >= self.limits.max_audit_records or total_bytes + payload_bytes > self.limits.max_audit_bytes:
            raise RuntimeStateError("audit_capacity_exceeded")
        connection.execute(
            "INSERT INTO audit_events(created_at, payload, payload_bytes) VALUES (?, ?, ?)",
            (_timestamp(now), payload, payload_bytes),
        )

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

    def _check_integrity(self) -> None:
        rows = self._require_connection().execute("PRAGMA integrity_check").fetchall()
        if rows != [("ok",)]:
            raise RuntimeStateError()

    def _prune_locked(self, now: datetime) -> None:
        connection = self._require_connection()
        connection.execute("DELETE FROM replay WHERE expires_at <= ?", (_timestamp(now),))
        connection.execute("DELETE FROM rate_events WHERE created_at <= ?", (_timestamp(now - timedelta(hours=1)),))
        connection.execute(
            "DELETE FROM audit_events WHERE created_at <= ?", (_timestamp(now - self.limits.audit_retention),)
        )

    def _assert_database_size(self) -> None:
        connection = self._require_connection()
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        physical_main = self.path.stat().st_size if self.path.exists() else 0
        physical_auxiliary = sum(candidate.stat().st_size for candidate in self._database_files()[1:] if candidate.exists())
        total = max(physical_main, page_count * page_size) + physical_auxiliary
        if total > self.limits.max_database_bytes:
            raise RuntimeStateError("state_capacity_exceeded")

    def _database_files(self) -> tuple[Path, Path, Path]:
        return self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")

    def _rollback_locked(self) -> None:
        connection = self._connection
        if connection is not None and connection.in_transaction:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass

    def _close_on_error(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeStateError()
        return self._connection


def sanitize_audit(audit: dict[str, Any]) -> dict[str, Any]:
    """Persist only deterministic audit fields, never evidence or transport detail."""

    allowed = {
        "event_time",
        "request_id",
        "caller_identity",
        "kubernetes_subject",
        "rate_subject",
        "policy_subject",
        "authentication_method",
        "token_audience",
        "nonce_sha256",
        "operation",
        "target",
        "time_range",
        "evidence_kinds",
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
    current = value.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = current - epoch
    return ((delta.days * 86_400 + delta.seconds) * 1_000_000) + delta.microseconds
