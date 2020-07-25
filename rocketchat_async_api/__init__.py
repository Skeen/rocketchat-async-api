import asyncio
import json
import sys

import aiohttp
import structlog
from structlog.contextvars import (bind_contextvars, clear_contextvars,
                                   unbind_contextvars)


async def shutdown_loop():
    for task in asyncio.all_tasks():
        task.cancel()


class RocketChat:
    def __init__(
        self,
        username,
        password,
        server_url,
        logger=None,
        on_login_callback=None,
        on_connect_callback=None,
        on_fatal_error_callback=None,
    ):
        self.username = username
        self.password = password
        self.server_url = server_url
        self.logger = logger or structlog.get_logger()
        self.on_login_callback = on_login_callback
        self.on_connect_callback = on_connect_callback
        self.on_fatal_error_callback = on_fatal_error_callback or shutdown_loop
        self.auto_login = True
        self.auto_connect = True

        self.message_handlers = {}
        self.message_result_handlers = {}
        self.last_message_id = 0
        self.websocket = None

        self._register_default_message_handlers()

        self.logger.debug("RocketChat.__init__ ran")

    async def get_rooms(self):
        future = await self._call_method(
            method="rooms/get", params=[{"$date": 0}],
        )
        data = await future
        return data["result"]

    async def _on_successful_login(self, data):
        self.logger.info("Successfully logged in!")
        if self.on_login_callback:
            await self.on_login_callback(data)

    def _get_message_id(self):
        self.last_message_id += 1
        self.logger.debug(
            "Getting message_id", message_id=self.last_message_id
        )
        return self.last_message_id

    def register_message_handler(self, message_type, callback):
        self.logger.debug(
            "Registering message handler",
            message_type=message_type,
            count=len(self.message_handlers),
        )
        self.message_handlers[message_type] = callback

    def _register_result_handler(self, message_id, callback):
        self.logger.debug(
            "Registering result handler",
            message_id=message_id,
            count=len(self.message_result_handlers),
        )
        self.message_result_handlers[message_id] = callback

    def _unregister_result_handler(self, message_id):
        self.logger.debug(
            "Unregistering result handler",
            message_id=message_id,
            count=len(self.message_result_handlers),
        )
        del self.message_result_handlers[message_id]

    async def _call_method_callback(self, method, params, callback):
        message_id = str(self._get_message_id())
        message_payload = {
            "msg": "method",
            "method": method,
            "id": message_id,
            "params": params,
        }
        self._register_result_handler(message_id, callback)
        self.logger.debug("Sending method call", payload=message_payload)
        await self.websocket.send_json(message_payload)

    async def _call_method(self, method, params):
        future = asyncio.Future()

        async def resolve_future(data):
            future.set_result(data)

        await self._call_method_callback(method, params, resolve_future)
        return future

    async def _answer_ping(self, data):
        self.logger.debug("Replying to ping challenge")
        await self.websocket.send_json({"msg": "pong"})

    async def login(self):
        future = await self._call_method(
            method="login",
            params=[
                {
                    "user": {"username": self.username},
                    "password": self.password,
                }
            ],
        )
        data = await future
        if "error" in data:
            await self.error_during_login(data)
            return
        if "result" not in data:
            await self.missing_result_during_login(data)
            return
        await self._on_successful_login(data)

    async def _attempt_login(self, data):
        await self.login()

    async def _connected(self, data):
        if self.on_connect_callback:
            await self.on_connect_callback(data)
        if self.auto_login:
            await self._attempt_login(data)

    async def _noop_handler(self, data):
        self.logger.debug("Noop handler called")

    async def missing_message_result_handler(self, data):
        self.logger.error(
            "No message result handler installed for message",
            result_handlers=list(self.message_result_handlers.keys()),
        )

    async def _result_handler(self, data):
        message_id = data["id"]
        bind_contextvars(message_id=message_id)
        try:
            result_handler = self.message_result_handlers[message_id]
            self._unregister_result_handler(message_id)
        except KeyError:
            await self.missing_message_result_handler(data)
            return
        self.logger.debug("Running message result handler for message result")
        await result_handler(data)

    def _register_default_message_handlers(self):
        default_handlers = {
            "ping": self._answer_ping,
            "connected": self._connected,
            "result": self._result_handler,
            "updated": self._noop_handler,
            "added": self._noop_handler,
        }
        self.logger.debug(
            "Registering default message handlers",
            handlers=list(default_handlers.keys()),
        )
        for message_type, callback in default_handlers.items():
            self.register_message_handler(message_type, callback)

    async def connect(self):
        connect_payload = {"msg": "connect", "version": "1", "support": ["1"]}
        # Send connect payload
        await self.websocket.send_json(connect_payload)

    async def websocket_message_error(self, websocket_message):
        self.logger.error("Websocket message error")

    async def websocket_message_not_parsable(self, websocket_message, exc):
        self.logger.error("Websocket message not JSON parsable", exception=exc)

    async def error_during_login(self, data):
        self.logger.critical("Fatal error occured during login")
        await self.on_fatal_error_callback()

    async def missing_result_during_login(self, data):
        self.logger.critical("Expected result in data for successful login")
        await self.on_fatal_error_callback()

    async def websocket_message_missing_data_msg(self, data):
        # Expected missing msg
        if data == {"server_id": "0"}:
            return
        self.logger.error("Websocket message missing 'msg' entry")

    async def missing_message_handler(self, data):
        self.logger.warn("No message handler installed for message")

    async def _message_loop(self):
        async for message in self.websocket:
            # Clear context vars for logging
            clear_contextvars()
            bind_contextvars(message=message)

            if message.type == aiohttp.WSMsgType.ERROR:
                await self.websocket_message_error(message)
                continue

            try:
                data = json.loads(message.data)
            except JSONDecodeError as exception:
                await self.websocket_message_not_parsable(message, exception)
                continue

            unbind_contextvars("message")
            bind_contextvars(data=data)

            if "msg" not in data:
                await self.websocket_message_missing_data_msg(data)
                continue

            bind_contextvars(message_type=data["msg"])
            try:
                message_handler = self.message_handlers[data["msg"]]
            except KeyError:
                await self.missing_message_handler(data)
                continue

            self.logger.debug("Scheduled running message handler for message")
            loop = asyncio.get_event_loop()
            loop.call_soon(
                lambda message_handler, data: asyncio.ensure_future(
                    message_handler(data)
                ),
                message_handler,
                data,
            )

    async def start(self):
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.server_url) as websocket:
                self.websocket = websocket
                if self.auto_connect:
                    await self.connect()
                await self._message_loop()
