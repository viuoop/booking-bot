import asyncio
import logging

from aiogram import Bot, Dispatcher

import admin
import client
import db
from config import BOT_TOKEN, DB_PATH


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init(DB_PATH)
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(admin.router)  # admin before client; later tasks: master router goes before client too
    dp.include_router(client.router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
