from twitch_chat_irc import twitch_chat_irc
import time
import os
import threading
import types
import logging
from typing import Optional as _Opt, List, Tuple
import ast
import datetime as _dt

connection = twitch_chat_irc.TwitchChatIRC()

# --- Patch to handle occasional UTF-8 decoding glitches gracefully ---
def _safe_recvall(self, buffer_size):
    data = b''
    while True:
        part = self._TwitchChatIRC__SOCKET.recv(buffer_size)
        data += part
        if len(part) < buffer_size:
            break
    return data.decode(errors='ignore')

connection._TwitchChatIRC__recvall = types.MethodType(_safe_recvall, connection)
twitch_chat_irc.TwitchChatIRC._TwitchChatIRC__recvall = _safe_recvall

on_ad_break = False
# Store completed ad intervals as (start_ms, end_ms)
_ad_breaks: List[Tuple[int, int]] = []
_current_ad_start: int | None = None

_listener_thread = None
_stop_event = threading.Event()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

def on_message(message):
    if on_ad_break:
        return
    
    with open('chat.txt', 'a', encoding='utf-8') as f:
        clean_message = {
            'color': message['color'],
            'display_name': message['display-name'],
            'message': message['message'],
            'emotes': message['emotes'],
            # Store the raw, unadjusted timestamp from Twitch
            'timestamp': int(message['tmi-sent-ts'])
        }
        f.write(f"{str(clean_message)}\n")

def note_ad_break_start():
    """Mark start of an ad (we pause ingestion separately)."""
    global _current_ad_start
    if _current_ad_start is None:
        _current_ad_start = int(time.time() * 1000)
        print(f"Ad break started at {_current_ad_start}")

def note_ad_break_end():
    """Finalize current ad interval."""
    global _current_ad_start, _ad_breaks
    if _current_ad_start is None:
        print("No active ad break to end")
        return
    end = int(time.time() * 1000)
    if end > _current_ad_start:
        _ad_breaks.append((_current_ad_start, end))
        print(f"Ad break ended: {end - _current_ad_start}ms duration")
    _current_ad_start = None
    _merge_ad_intervals()

def _merge_ad_intervals():
    """Coalesce overlapping ad intervals."""
    global _ad_breaks
    if len(_ad_breaks) < 2:
        return
    _ad_breaks.sort()
    merged = []
    cs, ce = _ad_breaks[0]
    for s, e in _ad_breaks[1:]:
        if s <= ce:          # overlap / touch
            ce = max(ce, e)
        else:
            merged.append((cs, ce))
            cs, ce = s, e
    merged.append((cs, ce))
    _ad_breaks = merged
    print(f"Merged ad intervals: {_ad_breaks}")

def start_watching_chat(channel_name):
    """Begin ingesting chat messages in a background thread."""
    try:
        with open('chat.txt', 'w', encoding='utf-8') as f:
            f.write('')
        print("Cleared chat.txt for new recording session")
    except Exception as e:
        print(f"Error clearing chat.txt: {e}")

    def _listen():
        """Internal thread target that keeps the chat connection alive."""
        global connection
        while not _stop_event.is_set():
            try:
                connection.listen(channel_name, on_message=on_message)
            except Exception as e:
                logging.warning(f"Chat connection error: {e}. Attempting to reconnect in 5 seconds…")
                try:
                    connection.close_connection()
                except Exception:
                    pass
                if _stop_event.is_set():
                    break
                time.sleep(5)
                connection = twitch_chat_irc.TwitchChatIRC()
                connection._TwitchChatIRC__recvall = types.MethodType(_safe_recvall, connection)
            else:
                if not _stop_event.is_set():
                    logging.info("Chat connection closed unexpectedly. Reconnecting in 5 seconds…")
                    time.sleep(5)
        logging.info("Listener thread exiting cleanly.")

    _stop_event.clear()
    global _listener_thread
    if _listener_thread and _listener_thread.is_alive():
        return
    _listener_thread = threading.Thread(target=_listen, daemon=True)
    _listener_thread.start()

def stop_watching_chat():
    """Stop chat ingestion and wait briefly for the listener thread to exit."""
    _stop_event.set()
    connection.close_connection()
    global _listener_thread
    if _listener_thread and _listener_thread.is_alive():
        _listener_thread.join(timeout=2)
    _listener_thread = None

def update_on_ad_break(status):
    global on_ad_break
    on_ad_break = status

def get_on_ad_break():
    return on_ad_break

def _total_ad_time_before(ts_ms: int) -> int:
    """Sum durations of ad intervals whose *end* <= ts_ms."""
    total = 0
    for s, e in _ad_breaks:
        if e <= ts_ms:
            total += (e - s)
        # if ts is inside an ad we *do not* deduct partial (we paused ingestion).
    return total

def dump_chat(clip_name: str, segment_length: int):
    """Dump chat for a clip, compressing completed ad gaps."""
    print(f"Starting chat dump for clip: {clip_name}, segment length: {segment_length}s")
    ts_part = os.path.basename(clip_name)
    if ts_part.lower().endswith(".mp4"):
        ts_part = ts_part[:-4]

    clip_start_ms: _Opt[int] = None
    try:
        dt = _dt.datetime.strptime(ts_part, "%Y%m%d-%H%M%S")
        clip_start_ms = int(dt.timestamp() * 1000)
        print(f"Parsed clip start timestamp: {clip_start_ms}")
    except ValueError:
        print(f"⚠️  Could not parse timestamp from '{clip_name}'. Chat timing may be inaccurate.")

    os.makedirs("chat_replays", exist_ok=True)
    try:
        with open("chat.txt", "r", encoding="utf-8") as fh:
            chat_lines = fh.readlines()
    except FileNotFoundError:
        print("No chat.txt file found – nothing to dump")
        return

    if not chat_lines:
        print("No chat content to process")
        return

    clip_start_ad_total = _total_ad_time_before(clip_start_ms) if clip_start_ms else 0
    print(f"Total ad time before clip start: {clip_start_ad_total}ms")

    parsed_messages: list[dict] = []
    remaining_lines: list[str] = []
    
    # Raw (wall-clock) clip end (only used if we fail to derive adjusted timeline early)
    clip_end_ms = clip_start_ms + (segment_length * 1000) if clip_start_ms else None

    for raw in chat_lines:
        raw = raw.strip()
        if not raw:
            continue

        try:
            msg = ast.literal_eval(raw)
            ts = int(msg.get("timestamp", 0))

            if clip_start_ms is None:
                # Fallback: first message becomes anchor.
                clip_start_ms = ts
                clip_end_ms = ts + (segment_length * 1000)
                clip_start_ad_total = _total_ad_time_before(clip_start_ms)
                print(f"Using fallback anchor from first message: {clip_start_ms}")

            # Compressed (ad-removed) delay
            msg_ad_total = _total_ad_time_before(ts)
            rel_delay = ((ts - clip_start_ms) - (msg_ad_total - clip_start_ad_total)) / 1000.0

            # Include based on *adjusted* timeline only (ads removed). This prevents dropping
            # post-ad messages that still belong to this video segment.
            if 0 <= rel_delay < segment_length:
                parsed_messages.append({
                    "delay": rel_delay,
                    "color": msg.get("color"),
                    "display_name": msg.get("display_name"),
                    "message": msg.get("message"),
                    "emotes": msg.get("emotes"),
                })
            # else: outside adjusted window -> discard (or could queue for next, but we no
            # longer mis-assign valid in-segment messages due to wall-clock ad gaps)
            else:
                # If you still want to carry forward future messages, detect only those with rel_delay >= segment_length
                if rel_delay >= segment_length:
                    remaining_lines.append(raw)

        except Exception as exc:
            print(f"Error parsing chat line: {exc}")
            continue

    out_path = f"chat_replays/{clip_name}.txt"
    with open(out_path, "w", encoding="utf-8") as fh:
        for m in parsed_messages:
            fh.write(f"{str(m)}\n")
    print(f"Chat dumped to {out_path} with {len(parsed_messages)} messages")

    try:
        with open("chat.txt", "w", encoding="utf-8") as fh:
            for raw in remaining_lines:
                fh.write(f"{raw}\n")
        print(f"chat.txt updated – kept {len(remaining_lines)} messages for next clip")
    except Exception as exc:
        print(f"Error updating chat.txt: {exc}")

    # Optional prune: drop ad intervals ending before earliest kept message
    if clip_start_ms:
        earliest_needed = clip_start_ms
        global _ad_breaks
        old_count = len(_ad_breaks)
        _ad_breaks = [(s, e) for (s, e) in _ad_breaks if e >= earliest_needed]
        print(f"Pruned ad break history: {old_count} -> {len(_ad_breaks)} intervals")
