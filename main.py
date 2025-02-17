import asyncio
import logging
import nest_asyncio
from mongopersistence import MongoPersistence
from config import BOT_TOKEN, MONGODB_URI
from data_base import client
from jobs import restore_jobs
from datetime import datetime
from config import TIMEZONE
from pymongo.errors import ServerSelectionTimeoutError
from handlers import setup_handlers
from telegram.ext import Application


nest_asyncio.apply()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def main():
    logging.info(f"Bot started at: {datetime.now(TIMEZONE)}")

    try:
        await client.admin.command('ping')
        logging.info("MongoDB connection established successfully")

        persistence = MongoPersistence(
            mongo_url=MONGODB_URI,
            db_name="reminder_bot",
            name_col_bot_data="bot_persistence",
            create_col_if_not_exist=True,
            update_interval=30
        )

    except ServerSelectionTimeoutError:
        logging.error("MongoDB connection failed - check if MongoDB server is running")
        return
    except Exception as e:
        logging.error(f"Error during MongoPersistence initialization: {e}")
        return

    application = Application.builder().token(BOT_TOKEN).persistence(persistence).build()
    await restore_jobs(application)
    setup_handlers(application)
    await application.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
