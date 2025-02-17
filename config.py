import os
from datetime import time
import pytz
from dotenv import load_dotenv

load_dotenv()

MONGODB_USERNAME = os.getenv('MONGODB_ROOT_USERNAME')
MONGODB_PASSWORD = os.getenv('MONGODB_ROOT_PASSWORD')
MONGODB_HOST = os.getenv('MONGODB_HOST', 'mongodb')
MONGODB_PORT = os.getenv('MONGODB_PORT', 27017)
MONGODB_DATABASE = os.getenv('MONGODB_DATABASE')

BOT_TOKEN = os.getenv("BOT_TOKEN")
CONNECTION_STRING = "mongodb://{username}:{password}@{host}:{port}/".format(
    username=MONGODB_USERNAME,
    password=MONGODB_PASSWORD,
    host=MONGODB_HOST,
    port=27017
)

MONGODB_URI = CONNECTION_STRING
TIMEZONE = pytz.timezone("Asia/Tashkent")
REGISTRATION_INTERVAL = 1 * 300  # 5 minutes
DAILY_REMINDER_TIME = time(11, 00)
REGISTRATION_FORMAT = "Task:"