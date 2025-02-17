from telegram.ext import ContextTypes, Application
from data_base import (
    groups_collection,
    jobs_collection,
    rollover_on_vacation_members,
    get_registered_count,
    create_daily_reminder
)
from utils import send_and_cleanup_message
from config import TIMEZONE, DAILY_REMINDER_TIME
import logging
import pytz
from datetime import datetime, timedelta
import telegram
import asyncio
logger = logging.getLogger(__name__)


async def transition_to_daily_reminders(context, group_id):
    daily_job_name = f"daily_reminders_{group_id}"
    if not context.job_queue.get_jobs_by_name(daily_job_name):
        target_time = TIMEZONE.localize(datetime.combine(datetime.today(), DAILY_REMINDER_TIME)).time()
        context.job_queue.run_daily(
            send_daily_reminders,
            time=target_time,
            data={"group_id": group_id},
            name=daily_job_name
        )
        logger.info(f"Set up daily reminder for group {group_id} at {target_time}")
    context.job.schedule_removal()


async def send_daily_reminders(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TIMEZONE).weekday()

    if today >= 5:  # 5 = Saturday, 6 = Sunday
        print(today)
        logging.info("Skipping daily reminders as today is a weekend.")
        return

    logging.info("Daily reminder job triggered!")
    group_id = None
    try:
        job_data = context.job.data
        group_id = job_data["group_id"]
        logging.info(f"Sending daily reminders for group {group_id}")

        try:
            bot_id = context.bot.id
        except Exception as e:
            await context.bot.initialize()
            bot_id = context.bot.id

        current_date = datetime.now().strftime('%Y-%m-%d')

        group_data = await groups_collection.find_one({"group_id": group_id})

        dates = group_data.get("dates", {})
        if not dates:
            logging.info(f"No dates records found for group {group_id}. Skipping daily reminders.")
            return

        if current_date not in dates:
            logging.info(f"No record for today ({current_date}) found for group {group_id}.")

        await rollover_on_vacation_members(group_data, group_id, current_date)

        group_data = await groups_collection.find_one({"group_id": group_id})

        if group_data and "dates" in group_data:
            previous_dates = [date for date in group_data["dates"].keys() if date != current_date]

            if previous_dates:
                latest_date = max(previous_dates)
                all_known_members = {
                    member["user_id"]: member
                    for member in group_data["dates"][latest_date]["members"]
                    if member["user_id"] != bot_id
                }

                today_submissions = {
                    member["user_id"]
                    for member in group_data.get("dates", {}).get(current_date, {}).get("members", [])
                    if "task" in member
                }

                missing_members = [
                    member for uid, member in all_known_members.items()
                    if uid not in today_submissions
                ]

                missing_members = [member for member in missing_members if member.get("on_vacation") != True]

                if missing_members:
                    mentions = [f"@{m['username']}" if m.get('username') else m['first_name'] for m in missing_members]
                    await send_and_cleanup_message(
                        context,
                        group_id,
                        f"Ежедневное напоминание: {', '.join(mentions)}, пожалуйста, отправьте свои апдейты. Включите #апдейт в любое место сообщения – всё, что после него, будет записано как апдейт.'",
                        cleanup_delay=3600
                    )
                    logger.info(f"Sent daily reminder to {len(missing_members)} members in group {group_id}")

                    follow_up_job_name = f"follow_up_reminders_{group_id}"
                    context.job_queue.run_repeating(
                        send_follow_up_reminders,
                        interval=3600,  # 5 minutes
                        data={"group_id": group_id},
                        name=follow_up_job_name
                    )
                    logger.info(f"Scheduled follow-up reminders for group {group_id}")

                else:
                    logger.info(f"All members submitted tasks in group {group_id}")
                    await jobs_collection.update_one(
                        {"group_id": group_id, "job_type": "daily"},
                        {"$set": {"last_run": datetime.now(pytz.UTC)}}
                    )
                    logger.info(f"Updated last_run time for daily job in group {group_id}")
                    await rollover_on_vacation_members(group_data, group_id, current_date)

        else:
            logging.warning(f"No data found for group {group_id} on {current_date}.")

    except Exception as e:
        logger.error(f"Error in send_daily_reminders for group {group_id}: {e}")


async def send_registration_reminders(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    group_id = job_data["group_id"]
    total_members = job_data["total_members"]
    bot_id = context.bot.id
    current_date = datetime.now().strftime('%Y-%m-%d')

    try:
        group_data = await groups_collection.find_one(
            {"group_id": group_id},
            {"dates": 1, "members": 1}
        )
    except Exception as e:
        logger.error(f"Failed to fetch group data for {group_id}: {e}")
        return

    previous_dates = [date for date in group_data.get("dates", {}).keys() if date != current_date] if group_data else []

    if previous_dates:
        latest_date = max(previous_dates)
        all_known_members = {
            member["user_id"]: member
            for member in group_data["dates"][latest_date]["members"]
            if member["user_id"] != bot_id
        }

        today_submissions = {
            member["user_id"]
            for member in group_data.get("dates", {}).get(current_date, {}).get("members", [])
            if "task" in member
        }

        missing_members = [
            member for uid, member in all_known_members.items()
            if uid not in today_submissions
        ]

        if missing_members:
            mentions = [f"@{m['username']}" if m.get('username') else m['first_name'] for m in missing_members]
            logger.info(f"Sending reminders to {len(missing_members)} missing members: {', '.join(mentions)}")
            await send_and_cleanup_message(
                context,
                group_id,
                f"Напоминание: {', '.join(mentions)}, пожалуйста, отправьте свои апдейты. Включите #апдейт в любое место сообщения – всё, что после него, будет записано как апдейт.",
                cleanup_delay=60
            )

        else:
            await send_and_cleanup_message(
                context,
                group_id,
                "Все участники отправили свои апдейты за сегодня!",
                cleanup_delay=30
            )
            daily_job_name = f"daily_reminders_{group_id}"
            if not context.job_queue.get_jobs_by_name(daily_job_name):
                target_time = TIMEZONE.localize(datetime.combine(datetime.today(), DAILY_REMINDER_TIME)).time()
                context.job_queue.run_daily(
                    send_daily_reminders,
                    time=target_time,
                    data={"group_id": group_id},
                    name=daily_job_name
                )
                logger.info(f"Set up daily reminder for group {group_id} at {target_time}")

            context.job.schedule_removal()

    else:
        registered_count = await get_registered_count(group_id, bot_id)
        unregistered_count = total_members - registered_count

        if unregistered_count > 0:

            await send_and_cleanup_message(
                context,
                group_id,
                f"Напоминание: {unregistered_count} участник(ов) ещё не отправил(и) свой апдейт. Пожалуйста, отправьте свои апдейты.Включите #апдейт в любое место сообщения – всё, что после него, будет записано как апдейт.",
                cleanup_delay=60
            )
        else:
            max_retries = 3
            retry_count = 0
            base_delay = 1

            while retry_count < max_retries:
                try:
                    target_time = TIMEZONE.localize(datetime.combine(datetime.today(), DAILY_REMINDER_TIME)).time()
                    job_name = await asyncio.wait_for(
                        create_daily_reminder(group_id, target_time),
                        timeout=10.0
                    )

                    context.job_queue.run_daily(
                        send_daily_reminders,
                        time=target_time,
                        data={"group_id": group_id},
                        name=job_name
                    )

                    logger.info(f"Transitioned group {group_id} to daily reminder schedule at {target_time}")
                    break

                except asyncio.TimeoutError as e:
                    retry_count += 1
                    if retry_count == max_retries:
                        logger.error(f"Failed to store daily reminder job after {max_retries} retries. Error: {str(e)}")
                        raise
                    await asyncio.sleep(base_delay * (2 ** retry_count))  # Exponential backoff

            await send_and_cleanup_message(
                context,
                group_id,
                "Все участники зарегистрированы! Регистрация завершена.",
                cleanup_delay=30
            )
            await jobs_collection.update_one(
                {"group_id": group_id, "job_type": "daily"},
                {"$set": {"last_run": datetime.now(pytz.UTC)}}
            )
            logger.info(f"Updated last_run time for daily job in group {group_id}")
            await jobs_collection.update_one(
                {"group_id": group_id, "job_type": "registration"},
                {"$set": {"active": False}}
            )
            logger.info(f"Updated last_run time for daily job in group {group_id}")

            await transition_to_daily_reminders(context, group_id)


async def check_new_member_update(context: ContextTypes.DEFAULT_TYPE):
    try:

        job_data = context.job.data
        if "group_id" not in job_data or "user_id" not in job_data:
            logging.error("Missing group_id or user_id in job data")
            return

        if not context.job or not context.job.data:
            logging.error("Job data missing")
            return

        group_id = job_data["group_id"]
        user_id = job_data["user_id"]

        if not group_id or not user_id:
            logging.error(f"Invalid job data: group_id or user_id is missing. group_id: {group_id}, user_id: {user_id}")
            return
        current_date = datetime.now().strftime('%Y-%m-%d')

        member_data = await groups_collection.find_one({
            "group_id": group_id,
            f"dates.{current_date}.members": {
                "$elemMatch": {
                    "user_id": user_id,
                    "task": {"$exists": True}
                }
            }
        })

        if not member_data:
            user_info = await groups_collection.find_one(
                {
                    "group_id": group_id,
                    f"dates.{current_date}.members.user_id": user_id
                },
                {f"dates.{current_date}.members.$": 1}
            )

            if user_info and "dates" in user_info and current_date in user_info["dates"]:

                if "dates" not in user_info or current_date not in user_info["dates"]:
                    logging.error(f"No task data for user {user_id} on {current_date}.")
                    return

                member = user_info["dates"][current_date]["members"][0]
                mention = f"@{member['username']}" if member.get('username') else member['first_name']

                await send_and_cleanup_message(
                    context,
                    group_id,
                    f"Напоминание: {mention}, пожалуйста, отправьте свои апдейты. "
                    f"Включите #апдейт в любое место сообщения – всё, что после него, будет записано как апдейт.",
                    cleanup_delay=60
                )
        else:
            logging.info(f"Stopping reminder job for user {user_id} in group {group_id} as task has been submitted.")
            context.job.schedule_removal()

    except Exception as e:
        logging.error(f"Error in check_new_member_update: {e}")


async def send_follow_up_reminders(context: ContextTypes.DEFAULT_TYPE):
    logging.info("Follow-up reminder job triggered!")
    try:
        job_data = context.job.data
        group_id = job_data["group_id"]
        logging.info(f"Sending follow-up reminders for group {group_id}")
        bot_id = context.bot.id
        current_date = datetime.now().strftime('%Y-%m-%d')

        group_data = await groups_collection.find_one({"group_id": group_id})

        if group_data and "dates" in group_data:
            previous_dates = [date for date in group_data["dates"].keys() if date != current_date]

            if previous_dates:
                latest_date = max(previous_dates)
                all_known_members = {
                    member["user_id"]: member
                    for member in group_data["dates"][latest_date]["members"]
                    if member["user_id"] != bot_id
                }

                today_submissions = {
                    member["user_id"]
                    for member in group_data.get("dates", {}).get(current_date, {}).get("members", [])
                    if "task" in member
                }

                missing_members = [
                    member for uid, member in all_known_members.items()
                    if uid not in today_submissions
                ]

                missing_members = [member for member in missing_members if member.get("on_vacation") != True]

                if missing_members:
                    mentions = [f"@{m['username']}" if m.get('username') else m['first_name'] for m in missing_members]
                    await send_and_cleanup_message(
                        context,
                        group_id,
                        f"Дополнительное напоминание: {', '.join(mentions)}, пожалуйста, отправьте свои апдейты. Включите #апдейт в любое место сообщения – всё, что после него, будет записано как апдейт.",
                        cleanup_delay=3601
                    )
                    logger.info(f"Sent follow-up reminder to {len(missing_members)} members in group {group_id}")
                else:
                    context.job.schedule_removal()
                    logger.info(f"Stopped follow-up reminders for group {group_id}")
                    logger.info(f"All members submitted tasks in group {group_id}")
                    await jobs_collection.update_one(
                        {"group_id": group_id, "job_type": "daily"},
                        {"$set": {"last_run": datetime.now(pytz.UTC)}}
                    )
                    logger.info(f"Updated last_run time for daily job in group {group_id}")

    except Exception as e:
        logger.error(f"Error in send_follow_up_reminders for group {group_id}: {e}")


async def restore_jobs(application: Application):
    try:
        jobs = await jobs_collection.find({"active": True}).to_list(None)
        logging.info(f"Restoring {len(jobs)} active jobs.")

        groups_with_daily_jobs = set()

        for job_data in jobs:
            group_id = job_data.get("group_id")
            if not group_id:
                logging.error(f"Job data is missing 'group_id': {job_data}")
                continue

            try:
                await application.bot.get_chat(group_id)
            except telegram.error.Forbidden:
                logging.warning(f"Bot is not in group {group_id}. Marking job as inactive.")
                await jobs_collection.update_one({"group_id": group_id}, {"$set": {"active": False}})
                continue
            except Exception as e:
                logging.error(f"Error fetching chat {group_id}: {e}")
                continue

            job_type = job_data.get("job_type")
            job_name = job_data.get("name", f"job_{group_id}")

            if job_type == "daily":
                if group_id in groups_with_daily_jobs:
                    continue
                groups_with_daily_jobs.add(group_id)

                target_time = datetime.strptime(job_data["schedule"]["time"], '%H:%M:%S').time()
                target_datetime = TIMEZONE.localize(datetime.combine(datetime.today(), target_time))
                now = datetime.now(TIMEZONE)
                today_date = now.date()

                target_naive = datetime.combine(today_date, target_time)
                # logging.info(f"Last Run (UTC): {last_run_utc}, Last Run (Local): {last_run_local}")
                logging.info(f"target_naive combined: {target_naive}")
                target_naive_utc = target_naive.replace(tzinfo=pytz.UTC)
                last_run = job_data.get("last_run")
                target_datetime_today = target_naive_utc.astimezone(TIMEZONE)

                logging.info(f"Now: {now}, Target Today: {target_datetime_today}")
                if last_run:
                    last_run_utc = last_run.replace(tzinfo=pytz.UTC)
                    last_run_local = last_run_utc.astimezone(TIMEZONE)
                    logging.info(f"Last Run (UTC): {last_run_utc}, Last Run (Local): {last_run_local}")

                    if last_run_local.date() < today_date and now >= target_datetime_today:
                        logging.info(f"Missed daily job for group {group_id}. Triggering now.")
                        application.job_queue.run_once(
                            send_daily_reminders,
                            when=timedelta(seconds=2),
                            data={"group_id": group_id},
                            name=f"missed_{job_name}"
                        )
                else:
                    if now >= target_datetime_today:
                        logging.info(f"No last_run and scheduled time passed. Triggering job for group {group_id}.")
                        application.job_queue.run_once(
                            send_daily_reminders,
                            when=timedelta(seconds=2),
                            data={"group_id": group_id},
                            name=f"initial_{job_name}"
                        )

                application.job_queue.run_daily(
                    send_daily_reminders,
                    time=target_datetime.time(),
                    data={"group_id": group_id},
                    name=job_name
                )
                logging.info(f"Restored daily job: {job_name} at {target_datetime_today.time()}")

            elif job_type == "registration":
                chat = await application.bot.get_chat(group_id)
                total_members = await chat.get_member_count() - 1

                application.job_queue.run_repeating(
                    send_registration_reminders,
                    interval=job_data["schedule"]["interval"],
                    data={"group_id": group_id, "total_members": total_members},
                    name=job_name
                )
                logging.info(f"Restored registration job: {job_name}")

            elif job_type == "new_member":
                application.job_queue.run_repeating(
                    check_new_member_update,
                    interval=job_data["schedule"]["interval"],
                    data={"group_id": group_id},
                    name=job_name
                )
                logging.info(f"Restored new member job: {job_name}")

    except Exception as e:
        logging.error(f"Error in restore_jobs: {e}")