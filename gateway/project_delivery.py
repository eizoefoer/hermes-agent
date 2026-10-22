"""Project authority bound to a stream's captured origin, including drafts and edits."""
from gateway.project_routing import check_delivery, record_reply_anchor, route_of


class ProjectDeliveryAdapter:
    def __init__(self, adapter, source):
        from gateway.session_identity import replace_source
        self._adapter = adapter
        self._source = replace_source(source)

    def __getattr__(self, name):
        method = getattr(self._adapter, name)
        if name not in {'send', 'edit_message', 'send_draft', 'send_stream_frame'}:
            return method
        async def deliver(*args, **kwargs):
            from gateway.platforms.event import MessageEvent
            from hermes_state_runtime import RuntimeStoreError
            chat = kwargs.get('chat_id', args[0] if args else None)
            metadata = kwargs.get('metadata') or {}
            if (str(chat) != str(self._source.chat_id)
                    or metadata.get('thread_id') is not None
                    and str(metadata['thread_id']) != str(self._source.thread_id)):
                raise RuntimeStoreError('permission_denied')
            check_delivery(self._adapter.gateway_runner, self._source)
            result = await method(*args, **kwargs)
            await record_reply_anchor(self._adapter.gateway_runner,
                                      MessageEvent(text='', source=self._source), result)
            return result
        return deliver


def project_delivery_adapter(adapter, source):
    return ProjectDeliveryAdapter(adapter, source) if route_of(source) is not None else adapter
