import asyncio
import json
import time
from functools import reduce, wraps

import click
import structlog
from structlog.contextvars import merge_contextvars

from rocketchat_async_api import RocketChat


class elapsedtime(object):
    """Context manager for timing operations.

    Example:

        with elapsedtime("sleep"):
            time.sleep(1)

        >>> sleep took 1.001 seconds ( 0.001 seconds)
    
    Args:
        operation (str): Informal name given to the operation.
        rounding (int): Number of decimal seconds to include in output.

    Returns:
        :obj:`ContextManager`: The context manager itself.
    """

    def __init__(self, operation, rounding=3):
        self.operation = operation
        self.rounding = rounding

    def __enter__(self):
        self.start_time_real = time.monotonic()
        self.start_time_cpu = time.process_time()
        return self

    def __exit__(self, type, value, traceback):
        elapsed_real = time.monotonic() - self.start_time_real
        elapsed_cpu = time.process_time() - self.start_time_cpu
        print(
            self.operation,
            "took",
            round(elapsed_real, self.rounding),
            "seconds",
            "(",
            round(elapsed_cpu, self.rounding),
            "seconds",
            ")",
        )


def async_to_sync(f):
    """Decorator to run an async function to completion.
    Example:
        @async_to_sync
        async def sleepy(seconds):
            await sleep(seconds)
        sleepy(5)

    Args:
        f (async function): The async function to wrap and make synchronous.
    Returns:
        :obj:`sync function`: The syncronhous function wrapping the async one.
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        future = asyncio.ensure_future(f(*args, **kwargs))
        try:
            return loop.run_until_complete(future)
        except asyncio.exceptions.CancelledError:
            pass

    return wrapper


def shutdown_loop():
    for task in asyncio.all_tasks():
        task.cancel()


@click.command()
@click.option("--username", help="Username for RocketChat instance.")
@click.option(
    "--password",
    help="Password for RocketChat instance.",
    prompt=True,
    hide_input=True,
)
@click.option(
    "--server-url",
    default="wss://open.rocket.chat/websocket",
    help="Server url for RocketChat instance.",
)
@click.argument("command", default="list_commands")
@async_to_sync
async def main(username, password, server_url, command):
    async def on_login(data):
        # Fetch all rooms
        rooms = (await rocket.get_rooms())["update"]
        # Find channels
        rooms = filter(lambda room: room["t"] == "c", rooms)
        # Convert to dict, name --> user count
        def map_builder(dicty, room):
            dicty[room.get("fname", room["name"])] = room["usersCount"]
            return dicty

        rooms = reduce(map_builder, rooms, {})
        # Print the result
        print(json.dumps(rooms, indent=4, sort_keys=True))
        # Shutdown the loop
        shutdown_loop()

    rocket = RocketChat(
        username, password, server_url, on_login_callback=on_login
    )
    await rocket.start()


if __name__ == "__main__":
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            merge_contextvars,
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    main(auto_envvar_prefix="ROCKETCHAT_CLI")
