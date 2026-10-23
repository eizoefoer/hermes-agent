"""Telegram batch preparation for independently identified project inputs."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway.platforms.event import MessageEvent


class TelegramProjectRoutingMixin:
    def _text_batch_key(self, event: MessageEvent) -> str:
        """Session-scoped batching key; topic recovery first so DM-topic batches coalesce on the recovered lane."""
        from gateway.project_routing import adapter_routing_enabled
        if not adapter_routing_enabled(self, event):
            self._apply_topic_recovery(event)
        return super()._text_batch_key(event)

    async def _route_photo_event(self, msg, event: MessageEvent) -> None:
        """Album items debounce on media_group_id; singles go through the photo burst batcher."""
        if self._drop_unresolved(event):  # identity FIRST: the batch lane is derived from it
            return
        from gateway.project_routing import adapter_routing_enabled
        if adapter_routing_enabled(self, event):
            await self.handle_message(event)
            return
        media_group_id = getattr(msg, "media_group_id", None)
        if media_group_id:
            await self._queue_media_group_event(str(media_group_id), event)
        else:
            self._enqueue_photo_event(self._photo_batch_key(event, msg), event)

