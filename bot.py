import asyncio
import logging

from aiogram import Bot, Dispatcher

import admin
import client
import db
import master
import reminders
from config import BOT_TOKEN, DB_PATH


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init(DB_PATH)
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(admin.router)
    dp.include_router(master.router)  # before client: takes /start m_<token> and the linked master's /start
    dp.include_router(client.router)
    task = asyncio.create_task(reminders.run(bot))  # keep the reference or the task may be garbage-collected
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
