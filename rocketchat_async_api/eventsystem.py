import asyncio
from collections import defaultdict
from uuid import uuid4

from structlog import get_logger
from structlog.contextvars import (bind_contextvars)


class EventSystem:
    def __init__(self, logger=None, loop=None):
        self.handlers = defaultdict(dict)
        self.logger = logger or get_logger()
        self.loop = loop or asyncio.get_event_loop()

    def num_handlers(self, event=None):
        if event:
            return len(self.handlers[event])
        return sum(map(self.num_handlers, self.handlers.keys()))

    def register_handler(self, event, callback, register_id=None):
        register_id = register_id or uuid4()
        self.logger.debug(
            "Registering message handler",
            handler_event=event,
            register_id=register_id,
            count=self.num_handlers()
        )
        self.handlers[event][register_id] = callback
        return register_id

    def unregister_handler(self, event, register_id):
        self.logger.debug(
            "Unregistering message handler",
            handler_event=event,
            register_id=register_id,
            count=self.num_handlers()
        )
        del self.handlers[event][register_id]

    def clear_handlers(self, event):
        handler_register_ids = list(self.handlers[event].keys())
        for register_id in handler_register_ids:
            self.unregister_handler(event, register_id)

    def fire_event(self, event, data):
        handlers = self.handlers[event]

        bind_contextvars(handler_event=event)
        for register_id, callback in handlers.items():
            bind_contextvars(register_id=register_id)
            self.logger.debug("Scheduled handler")
            self.loop.call_soon(
                lambda callback, data: asyncio.ensure_future(
                    callback(data)
                ),
                callback,
                data,
            )
        return len(handlers)
