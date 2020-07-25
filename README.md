## rocketchat-async-api
Python API wrapper for [Rocket.Chat](https://docs.rocket.chat/api/realtime-api)

### Installation
- From pypi:
`pip3 install rocketchat_async_api`
- From GitHub:
Clone our repository and `python3 setup.py install`

### Requirements
- [aiohttp](https://github.com/aio-libs/aiohttp)
- [structlog](https://github.com/hynek/structlog)

### Usage
```python
from pprint import pprint
from rocketchat_async_api import RocketChat

async def on_login(_):
    pprint(await rocket.me())
    pprint(await rocket.channels_list())
    pprint(await rocket.chat_post_message('good news everyone!', channel='GENERAL', alias='Farnsworth'))
    pprint(await rocket.channels_history('GENERAL', count=5))

rocket = RocketChat('user', 'pass', 'wss://open.rocket.chat/websocket', on_login_callback=on_login)
await rocket.start()

```

*note*: every method returns a dict

### API coverage
Almost none of the API methods are implemented. If you are interested in a specific call just open an issue or open a pull request.

### Tests
We are not actively testing :(

### Contributing
You can contribute by doing Pull Requests. (It may take a while to merge your code but if it's good it will be merged). Please, try to implement tests for all your code and use a black to format your code.

Reporting bugs and asking for features is also contributing ;) Feel free to help us grow by registering issues.

We hang out [here](https://open.rocket.chat/channel/python_rocketchat_async_api) if you want to talk. 
