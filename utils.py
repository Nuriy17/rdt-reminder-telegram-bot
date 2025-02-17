from telegram.ext import ContextTypes
import asyncio
import logging


async def send_and_cleanup_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str,
                                   cleanup_delay: int = 60):
    message = await context.bot.send_message(
        chat_id=chat_id,
        text=text
    )

    context.job_queue.run_once(
        delete_message,
        cleanup_delay,
        data={'chat_id': chat_id, 'message_id': message.message_id},
        job_kwargs={"misfire_grace_time": 60, "max_instances": 5}
    )


async def delete_message(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    try:
        await asyncio.wait_for(
            context.bot.delete_message(
                chat_id=job_data['chat_id'],
                message_id=job_data['message_id']
            ),
            timeout=5.0
        )
    except asyncio.TimeoutError:
        logging.error(f"Timeout while deleting message {job_data['message_id']} in chat {job_data['chat_id']}")
    except Exception as e:
        logging.error(f"Failed to delete message: {e}")
