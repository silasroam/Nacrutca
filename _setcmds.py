import asyncio
from telegram import Bot, BotCommand

TOKEN = "8658441399:AAHUBgSOXzwqOtmZHgzqiPXh3eW1FDoQGxA"
CMDS = [BotCommand("start", "Открыть меню поддержки")]


async def main() -> None:
    b = Bot(TOKEN)
    res = await b.set_my_commands(CMDS)
    cur = await b.get_my_commands()
    print("set result:", res)
    print("current commands:", [(c.command, c.description) for c in cur])


if __name__ == "__main__":
    asyncio.run(main())