from time import time

from .. import LOGGER
from ..core.tg_client import TgClient
from ..helper.ext_utils.bot_utils import new_task
from ..helper.ext_utils.db_handler import database
from ..helper.ext_utils.topic_clone_utils import (
    create_group,
    discover_topics,
    enumerate_all_messages,
    enumerate_topic_messages,
    resolve_source,
    setup_topics,
    submit_leech,
)
from ..helper.telegram_helper.message_utils import edit_message, send_message

_EDIT_INTERVAL = 15


@new_task
async def topic_clone(_, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_message(
            message,
            "<b>Clone an entire group/channel, topic by topic.</b>\n\n"
            "Downloads and re-uploads every message (works even when "
            "forwarding/saving is restricted in the source), auto-creates a "
            'new group named "&lt;source title&gt; Dr Scopium", and recreates '
            "every topic to match.\n\n"
            "<b>Usage:</b>\n"
            "<code>/topicclone &lt;any message link from inside the source chat&gt;</code>\n\n"
            "Re-running the same link resumes an interrupted run instead of "
            "starting over.",
        )
        return

    if not TgClient.user:
        await send_message(
            message, "<b>USER_SESSION_STRING is required for this command.</b>"
        )
        return

    link = args[1].strip()

    try:
        chat, link_prefix = await resolve_source(link)
    except ValueError as e:
        await send_message(message, f"<b>Error:</b> {e}")
        return
    except Exception as e:
        await send_message(message, f"<b>Couldn't access that chat:</b> {e}")
        return

    source_chat_id = chat.id
    source_title = chat.title or f"Chat {source_chat_id}"

    status_msg = await send_message(message, f"<b>{source_title}</b>\nStarting...")

    job = await database.get_topic_clone_job(source_chat_id)

    if job and job.get("topics_created"):
        dest_chat_id = job["dest_chat_id"]
        topic_map = {int(k): v for k, v in job.get("topic_map", {}).items()}
        submitted = {k: set(v) for k, v in job.get("submitted", {}).items()}
        await edit_message(
            status_msg,
            f"<b>{source_title}</b>\nResuming existing clone -> <code>{dest_chat_id}</code>",
        )
    else:
        await edit_message(status_msg, f"<b>{source_title}</b>\nDiscovering topics...")
        try:
            topics = await discover_topics(source_chat_id) if chat.is_forum else []
        except Exception as e:
            await edit_message(status_msg, f"<b>Failed to list topics:</b> {e}")
            return

        if job and job.get("dest_chat_id"):
            # A previous attempt already created the group but crashed before
            # finishing topic setup - reuse it instead of making a duplicate.
            dest_chat_id = job["dest_chat_id"]
            await edit_message(
                status_msg,
                f"<b>{source_title}</b>\n"
                f"Reusing existing destination group <code>{dest_chat_id}</code>\n"
                "Setting up topics...",
            )
        else:
            await edit_message(
                status_msg,
                f"<b>{source_title}</b>\n"
                + (
                    f"Forum with {len(topics)} topics found.\n"
                    if topics
                    else "No topics - plain leech.\n"
                )
                + "Creating destination group...",
            )
            try:
                dest_chat_id = await create_group(source_title)
            except Exception as e:
                await edit_message(
                    status_msg, f"<b>Failed to create destination group:</b> {e}"
                )
                return
            # Persisted immediately - if topic setup below crashes, a retry
            # resumes against this group instead of creating another one.
            await database.save_topic_clone_job(
                source_chat_id,
                {
                    "source_title": source_title,
                    "dest_chat_id": dest_chat_id,
                    "topics_created": False,
                },
            )
            await edit_message(
                status_msg,
                f"<b>{source_title}</b>\nDestination created: <code>{dest_chat_id}</code>\n"
                "Setting up topics...",
            )

        try:
            topic_map = await setup_topics(dest_chat_id, topics)
        except Exception as e:
            await edit_message(status_msg, f"<b>Failed to set up topics:</b> {e}")
            return

        submitted = {}
        await database.save_topic_clone_job(
            source_chat_id,
            {
                "source_title": source_title,
                "dest_chat_id": dest_chat_id,
                "topic_map": {str(k): v for k, v in topic_map.items()},
                "topics_created": True,
                "submitted": {},
            },
        )
        await edit_message(
            status_msg,
            f"<b>{source_title}</b>\nDestination ready: <code>{dest_chat_id}</code>\n"
            f"{len(topic_map)} topics created.\nDiscovering messages...",
        )

    buckets = (
        [(str(src), src, dst) for src, dst in topic_map.items()]
        if topic_map
        else [("all", None, None)]
    )

    total_submitted = sum(len(v) for v in submitted.values())
    last_edit = time()

    for i, (topic_key, src_topic_id, dst_topic_id) in enumerate(buckets, 1):
        done_ids = submitted.get(topic_key, set())
        try:
            msg_ids = (
                await enumerate_topic_messages(source_chat_id, src_topic_id)
                if src_topic_id is not None
                else await enumerate_all_messages(source_chat_id)
            )
        except Exception as e:
            LOGGER.error(f"topicclone: failed enumerating topic {topic_key}: {e}")
            continue

        pending = sorted(mid for mid in msg_ids if mid not in done_ids)
        for j, mid in enumerate(pending, 1):
            try:
                await submit_leech(link_prefix, mid, dest_chat_id, dst_topic_id)
            except Exception as e:
                LOGGER.error(f"topicclone: failed to submit message {mid}: {e}")
                continue
            done_ids.add(mid)
            submitted[topic_key] = done_ids
            await database.mark_topic_clone_submitted(source_chat_id, topic_key, mid)
            total_submitted += 1

            if time() - last_edit > _EDIT_INTERVAL:
                last_edit = time()
                try:
                    await edit_message(
                        status_msg,
                        f"<b>{source_title}</b>\n"
                        f"Topic {i}/{len(buckets)}: submitted {j}/{len(pending)}\n"
                        f"Total submitted this run: {total_submitted}",
                    )
                except Exception:
                    pass

    await edit_message(
        status_msg,
        f"<b>{source_title}</b> - all done.\n"
        f"{total_submitted} messages submitted to the leech queue -> "
        f"<code>{dest_chat_id}</code>.",
    )
