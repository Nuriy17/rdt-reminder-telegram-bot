from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGODB_URI, TIMEZONE
import pytz
from datetime import datetime, time
import logging


client = AsyncIOMotorClient(
    MONGODB_URI,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=5000
)
db = client["reminder_bot"]
groups_collection = db["groups"]
jobs_collection = db["jobs"]


async def store_job(group_id: int, job_type: str, schedule: dict, job_name: str):
    if 'time' in schedule:
        target_time = datetime.strptime(str(schedule['time']), '%H:%M:%S').time()

        target_time_utc = TIMEZONE.localize(datetime.combine(datetime.today(), target_time)).astimezone(pytz.UTC)
        schedule['time'] = target_time_utc.strftime('%H:%M:%S')

    job_data = {
        "group_id": group_id,
        "job_type": job_type,
        "schedule": schedule,
        "active": True,
        "last_run": datetime.now(pytz.UTC),
        "next_run": target_time_utc if 'time' in schedule else None,
        "name": job_name
    }
    await jobs_collection.insert_one(job_data)


async def create_daily_reminder(group_id: int, target_time: time):
    schedule = {
        "type": "daily",
        "time": target_time,
        "timezone": str(TIMEZONE)
    }
    job_name = f"daily_reminders_{group_id}"
    await store_job(group_id, "daily", schedule, job_name)
    return job_name


async def create_registration_reminder(group_id: int, interval: int):
    schedule = {
        "type": "interval",
        "interval": interval
    }
    job_name = f"registration_reminders_{group_id}"
    await store_job(group_id, "registration", schedule, job_name)
    return job_name


async def get_registered_count(group_id: int, bot_id: int):
    current_date = datetime.now().strftime('%Y-%m-%d')
    group_data = await groups_collection.find_one({"group_id": group_id})

    if not group_data or "dates" not in group_data or current_date not in group_data["dates"]:
        return 0

    members = group_data["dates"][current_date]["members"]
    unique_members = {
        member["user_id"] for member in members
        if member["user_id"] != bot_id and "task" in member
    }
    return len(unique_members)


async def rollover_on_vacation_members(group_data: dict, group_id: int, current_date: str):
    if "dates" in group_data and current_date not in group_data["dates"]:
        previous_dates = list(group_data["dates"].keys())
        if not previous_dates:
            return

        latest_date = max(previous_dates)
        previous_members = group_data["dates"][latest_date].get("members", [])

        on_vacation_members = []
        for member in previous_members:
            if member.get("on_vacation"):
                new_member = member.copy()
                new_member["timestamp"] = datetime.now(TIMEZONE)
                new_member["task"] = "On Vacation"
                on_vacation_members.append(new_member)

        await groups_collection.update_one(
            {"group_id": group_id},
            {"$set": {f"dates.{current_date}.members": on_vacation_members}},
            upsert=True
        )
        logging.info(f"Rolled over on-vacation members to {current_date} for group {group_id}")
