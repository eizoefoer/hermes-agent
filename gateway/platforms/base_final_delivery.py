"""Final text delivery ledger and routing authorization for platform adapters."""
from __future__ import annotations

import logging
import asyncio
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from gateway.platforms.base import BasePlatformAdapter, SendResult
    from gateway.platforms.event import MessageEvent

logger = logging.getLogger('gateway.platforms.base')


class BaseFinalDeliveryMixin:
    async def send_final_ledgered(
        self, event: MessageEvent, session_key: str, text_content: str, metadata: dict[str, Any], *,
        reply_to: Optional[str], is_ephemeral_response: bool = False,
    ) -> tuple[SendResult, BasePlatformAdapter]:
        """The delivery-ledger bracket every final text goes through, on the CURRENT transport
        (a reconnect may have replaced this adapter): record the obligation before the send,
        send with retry, finalize from the result — so a refused final (flood control, a dead
        transport) leaves a ledger row the boot sweep / runtime redelivery can act on. ``event``
        supplies the source and the ledger identity (``ledger_message_id`` or ``message_id``).
        Returns the result with the adapter that sent it: that adapter owns ``result.message_id``
        (an ephemeral delete must go to the same transport)."""
        from gateway.platforms.base import stop_reply_clock
        from gateway.project_routing import check_delivery
        check_delivery(self.gateway_runner, event.source)
        delivery_adapter = self._final_delivery_adapter(event.source)
        logger.info("[%s] Sending response (%d chars) to %s", delivery_adapter.name,
                    len(text_content), event.source.chat_id)
        obligation_id = await self._record_delivery_obligation(
            event, session_key, text_content, delivery_adapter, is_ephemeral_response)
        if obligation_id is not None:
            await self._release_turn_marker(event)  # the ledger now owns the crash recovery
        from gateway.project_routing import delivery_authorization
        result = await delivery_adapter._send_with_retry(
            chat_id=event.source.chat_id, content=text_content, reply_to=reply_to, metadata=metadata,
            **delivery_authorization(self.gateway_runner, event.source))
        from gateway.project_routing import record_reply_anchor
        stop_reply_clock(delivery_adapter, event.source.chat_id, result)
        if obligation_id is not None:
            await self._finalize_delivery_obligation(obligation_id, result, event, delivery_adapter)
        await record_reply_anchor(self.gateway_runner, event, result)
        return result, delivery_adapter


    async def _record_delivery_obligation(
        self, event: MessageEvent, session_key: str, text_content: str,
        delivery_adapter: BasePlatformAdapter, is_ephemeral_response: bool) -> Optional[str]:
        """Ledger the final response BEFORE the send so a crash before platform ACK redelivers on
        next boot; best-effort, skips slash-command and ephemeral replies. Returns the obligation id
        or None."""
        if is_ephemeral_response or str(event.text or "").lstrip().startswith(
            ("/", self.typed_command_prefix or "!")):
            return None
        try:
            from gateway.delivery_ledger import (
                compute_obligation_id, ledger_enabled, mark_attempting, record_obligation)
            if not await asyncio.to_thread(ledger_enabled):
                return None
            source = event.source
            # ``ledger_message_id`` wins when set: a queued chain's final answers the last message
            # of the chain, not the event that opened it (see ``MessageEvent.ledger_message_id``).
            _ledger_id = getattr(event, "ledger_message_id", None)
            if _ledger_id is None:
                _ledger_id = getattr(event, "message_id", "")
            obligation_id = compute_obligation_id(
                session_key, str(_ledger_id or ""), text_content)
            await asyncio.to_thread(
                record_obligation, obligation_id=obligation_id, session_key=session_key,
                platform=str(getattr(source.platform, "value", source.platform)),
                chat_id=source.chat_id, thread_id=getattr(source, "thread_id", None),
                content=text_content,
                adapter_profile=getattr(delivery_adapter, "_owner_profile", None))
            from gateway.project_routing import link_delivery_obligation
            await asyncio.to_thread(link_delivery_obligation, self.gateway_runner, event, obligation_id)
            await asyncio.to_thread(mark_attempting, obligation_id)
            return obligation_id
        except Exception:
            logger.debug("delivery ledger record failed", exc_info=True)
            return None

