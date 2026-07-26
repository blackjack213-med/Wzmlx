from asyncio import sleep
from re import match as re_match

from pyrogram.errors import FloodWait
from pyrogram.raw import functions
from pyrogram.raw.types import MessageService

from ... import LOGGER
from ...core.tg_client import TgClient
from ..telegram_helper.bot_commands import BotCommands

_GENERAL_TOPIC_ID = 1
# Tuned for speed while staying under Telegram's per-chat rate limit
# (roughly 1 message/sec to the same chat before it starts flood-waiting).
# Group/topic creation keeps a bit more headroom since those are rarer,
# heavier actions than a routine message send.
_CREATE_PAUSE = 1.2
_SUBMIT_PAUSE = 0.7
_PAGE_PAUSE = 0.4


async def _retry_flood(func, *args, **kwargs):
    while True:
        try:
            return await func(*args, **kwargs)
        except FloodWait as e:
            LOGGER.warning(f"topicclone: FloodWait {e.value}s on {func.__name__}")
            await sleep(e.value + 2)


async def resolve_source(link):
    """Parse any t.me message link and resolve the chat it points to.

    Returns (chat, link_prefix) where link_prefix is the t.me/... base
    (without a trailing message id) used to build per-message leech links.
    """
    m = re_match(
        r"https?://(?:t\.me|telegram\.me|telegram\.dog|telegram\.space)/"
        r"(?:c/)?([^/]+)(?:/[0-9]+)?/([0-9]+)",
        link.strip(),
    )
    if not m:
        raise ValueError(
            "That doesn't look like a Telegram message link. Long-press any "
            "message inside the source chat, tap Copy Link, and pass that."
        )
    chat_part = m.group(1)
    if chat_part.isdigit():
        link_prefix = f"https://t.me/c/{chat_part}"
        resolve_target = int(f"-100{chat_part}")
    else:
        link_prefix = f"https://t.me/{chat_part}"
        resolve_target = chat_part
    chat = await TgClient.user.get_chat(resolve_target)
    return chat, link_prefix


async def discover_topics(chat_id):
    """List every forum topic in chat_id as (topic_id, title). Empty if not a forum.

    A topic's title can come back empty/None (seen in practice on at least one
    real chat) - fall back to a synthetic name rather than letting that reach
    create_forum_topic(), which requires a string.
    """
    topics = []
    async for topic in TgClient.user.get_forum_topics(chat_id):
        title = topic.title or f"Topic {topic.id}"
        topics.append((topic.id, title))
    return topics


async def enumerate_topic_messages(chat_id, topic_id):
    """All message ids belonging to one forum topic.

    Topic message ids are NOT contiguous in the chat's global id space (other
    topics' messages interleave), so this walks Telegram's own reply-thread
    listing for the topic rather than a numeric range.
    """
    peer = await TgClient.user.resolve_peer(chat_id)
    ids = []
    offset_id = 0
    while True:
        result = await _retry_flood(
            TgClient.user.invoke,
            functions.messages.GetReplies(
                peer=peer,
                msg_id=topic_id,
                offset_id=offset_id,
                offset_date=0,
                add_offset=0,
                limit=100,
                max_id=0,
                min_id=0,
                hash=0,
            ),
        )
        msgs = result.messages
        if not msgs:
            break
        # GetReplies returns raw MessageService entries too (member joined/
        # left, topic renamed, etc.), and plain text-only posts (chat banter,
        # invite links people pasted, etc.) - nothing to leech in either, and
        # leeching a text-only message makes the bot try to "download" its
        # text as if it were a link. Only keep messages with real media.
        ids.extend(
            m.id
            for m in msgs
            if not isinstance(m, MessageService) and getattr(m, "media", None)
        )
        if len(msgs) < 100:
            break
        offset_id = msgs[-1].id
        await sleep(_PAGE_PAUSE)
    return ids


async def enumerate_all_messages(chat_id):
    """All real message ids in a chat with no topics (plain group or channel)."""
    ids = []
    async for msg in TgClient.user.get_chat_history(chat_id):
        if msg.empty or msg.service or not msg.media:
            continue
        ids.append(msg.id)
        if len(ids) % 500 == 0:
            await sleep(_PAGE_PAUSE)
    return ids


async def create_group(source_title):
    """Create '<source_title> Dr Scopium' as a new, empty supergroup.
    Split out from topic/bot setup so the caller can persist dest_chat_id
    right away - a crash in the setup step then resumes against this same
    group instead of creating a duplicate orphan on retry."""
    dest = await _retry_flood(
        TgClient.user.create_supergroup,
        f"{source_title} Dr Scopium",
        "Auto-cloned via /topicclone",
    )
    await sleep(_CREATE_PAUSE)
    return dest.id


async def _toggle_forum(chat_id, enabled):
    # Client.toggle_forum() is broken in this pyrogram build - it invokes
    # raw.functions.channels.toggleForum, which doesn't exist (the real raw
    # class is ToggleForum). Call the raw function directly instead.
    peer = await TgClient.user.resolve_peer(chat_id)
    await TgClient.user.invoke(
        functions.channels.ToggleForum(channel=peer, enabled=enabled, tabs=False)
    )


async def setup_topics(dest_chat_id, topics):
    """Enable forum mode (if needed) and recreate every topic in dest_chat_id,
    then add the bot so it can post. Returns topic_map: source_topic_id ->
    destination_topic_id. Safe to re-run against a partially set-up group -
    topics already created are simply recreated again is NOT handled here,
    callers should only call this once per group."""
    topic_map = {}
    if topics:
        await _retry_flood(_toggle_forum, dest_chat_id, True)
        await sleep(_CREATE_PAUSE)
        for topic_id, title in topics:
            if topic_id == _GENERAL_TOPIC_ID:
                topic_map[topic_id] = _GENERAL_TOPIC_ID
                continue
            created = await _retry_flood(
                TgClient.user.create_forum_topic, dest_chat_id, title
            )
            topic_map[topic_id] = created.id
            await sleep(_CREATE_PAUSE)

    if TgClient.BNAME:
        try:
            await _retry_flood(
                TgClient.user.add_chat_members, dest_chat_id, TgClient.BNAME
            )
        except Exception as e:
            LOGGER.warning(f"topicclone: couldn't add bot to new group: {e}")

    return topic_map


async def submit_leech(link_prefix, msg_id, dest_chat_id, dest_thread_id):
    """Trigger a real /leech of one message, landing in the destination
    topic, by posting the command directly inside that topic thread (as the
    owner) rather than via a '-up' flag.

    The '-up chat|thread' flag looks like it should redirect a leech's
    output, but in this bot's leech path self.up_dest for leech tasks is
    unconditionally overwritten by Config.LEECH_DUMP_CHAT (common.py), and
    the per-command override only survives as a secondary "also forward a
    copy" destination (itself thread-unaware) - it never controls where the
    primary upload lands. Posting the command inside the target topic works
    because the upload replies to the triggering command message itself,
    which Telegram keeps anchored in that same topic.
    """
    leech_link = f"{link_prefix}/{msg_id}"
    cmd = BotCommands.LeechCommand[0]
    kwargs = {}
    if dest_thread_id is not None and dest_thread_id != _GENERAL_TOPIC_ID:
        kwargs["message_thread_id"] = dest_thread_id
    await _retry_flood(
        TgClient.user.send_message,
        dest_chat_id,
        f"/{cmd} {leech_link}",
        **kwargs,
    )
    await sleep(_SUBMIT_PAUSE)
