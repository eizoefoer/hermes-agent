"""Signed Telegram approval API retained for deployed control-plane plugins.

Conversational Hermes approvals are implemented independently; this facade
exports only the signed repository/task approval contract.
"""

from gateway.signed_telegram_approval import (
    ApprovalStore,
    ResumeKind,
    ResumeRegistration,
    ResumeResult,
    UnknownResumeKindError,
    handle_callback,
    primary_keyboard,
    register_resume_handler,
    resume_request,
    scope_keyboard,
    validate_resume_registry,
)

__all__ = [
    "ApprovalStore",
    "ResumeKind",
    "ResumeRegistration",
    "ResumeResult",
    "UnknownResumeKindError",
    "handle_callback",
    "primary_keyboard",
    "register_resume_handler",
    "resume_request",
    "scope_keyboard",
    "validate_resume_registry",
]
