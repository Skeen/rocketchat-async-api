import asyncio
import json
import sys
from uuid import uuid4
from functools import partial

import aiohttp
import structlog
from structlog.contextvars import (bind_contextvars, clear_contextvars,
                                   unbind_contextvars)


from rocketchat_async_api.eventsystem import EventSystem
from rocketchat_async_api.utils import shutdown_loop


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
        auto_login=True,
        auto_connect=True
    ):
        # Bind arguments
        self.username = username
        self.password = password
        self.server_url = server_url
        self.logger = logger or structlog.get_logger()
        self.on_login_callback = on_login_callback
        self.on_connect_callback = on_connect_callback
        self.on_fatal_error_callback = on_fatal_error_callback or shutdown_loop
        self.auto_login = auto_login
        self.auto_connect = auto_connect
        # Initialize state
        self.websocket_event_system = EventSystem(self.logger)
        self.result_event_system = EventSystem(self.logger)
        self.websocket = None
        # Install default handlers for ping and such
        self._register_default_message_handlers()
        self.logger.debug("RocketChat.__init__ ran")

        def arg_mapper(arg_to_keys, *args):
            if len(args) > len(arg_to_keys):
                raise ValueError("arg_mapper not specified")
            return dict(zip(arg_to_keys, args))

        def arg_to_kwargs(function, arg_to_keys):
            def new_function(*args, **kwargs):
                arg_map = arg_mapper(arg_to_keys, *args)
                new_kwargs = kwargs.copy()
                new_kwargs.update(arg_mapper(arg_to_keys, *args))
                return function(**new_kwargs)
            return new_function

        self._call_method = partial(
            arg_to_kwargs(self._send_message, ["method", "params"]),
            msg="method"
        )
        self._call_method_callback = partial(
            arg_to_kwargs(self._send_message_callback, ["method", "params", "callback"]),
            msg="method"
        )

        async def get_rooms_args(since=0):
            return [{"$date": since}]

        async def login_args(username, password):
            return [{
                "user": {"username": username},
                "password": password,
            }]

        async def send_message_args(message, room_id=None, room_name=None):
            if room_id is None and room_name is None:
                raise ValueError("Must provide either room_id or room_name")
            if room_id and room_name:
                raise ValueError("Cannot provide both room_id and room_name")
            if room_name:
                room_id = await self._room_name_to_id(room_name)
            return [{
                #"_id": str(uuid4()),
                "rid": room_id,
                "msg": message
            }]

        methods = {
            "get_rooms": {
                "method": "rooms/get",
                "params": get_rooms_args,
            },
            "login": {
                "method": "login",
                "params": login_args,
            },
            "send_message": {
                "method": "sendMessage",
                "params": send_message_args,
            }
        }

        def build_method(method_entry):
            async def method(*args, **kwargs):
                params = await method_entry["params"](*args, **kwargs)
                future = await self._call_method(
                    method=method_entry["method"], params=params,
                )
                data = await future
                if "error" in data:
                    raise ValueError(data["error"])
                return data["result"]
            return method

        for method_name, method_entry in methods.items():
            setattr(self, method_name, build_method(method_entry))

    async def _room_name_to_id(self, room_name):
        rooms = (await self.get_rooms())['update']
        rooms = filter(
            lambda room: room.get("fname", room["name"]) == room_name, rooms
        )
        room = next(rooms, None)
        if next(rooms, None) is not None:
            raise ValueError("Ambigious room_name, use room_id")
        return room['_id']

    async def subscribe_to_room_messages(self, room_id=None, room_name=None):
        if room_id is None and room_name is None:
            raise ValueError("Must provide either room_id or room_name")
        if room_id and room_name:
            raise ValueError("Cannot provide both room_id and room_name")
        if room_name:
            room_id = await self._room_name_to_id(room_name)
        future = await self._send_message(
            msg="sub",
            name="stream-room-messages",
            params=[
                room_id, False
            ]
        )
        data = await future
        return data

    async def _on_successful_login(self, data):
        self.logger.info("Successfully logged in!")
        if self.on_login_callback:
            await self.on_login_callback(data)

    def _get_message_id(self):
        message_id = str(uuid4())
        self.logger.debug(
            "Getting message_id", message_id=message_id
        )
        return message_id

    async def _send_message_callback(self, callback, **kwargs):
        message_id = str(self._get_message_id())
        message_payload = {
            "id": message_id,
            **kwargs
        }
        self.result_event_system.register_handler(message_id, callback)
        self.logger.debug("Sending message", payload=message_payload)
        await self.websocket.send_json(message_payload)

    async def _send_message(self, **kwargs):
        future = asyncio.Future()

        async def resolve_future(data):
            future.set_result(data)

        await self._send_message_callback(resolve_future, **kwargs)
        return future

    async def _answer_ping(self, data):
        self.logger.debug("Replying to ping challenge")
        await self.websocket.send_json({"msg": "pong"})

    async def _attempt_login(self, data):
        try:
            data = await self.login(self.username, self.password)
        except ValueError:
            await self.error_during_login(data)
            return
        await self._on_successful_login(data)

    async def _connected(self, data):
        if self.on_connect_callback:
            await self.on_connect_callback(data)
        if self.auto_login:
            await self._attempt_login(data)

    async def _noop_handler(self, data):
        self.logger.debug("Noop handler called")

    async def missing_message_result_handler(self, data):
        self.logger.error("No message result handler installed for message")

    async def _result_handler(self, data):
        message_id = data["id"]
        num_fired = self.result_event_system.fire_event(message_id, data)
        self.result_event_system.clear_handlers(message_id)
        if num_fired == 0:
            await self.missing_message_result_handler(data)

    def _register_default_message_handlers(self):
        default_handlers = {
            "ping": self._answer_ping,
            "connected": self._connected,
            "result": self._result_handler,
            #"updated": self._noop_handler,
            #"added": self._noop_handler,
        }
        self.logger.debug(
            "Registering default message handlers",
            handlers=list(default_handlers.keys()),
        )
        for message_type, callback in default_handlers.items():
            self.websocket_event_system.register_handler(
                message_type, callback
            )

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
            # bind_contextvars(data=data)

            if "msg" not in data:
                await self.websocket_message_missing_data_msg(data)
                continue

            num_fired = self.websocket_event_system.fire_event(
                event=data["msg"],
                data=data
            )
            if num_fired == 0:
                await self.missing_message_handler(data)

    async def start(self):
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.server_url) as websocket:
                self.websocket = websocket
                if self.auto_connect:
                    await self.connect()
                await self._message_loop()
