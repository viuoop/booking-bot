import asyncio
import logging

from aiogram import Bot, Dispatcher

import client
import db
from config import BOT_TOKEN, DB_PATH


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init(DB_PATH)
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(client.router)  # later tasks: include master/admin routers before this one
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
