from telegram import Update, ReactionTypeEmoji
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    ChatMemberHandler,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)
from data_base import (
    groups_collection,
    create_registration_reminder,
    store_job
)
from utils import send_and_cleanup_message
from jobs import send_registration_reminders, check_new_member_update
from config import REGISTRATION_INTERVAL
import logging
import re
from datetime import datetime
import asyncio

logger = logging.getLogger(__name__)


async def added_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_member = update.my_chat_member
        chat = chat_member.chat
        new_status = chat_member.new_chat_member.status
        old_status = chat_member.old_chat_member.status

        logging.info(f"Chat ID: {chat.id}, Title: {chat.title}")
        logging.info(f"New Status: {new_status}, Old Status: {old_status}")

        if new_status == ChatMemberStatus.ADMINISTRATOR and old_status != ChatMemberStatus.ADMINISTRATOR:
            job_name = await create_registration_reminder(chat.id, REGISTRATION_INTERVAL)

            await send_and_cleanup_message(
                context,
                chat.id,
                text=("Спасибо за предоставление прав администратора! Давайте начнем процесс регистрации. "
                     "Пожалуйста, отправьте свои апдейты. Включите #апдейт в любое место сообщения – "
                     "всё, что после него, будет записано как апдейт."),
                cleanup_delay=500
            )

            total_members = (await chat.get_member_count()) - 1

            context.job_queue.run_repeating(
                send_registration_reminders,
                interval=REGISTRATION_INTERVAL,
                data={"group_id": chat.id, "total_members": total_members},
                name=job_name
            )

    except Exception as e:
        logging.error(f"Error in added_to_group: {e}")


async def new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.message and update.message.new_chat_members:
            for new_member in update.message.new_chat_members:
                if new_member.id == context.bot.id:
                    continue

                group_id = update.message.chat_id
                current_date = datetime.now().strftime('%Y-%m-%d')

                max_retries = 3
                retry_count = 0
                while retry_count < max_retries:
                    try:
                        existing_member = await asyncio.wait_for(
                            groups_collection.find_one({
                                "group_id": group_id,
                                f"dates.{current_date}.members": {
                                    "$elemMatch": {"user_id": new_member.id}
                                }
                            }),
                            timeout=10.0
                        )

                        if not existing_member:
                            user_data = {
                                "user_id": new_member.id,
                                "username": new_member.username,
                                "first_name": new_member.first_name,
                                "timestamp": datetime.now()
                            }

                            await asyncio.wait_for(
                                groups_collection.update_one(
                                    {"group_id": group_id},
                                    {
                                        "$setOnInsert": {"group_id": group_id},
                                        "$addToSet": {f"dates.{current_date}.members": user_data}
                                    },
                                    upsert=True
                                ),
                                timeout=10.0
                            )

                        break

                    except asyncio.TimeoutError:
                        retry_count += 1
                        if retry_count == max_retries:
                            logging.error(f"Failed to complete database operation after {max_retries} retries")
                            raise
                        await asyncio.sleep(1)

                await asyncio.wait_for(
                    send_and_cleanup_message(
                        context,
                        group_id,
                        f"Добро пожаловать, {new_member.first_name}! Пожалуйста, отправьте свой апдейт. "
                        f"Включите #апдейт в любое место сообщения – всё, что после него, будет записано как апдейт.",
                        cleanup_delay=45
                    ),
                    timeout=10.0
                )

                schedule = {
                    "type": "interval",
                    "interval": REGISTRATION_INTERVAL
                }
                job_name = f"new_member_reminder_{group_id}_{new_member.id}"
                await asyncio.wait_for(
                    store_job(group_id, "new_member", schedule, job_name),
                    timeout=10.0
                )

                context.job_queue.run_repeating(
                    check_new_member_update,
                    interval=REGISTRATION_INTERVAL,
                    data={"group_id": group_id, "user_id": new_member.id},
                    name=job_name
                )

                logging.info(f"Added new member {new_member.first_name} ({new_member.username}) in group {group_id}")

    except Exception as e:
        logging.error(f"Error in new_member_handler: {e}")


async def register_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            logger.warning("Update does not contain a message. Skipping...")
            return

        message = update.message
        group_id = message.chat_id
        user = message.from_user

        if not user:
            logger.warning(f"No user found for message: {message.text}")
            return

        bot_id = context.bot.id

        if user.id == bot_id:
            logger.info("Message sent by the bot, skipping...")
            return

        if not user or not group_id:
            logger.warning("Message does not have a valid user or group_id.")
            return

        task_pattern = re.compile(r"#апдейт\b", re.IGNORECASE)

        if not task_pattern.search(message.text):
            logger.info(f"Irrelevant message from {user.username}: {message.text}")
            return

        await context.bot.set_message_reaction(
            chat_id=group_id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji("👍")]
        )

        task_description = re.sub(r"#апдейт\b", "", message.text, flags=re.IGNORECASE).strip()
        if not task_description:
            await context.bot.send_message(
                chat_id=group_id,
                text="Task description cannot be empty. Please send your update as: Task: [Your Task]"
            )
            return

        current_date = datetime.now().strftime('%Y-%m-%d')

        user_data = {
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "task": task_description,
            "timestamp": message.date
        }

        group_query = {"group_id": group_id}
        date_field = f"dates.{current_date}.members"

        result = await groups_collection.update_one(
            {
                **group_query,
                f"{date_field}.user_id": user.id
            },
            {
                "$set": {
                    f"{date_field}.$.task": task_description,
                    f"{date_field}.$.timestamp": message.date
                }
            }
        )

        if result.matched_count == 0:
            await groups_collection.update_one(
                group_query,
                {
                    "$setOnInsert": {"group_id": group_id},
                    "$addToSet": {date_field: user_data}
                },
                upsert=True
            )
            logger.info(f"Added new task for user {user.username} under {current_date} in group {group_id}.")
        else:
            logger.info(f"Updated task for user {user.username} under {current_date} in group {group_id}.")

        await groups_collection.update_one(
            {**group_query, f"{date_field}.user_id": user.id},
            {"$unset": {f"{date_field}.$.on_vacation": ""}}
        )

        logger.info(f"{user.username} from group {group_id}  will be triggered from now on.")

        job_name = f"new_member_reminder_{group_id}_{user.id}"
        current_jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in current_jobs:
            logging.info(f"Stopping reminder job {job_name} for user {user.username} in group {group_id}.")
            job.schedule_removal()

    except Exception as e:
        logger.error(f"Error in register_user_message: {e}")


async def on_vacation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.message.from_user
    group_id = update.message.chat_id

    if not user or not group_id:
        return

    current_date = datetime.now().strftime('%Y-%m-%d')
    group_query = {"group_id": group_id}
    date_field = f"dates.{current_date}.members"
    message = update.message

    user_data = {
        "user_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "task": "On Vacation",
        "on_vacation": True,
        "timestamp": datetime.now()
    }

    result = await groups_collection.update_one(
        {**group_query, f"{date_field}.user_id": user.id},
        {"$set": {f"{date_field}.$.on_vacation": True}}
    )

    if result.matched_count == 0:
        await groups_collection.update_one(
            group_query,
            {
                "$setOnInsert": {"group_id": group_id},
                "$addToSet": {date_field: user_data}
            },
            upsert=True
        )

        await context.bot.set_message_reaction(
            chat_id=group_id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji("👌")]
        )


def setup_handlers(application: Application):
    application.add_handler(ChatMemberHandler(added_to_group, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, register_user_message))
    application.add_handler(CommandHandler("on_vacation", on_vacation))
