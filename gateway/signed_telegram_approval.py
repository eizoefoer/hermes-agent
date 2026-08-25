#!/usr/bin/env python3
"""Compatibility contract for signed repository/task Telegram approvals.

This preserves the durable ``pa:`` protocol consumed by deployed control-plane
plugins.  Conversational Hermes approvals continue to use
``gateway.approval_store`` and ``TelegramApprovalService``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable


APPROVAL_VERSION = "1"
CALLBACK_PREFIX = "pa"
DEFAULT_TTL_SECONDS = 24 * 60 * 60
DEFAULT_HOME = Path.home() / ".hermes" / "approvals"
VALID_DECISIONS = {"approve", "deny", "once", "task", "repo"}


class ResumeKind(str, Enum):
    GITHUB_WORKFLOW = "github_workflow"
    HERMES_SESSION = "hermes_session"
    ALWAYS_ON_DYNAMIC_PERMISSION = "always_on_dynamic_permission"


@dataclass(frozen=True)
class ResumeResult:
    task_id: str
    ownership_established: bool
    state: str
    message: str


@dataclass(frozen=True)
class ResumeRegistration:
    handler: Callable[[dict[str, Any]], ResumeResult]
    transition: str


class UnknownResumeKindError(RuntimeError):
    pass


_RESUME_REGISTRY: dict[str, ResumeRegistration] = {}
_RESUME_TRANSITIONS = {
    ResumeKind.GITHUB_WORKFLOW.value: "approved_pending_resume -> workflow_queued",
    ResumeKind.HERMES_SESSION.value: "approved_pending_resume -> session_owned",
    ResumeKind.ALWAYS_ON_DYNAMIC_PERMISSION.value: "approved_pending_resume -> always_on_owned_or_blocked",
}


def register_resume_handler(kind: ResumeKind | str, handler: Callable[[dict[str, Any]], ResumeResult], *, transition: str) -> None:
    value = kind.value if isinstance(kind, ResumeKind) else str(kind)
    if value not in {item.value for item in ResumeKind}:
        raise ValueError(f"undeclared resume kind: {value}")
    if transition != _RESUME_TRANSITIONS.get(value):
        raise ValueError(f"resume kind {value} has no matching state transition")
    existing = _RESUME_REGISTRY.get(value)
    if existing and existing.handler is not handler:
        raise ValueError(f"resume handler already registered: {value}")
    _RESUME_REGISTRY[value] = ResumeRegistration(handler, transition)


def validate_resume_registry(*, required: set[str] | None = None) -> None:
    known = {item.value for item in ResumeKind}
    referenced = required or set(_RESUME_REGISTRY)
    unknown = referenced - known
    missing = referenced - set(_RESUME_REGISTRY)
    transitionless = {kind for kind in referenced if kind not in _RESUME_TRANSITIONS}
    if unknown or missing or transitionless:
        raise RuntimeError(
            f"invalid resume registry: unknown={sorted(unknown)} missing={sorted(missing)} "
            f"transitionless={sorted(transitionless)}"
        )


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _short_signature(secret: bytes, body: str) -> str:
    digest = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()[:10]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def verify_request_integrity(request: dict[str, Any]) -> None:
    payload = json.loads(str(request["resume_payload"]))
    bound = {
        "task_id": request["task_id"], "permission_request_id": request["permission_request_id"],
        "project": request["project"], "repository": request["repository"],
        "action": request["action"], "scope": request["scope"],
        "approval_version": request["approval_version"], "expires_at": request["expires_at"],
        "requesting_user_id": request["requesting_user_id"], "requesting_chat_id": request["requesting_chat_id"],
        "resume_kind": request["resume_kind"], "resume_payload": payload,
    }
    if not hmac.compare_digest(str(request["checksum"]), _checksum(bound)):
        raise RuntimeError("approval record checksum mismatch")


def current_project_context() -> tuple[str, str]:
    """Return the active Hermes project and canonical repository identity."""
    try:
        from agent.runtime_cwd import resolve_agent_cwd
        cwd = resolve_agent_cwd()
    except Exception:
        cwd = Path.cwd()
    project = cwd.name or "unknown"
    repository = f"local:{cwd.resolve()}"
    try:
        root = subprocess.check_output(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            text=True, stderr=subprocess.DEVNULL, timeout=3,
        ).strip()
        remote = subprocess.check_output(
            ["git", "-C", root, "remote", "get-url", "origin"],
            text=True, stderr=subprocess.DEVNULL, timeout=3,
        ).strip()
        project = Path(root).name
        normalized = remote.removesuffix(".git")
        if normalized.startswith("git@") and ":" in normalized:
            normalized = normalized.split(":", 1)[1]
        elif "://" in normalized:
            normalized = normalized.split("://", 1)[1].split("/", 1)[-1]
        repository = normalized
    except Exception:
        pass
    return project, repository


@dataclass(frozen=True)
class Resolution:
    outcome: str
    request: dict[str, Any] | None = None
    decision: str | None = None
    execute: bool = False


class ApprovalStore:
    def __init__(self, root: Path | None = None, now: Callable[[], float] = time.time):
        self.root = Path(root or os.environ.get("HERMES_APPROVAL_HOME", DEFAULT_HOME))
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.db_path = self.root / "approvals.sqlite3"
        self.secret_path = self.root / "callback-signing.key"
        self.now = now
        self.secret = self._load_secret()
        self._initialize()

    def _load_secret(self) -> bytes:
        try:
            value = self.secret_path.read_bytes()
        except FileNotFoundError:
            value = secrets.token_bytes(32)
            fd = os.open(self.secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(value)
        os.chmod(self.secret_path, 0o600)
        if len(value) < 32:
            raise RuntimeError("Telegram approval signing key is too short")
        return value

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    state TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY,
                    permission_request_id TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    project TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    action TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    approval_version TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    requesting_user_id TEXT NOT NULL,
                    requesting_chat_id TEXT NOT NULL,
                    message_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    decision TEXT,
                    decided_at REAL,
                    resume_kind TEXT NOT NULL,
                    resume_payload TEXT NOT NULL,
                    details TEXT NOT NULL,
                    resumed_at REAL,
                    resume_claimed_at REAL,
                    resume_error TEXT,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );
                CREATE TABLE IF NOT EXISTS grants (
                    grant_key TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    task_id TEXT,
                    action TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    requesting_user_id TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS callback_events (
                    callback_event_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES requests(request_id)
                );
                CREATE TABLE IF NOT EXISTS resume_events (
                    request_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    resume_kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES requests(request_id)
                );
                CREATE INDEX IF NOT EXISTS requests_status_idx ON requests(status, expires_at);
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(requests)")}
            if "resume_claimed_at" not in columns:
                connection.execute("ALTER TABLE requests ADD COLUMN resume_claimed_at REAL")
        os.chmod(self.db_path, 0o600)

    def create_request(
        self,
        *,
        task_id: str,
        permission_request_id: str | None,
        project: str,
        repository: str,
        action: str,
        scope: str,
        requesting_user_id: str,
        requesting_chat_id: str,
        resume_kind: str,
        resume_payload: dict[str, Any],
        details: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> dict[str, Any]:
        now = self.now()
        validate_resume_registry(required={resume_kind})
        request_id = secrets.token_hex(12)
        permission_request_id = permission_request_id or f"perm-{secrets.token_hex(12)}"
        bound = {
            "task_id": task_id,
            "permission_request_id": permission_request_id,
            "project": project,
            "repository": repository,
            "action": action,
            "scope": scope,
            "approval_version": APPROVAL_VERSION,
            "expires_at": now + max(60, int(ttl_seconds)),
            "requesting_user_id": str(requesting_user_id),
            "requesting_chat_id": str(requesting_chat_id),
            "resume_kind": resume_kind,
            "resume_payload": resume_payload,
        }
        checksum = _checksum(bound)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO tasks(task_id, project, repository, state, updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET state='waiting_approval', updated_at=excluded.updated_at",
                (task_id, project, repository, "waiting_approval", now),
            )
            connection.execute(
                """INSERT INTO requests(
                    request_id, permission_request_id, task_id, project, repository,
                    action, scope, approval_version, checksum, expires_at,
                    requesting_user_id, requesting_chat_id, resume_kind,
                    resume_payload, details, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    request_id, permission_request_id, task_id, project, repository,
                    action, scope, APPROVAL_VERSION, checksum, bound["expires_at"],
                    str(requesting_user_id), str(requesting_chat_id), resume_kind,
                    _canonical(resume_payload), details, now,
                ),
            )
            connection.commit()
        return self.get(request_id) or {}

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def bind_message(self, request_id: str, message_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE requests SET message_id=? WHERE request_id=? AND status='pending'",
                (str(message_id), request_id),
            )

    def callback(self, request_id: str, operation: str) -> str:
        if operation not in {"a", "d", "v", "s", "o", "t", "r", "c"}:
            raise ValueError("Unsupported approval operation")
        request = self.get(request_id)
        if not request:
            raise KeyError(request_id)
        body = f"{CALLBACK_PREFIX}:{operation}:{request_id}"
        signed_body = f"{body}:{request['approval_version']}:{request['checksum']}"
        return f"{body}:{_short_signature(self.secret, signed_body)}"

    def parse_callback(self, callback_data: str) -> tuple[str, str, str] | None:
        parts = callback_data.split(":")
        if len(parts) != 4 or parts[0] != CALLBACK_PREFIX:
            return None
        _, operation, request_id, signature = parts
        return operation, request_id, signature

    def has_grant(
        self, *, project: str, repository: str, task_id: str, action: str,
        scope: str, requesting_user_id: str,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM grants WHERE project=? AND repository=? AND action=?
                   AND scope=? AND requesting_user_id=? AND (task_id IS NULL OR task_id=?) LIMIT 1""",
                (project, repository, action, scope, str(requesting_user_id), task_id),
            ).fetchone()
        return row is not None

    def resolve(
        self,
        *,
        callback_data: str,
        caller_user_id: str,
        caller_chat_id: str,
        message_id: str,
    ) -> Resolution:
        parsed = self.parse_callback(callback_data)
        if not parsed:
            return Resolution("invalid")
        operation, request_id, signature = parsed
        decision_by_operation = {"a": "approve", "d": "deny", "o": "once", "t": "task", "r": "repo"}
        decision = decision_by_operation.get(operation)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if not row:
                connection.rollback()
                return Resolution("invalid")
            request = dict(row)
            try:
                verify_request_integrity(request)
            except (KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError):
                connection.rollback()
                return Resolution("invalid", request)
            body = f"{CALLBACK_PREFIX}:{operation}:{request_id}"
            signed_body = f"{body}:{request['approval_version']}:{request['checksum']}"
            if not hmac.compare_digest(signature, _short_signature(self.secret, signed_body)):
                connection.rollback()
                return Resolution("invalid", request)
            if str(request["requesting_user_id"]) != str(caller_user_id) or str(request["requesting_chat_id"]) != str(caller_chat_id):
                connection.rollback()
                return Resolution("unauthorized", request)
            if request["message_id"] and str(request["message_id"]) != str(message_id):
                connection.rollback()
                return Resolution("invalid", request)
            if request["approval_version"] != APPROVAL_VERSION:
                connection.rollback()
                return Resolution("stale", request)
            if float(request["expires_at"]) <= self.now():
                connection.execute("UPDATE requests SET status='expired' WHERE request_id=? AND status='pending'", (request_id,))
                connection.execute("UPDATE tasks SET state='blocked', updated_at=? WHERE task_id=?", (self.now(), request["task_id"]))
                connection.commit()
                return Resolution("expired", request)
            if operation in {"v", "s", "c"}:
                connection.rollback()
                return Resolution({"v": "details", "s": "scope", "c": "cancel_scope"}[operation], request)
            if request["status"] != "pending":
                connection.rollback()
                return Resolution("duplicate", request, request.get("decision"), False)
            if decision not in VALID_DECISIONS:
                connection.rollback()
                return Resolution("invalid", request)
            status = "denied" if decision == "deny" else "approved"
            task_state = "blocked" if decision == "deny" else "approved_pending_resume"
            now = self.now()
            connection.execute(
                "UPDATE requests SET status=?, decision=?, decided_at=? WHERE request_id=? AND status='pending'",
                (status, decision, now, request_id),
            )
            connection.execute("UPDATE tasks SET state=?, updated_at=? WHERE task_id=?", (task_state, now, request["task_id"]))
            if decision in {"task", "repo"}:
                grant_task = request["task_id"] if decision == "task" else None
                grant_key = _checksum({
                    "project": request["project"], "repository": request["repository"],
                    "task_id": grant_task, "action": request["action"],
                    "scope": request["scope"], "user": request["requesting_user_id"],
                })
                connection.execute(
                    "INSERT OR IGNORE INTO grants VALUES(?,?,?,?,?,?,?,?)",
                    (grant_key, request["project"], request["repository"], grant_task,
                     request["action"], request["scope"], request["requesting_user_id"], now),
                )
            connection.commit()
        request["status"] = status
        request["decision"] = decision
        return Resolution(status, request, decision, decision != "deny")

    def record_callback_event(self, request_id: str, callback_event_id: str, outcome: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO callback_events VALUES(?,?,?,?)",
                (str(callback_event_id), request_id, outcome, self.now()),
            )

    def mark_resumed(self, request_id: str, result: ResumeResult) -> None:
        now = self.now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT task_id,resume_kind,resumed_at FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if not row or row["resumed_at"] is not None:
                connection.rollback()
                return
            connection.execute(
                "UPDATE requests SET resumed_at=?,resume_error=NULL,resume_claimed_at=NULL WHERE request_id=?",
                (now, request_id),
            )
            connection.execute(
                "UPDATE tasks SET state=?, updated_at=? WHERE task_id=?",
                (result.state, now, row["task_id"]),
            )
            connection.execute(
                """INSERT INTO resume_events VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(request_id) DO UPDATE SET state=excluded.state,detail=excluded.detail,updated_at=excluded.updated_at""",
                (request_id, row["task_id"], row["resume_kind"], result.state, result.message, now, now),
            )
            connection.commit()

    def mark_resume_blocked(self, request_id: str, error_code: str, detail: str) -> None:
        now = self.now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT task_id,resume_kind FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if not row:
                connection.rollback()
                return
            connection.execute(
                "UPDATE requests SET resume_error=?,resume_claimed_at=NULL WHERE request_id=? AND resumed_at IS NULL",
                (error_code, request_id),
            )
            connection.execute("UPDATE tasks SET state='approved_resume_blocked',updated_at=? WHERE task_id=?", (now, row["task_id"]))
            connection.execute(
                """INSERT INTO resume_events VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(request_id) DO UPDATE SET state=excluded.state,detail=excluded.detail,updated_at=excluded.updated_at""",
                (request_id, row["task_id"], row["resume_kind"], "approved_resume_blocked", detail[:1000], now, now),
            )
            connection.commit()

    def reopen_failed_resume(self, request_id: str) -> bool:
        """Reopen a retained legacy approval without changing its decision."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """UPDATE requests SET resumed_at=NULL,resume_claimed_at=NULL,resume_error=NULL
                   WHERE request_id=? AND status='approved' AND resume_error IS NOT NULL""",
                (request_id,),
            ).rowcount
            connection.commit()
        return changed == 1

    def claim_resume(self, request_id: str, lease_seconds: int = 300) -> bool:
        now = self.now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE requests SET resume_claimed_at=? WHERE request_id=?
                   AND status='approved' AND resumed_at IS NULL
                   AND (resume_claimed_at IS NULL OR resume_claimed_at<?)""",
                (now, request_id, now - lease_seconds),
            )
            connection.commit()
        return cursor.rowcount == 1

    def pending_resumes(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM requests WHERE status='approved' AND resumed_at IS NULL ORDER BY decided_at"
            ).fetchall()
        return [dict(row) for row in rows]


def primary_keyboard(store: ApprovalStore, request_id: str) -> dict[str, Any]:
    return {"inline_keyboard": [
        [{"text": "✅ Approve", "callback_data": store.callback(request_id, "a")},
         {"text": "❌ Deny", "callback_data": store.callback(request_id, "d")}],
        [{"text": "View details", "callback_data": store.callback(request_id, "v")},
         {"text": "Change scope", "callback_data": store.callback(request_id, "s")}],
    ]}


def scope_keyboard(store: ApprovalStore, request_id: str) -> dict[str, Any]:
    return {"inline_keyboard": [
        [{"text": "Approve once", "callback_data": store.callback(request_id, "o")}],
        [{"text": "Approve for this task", "callback_data": store.callback(request_id, "t")}],
        [{"text": "Always allow for this repository", "callback_data": store.callback(request_id, "r")}],
        [{"text": "Cancel", "callback_data": store.callback(request_id, "c")}],
    ]}


def _resume_github_workflow(request: dict[str, Any]) -> ResumeResult:
    payload = json.loads(request["resume_payload"])
    command = ["gh", "workflow", "run", payload["workflow"], "--repo", request["repository"], "--ref", payload.get("ref", "main")]
    for key, value in payload.get("inputs", {}).items():
        command.extend(["-f", f"{key}={value}"])
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    return ResumeResult(request["task_id"], True, "workflow_queued", f"Work resumed for task {request['task_id']}.")


def _resume_hermes_session(request: dict[str, Any]) -> ResumeResult:
    payload = json.loads(request["resume_payload"])
    from tools.approval import resolve_gateway_approval
    count = resolve_gateway_approval(payload["session_key"], payload.get("choice", "once"))
    if not count:
        raise RuntimeError("Hermes task is no longer waiting")
    return ResumeResult(request["task_id"], True, "session_owned", f"Work resumed for task {request['task_id']}.")


def resume_request(request: dict[str, Any]) -> ResumeResult:
    kind = str(request["resume_kind"])
    verify_request_integrity(request)
    registration = _RESUME_REGISTRY.get(kind)
    if not registration:
        raise UnknownResumeKindError(f"unregistered resume kind: {kind}")
    return registration.handler(request)


register_resume_handler(ResumeKind.GITHUB_WORKFLOW, _resume_github_workflow, transition=_RESUME_TRANSITIONS[ResumeKind.GITHUB_WORKFLOW.value])
register_resume_handler(ResumeKind.HERMES_SESSION, _resume_hermes_session, transition=_RESUME_TRANSITIONS[ResumeKind.HERMES_SESSION.value])


async def handle_callback(adapter: Any, query: Any) -> bool:
    data = str(getattr(query, "data", "") or "")
    if not data.startswith(f"{CALLBACK_PREFIX}:"):
        return False
    store = ApprovalStore()
    await query.answer(text="Processing...")
    message = getattr(query, "message", None)
    caller = getattr(query, "from_user", None)
    resolution = store.resolve(
        callback_data=data,
        caller_user_id=str(getattr(caller, "id", "")),
        caller_chat_id=str(getattr(message, "chat_id", "")),
        message_id=str(getattr(message, "message_id", "")),
    )
    request = resolution.request or {}
    if request.get("request_id"):
        store.record_callback_event(str(request["request_id"]), str(getattr(query, "id", "") or _checksum({"data": data})), resolution.outcome)
    if resolution.outcome == "unauthorized":
        await adapter._bot.send_message(chat_id=getattr(message, "chat_id", None), text="This approval belongs to another Telegram user.")
        return True
    if resolution.outcome in {"invalid", "stale", "expired"}:
        label = "⌛ Expired" if resolution.outcome == "expired" else "This approval is invalid or stale. No action was taken."
        try:
            await query.edit_message_text(text=label, reply_markup=None)
        except Exception:
            pass
        await adapter._bot.send_message(chat_id=getattr(message, "chat_id", None), text=label)
        return True
    if resolution.outcome == "details":
        await adapter._bot.send_message(chat_id=message.chat_id, text=str(request.get("details", "No details available."))[:4096])
        return True
    if resolution.outcome == "scope":
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        rows = [[InlineKeyboardButton(item["text"], callback_data=item["callback_data"]) for item in row] for row in scope_keyboard(store, request["request_id"])["inline_keyboard"]]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows))
        return True
    if resolution.outcome == "cancel_scope":
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        rows = [[InlineKeyboardButton(item["text"], callback_data=item["callback_data"]) for item in row] for row in primary_keyboard(store, request["request_id"])["inline_keyboard"]]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows))
        return True
    if resolution.outcome == "duplicate":
        return True
    project = str(request.get("project") or "Project")
    action = str(request.get("action") or "permission").replace("_", " ")
    label = f"❌ Denied — {project}: {action}" if resolution.outcome == "denied" else f"⚠️ Approved — resume pending — {project}: {action}"
    try:
        await query.edit_message_text(text=label, reply_markup=None)
    except Exception:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    if not resolution.execute:
        return True
    if not store.claim_resume(request["request_id"]):
        return True

    async def _resume() -> None:
        try:
            result = await asyncio.to_thread(resume_request, request)
            if not result.ownership_established:
                raise RuntimeError("resume handler did not establish ownership")
            store.mark_resumed(request["request_id"], result)
            await query.edit_message_text(text=f"✅ Approved — work resumed — {project}: {action}", reply_markup=None)
            await adapter._bot.send_message(chat_id=message.chat_id, text=result.message[:4096])
        except Exception as exc:
            code = "unknown_resume_kind" if isinstance(exc, UnknownResumeKindError) else "resume_handler_error"
            store.mark_resume_blocked(request["request_id"], code, f"{type(exc).__name__}: {exc}")
            try:
                await query.edit_message_text(text=f"⚠️ Approved — resume pending — {project}: {action}", reply_markup=None)
            except Exception:
                pass
            await adapter._bot.send_message(
                chat_id=message.chat_id,
                text=f"⚠️ {project} permission was saved, but task resumption is blocked by an internal dispatcher error. Your approval is retained; no further action is required.",
            )

    asyncio.create_task(_resume())
    return True
