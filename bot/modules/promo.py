from html import escape

from ..core.config_manager import Config
from ..helper.ext_utils.bot_utils import new_task
from ..helper.ext_utils.db_handler import database
from ..helper.telegram_helper.message_utils import send_message


@new_task
async def promo(_, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        status = "ON" if Config.PROMO_ENABLED else "OFF"
        text = escape(Config.PROMO_TEXT) if Config.PROMO_TEXT else "<i>(not set)</i>"
        await send_message(
            message,
            f"<b>Promotional message</b>\n\nStatus: <b>{status}</b>\nText:\n{text}\n\n"
            "<b>Usage:</b>\n"
            "<code>/promo &lt;text&gt;</code> - set the text (turns it on)\n"
            "<code>/promo on</code> / <code>/promo off</code> - toggle without changing the text\n\n"
            "It gets appended to every leeched file's caption and, where supported "
            "(e.g. Google Drive), to the upload description. Any @username inside "
            "this text is kept in leeched captions - every other @username in the "
            "original file's caption is stripped.",
        )
        return

    value = args[1].strip()
    if value.lower() == "on":
        Config.PROMO_ENABLED = True
        await database.update_config({"PROMO_ENABLED": True})
        await send_message(message, "<b>Promotional message enabled.</b>")
    elif value.lower() == "off":
        Config.PROMO_ENABLED = False
        await database.update_config({"PROMO_ENABLED": False})
        await send_message(message, "<b>Promotional message disabled.</b>")
    else:
        Config.PROMO_TEXT = value
        Config.PROMO_ENABLED = True
        await database.update_config({"PROMO_TEXT": value, "PROMO_ENABLED": True})
        await send_message(
            message, f"<b>Promotional message updated and enabled:</b>\n\n{value}"
        )
