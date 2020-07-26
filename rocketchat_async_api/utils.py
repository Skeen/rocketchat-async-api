import asyncio

async def shutdown_loop():
    for task in asyncio.all_tasks():
        task.cancel()
