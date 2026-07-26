from .. import user_data
from ..core.config_manager import Config
from ..core.tg_client import TgClient
from ..helper.ext_utils.bot_utils import new_task, update_user_ldata
from ..helper.ext_utils.db_handler import database
from ..helper.telegram_helper.message_utils import send_message, edit_message


@new_task
async def user_session(_, message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if len(args) < 2:
        current = user_data.get(user_id, {}).get("USER_TG_SESSION")
        status = "set" if current else "not set"
        await send_message(
            message,
            "<b>Personal Telegram Session</b>\n\n"
            f"Status: <b>{status}</b>\n\n"
            "This lets the bot use <i>your own</i> Telegram account to fetch "
            "messages from private chats/channels only you are a member of, "
            "when you leech/mirror a t.me link from them.\n\n"
            "<b>Usage:</b>\n"
            "<code>/usersession &lt;pyrogram_session_string&gt;</code>\n"
            "<code>/usersession remove</code> - to clear it",
        )
        return

    value = args[1].strip()

    if value.lower() == "remove":
        update_user_ldata(user_id, "USER_TG_SESSION", "")
        if Config.DATABASE_URL:
            await database.update_user_data(user_id)
        await TgClient.remove_personal_client(user_id)
        await send_message(message, "<b>Personal session removed.</b>")
        return

    status_msg = await send_message(message, "<i>Validating session...</i>")

    # Drop any previously cached client so a replacement session is
    # actually tried, instead of silently reusing the old one.
    await TgClient.remove_personal_client(user_id)

    try:
        client = await TgClient.get_personal_client(user_id, value)
    except Exception as e:
        await edit_message(status_msg, f"<b>Invalid session:</b> {e}")
        return
    if client is None:
        await edit_message(status_msg, "<b>Invalid session string.</b>")
        return

    uname = client.me.username or client.me.first_name
    update_user_ldata(user_id, "USER_TG_SESSION", value)
    if Config.DATABASE_URL:
        await database.update_user_data(user_id)
    await edit_message(
        status_msg,
        f"<b>Personal session registered!</b>\nLogged in as: {uname}\n\n"
        "It will now be used automatically for t.me links only you have access to.",
    )
