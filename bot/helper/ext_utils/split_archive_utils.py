from asyncio import sleep
from re import IGNORECASE, compile as re_compile, escape as re_escape, search as re_search

_SEARCH_WINDOW = 150
_BATCH_SIZE = 100
_BATCH_PAUSE = 0.3


def sibling_pattern(filename):
    """Build a regex matching sibling volumes of a split archive, given the
    filename of what is already known to be part 1 (per is_first_archive_split
    in files_utils.py - this mirrors its three branches)."""
    low = filename.strip().lower()

    m = re_search(r"^(.*)\.part0*1\.rar$", low)
    if m:
        return re_compile(rf"^{re_escape(m.group(1))}\.part(\d+)\.rar$", IGNORECASE)

    m = re_search(r"^(.*)\.7z\.0*1$", low)
    if m:
        return re_compile(rf"^{re_escape(m.group(1))}\.7z\.(\d+)$", IGNORECASE)

    m = re_search(r"^(.*)\.zip\.0*1$", low)
    if m:
        return re_compile(rf"^{re_escape(m.group(1))}\.zip\.(\d+)$", IGNORECASE)

    if low.endswith(".rar") and not re_search(r"\.part\d+\.rar$", low):
        base = low[: -len(".rar")]
        return re_compile(rf"^{re_escape(base)}\.r(\d+)$", IGNORECASE)

    return None


async def find_split_siblings(client, chat_id, first_msg_id, filename):
    """Scan a bounded window of messages right after first_msg_id in the same
    chat for other volumes of the same split archive. Sibling parts are
    posted as consecutive/near-consecutive messages in practice, so a full
    chat/topic history walk isn't needed. Returns sorted message ids (not
    including first_msg_id itself)."""
    pattern = sibling_pattern(filename)
    if pattern is None:
        return []

    candidate_ids = list(range(first_msg_id + 1, first_msg_id + 1 + _SEARCH_WINDOW))
    found = []
    for i in range(0, len(candidate_ids), _BATCH_SIZE):
        batch_ids = candidate_ids[i : i + _BATCH_SIZE]
        try:
            msgs = await client.get_messages(chat_id, batch_ids)
        except Exception:
            continue
        if not isinstance(msgs, list):
            msgs = [msgs]
        for msg in msgs:
            if not msg or msg.empty or msg.service:
                continue
            doc = getattr(msg, "document", None)
            if not doc or not doc.file_name:
                continue
            if pattern.search(doc.file_name.strip().lower()):
                found.append(msg.id)
        await sleep(_BATCH_PAUSE)

    return sorted(found)
