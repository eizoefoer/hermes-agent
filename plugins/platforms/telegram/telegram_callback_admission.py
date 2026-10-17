"""Telegram callback context and authorization before protocol dispatch."""
from typing import Any


class TelegramCallbackAdmissionMixin:
    @staticmethod
    def _callback_ctx(query) -> dict[str, Any]:
        """Chat/thread/user context of a button tap, for the callback auth gate."""
        query_message = getattr(query, "message", None)
        query_chat = getattr(query_message, "chat", None)
        return {
            "chat_id": getattr(query_message, "chat_id", None), "chat_type": getattr(query_chat, "type", None),
            "thread_id": getattr(query_message, "message_thread_id", None), "user_name": getattr(query.from_user, "first_name", None)}

    async def _callback_authorized(self, query, cb: dict[str, Any], denial_text: str) -> bool:
        """Gate a button tap on the callback allowlist; answers ``denial_text`` when refused."""
        if self._is_callback_user_authorized(
            str(getattr(query.from_user, "id", "")), chat_id=cb["chat_id"],
            chat_type=str(cb["chat_type"]) if cb["chat_type"] is not None else None,
            thread_id=str(cb["thread_id"]) if cb["thread_id"] is not None else None, user_name=cb["user_name"]):
            return True
        await query.answer(text=denial_text)
        return False

    async def _handle_signed_approval_callback(self, query, data: str, cb: dict[str, Any]) -> None:
        """Preserve the signed control-plane protocol behind the current callback auth gate."""
        from plugins.platforms.telegram.adapter import _unauthorized
        if not await self._callback_authorized(query, cb, _unauthorized()):
            return
        # Use the public compatibility seam: deployed plugins install their denial bridge here.
        from gateway import telegram_approval
        await telegram_approval.handle_callback(self, query)
