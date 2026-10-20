"""Normalize flat playlist metadata without fetching each video's formats."""

import re
from urllib.parse import urlparse

from .options import MAX_PLAYLIST_ITEMS


def normalize_playlist(info: dict) -> dict:
    raw_entries = info.get("entries") or []
    if len(raw_entries) > MAX_PLAYLIST_ITEMS or (info.get("playlist_count") or 0) > MAX_PLAYLIST_ITEMS:
        raise ValueError(f"В плейлисте больше {MAX_PLAYLIST_ITEMS} видео. Разделите его на несколько списков.")
    entries = []
    for index, item in enumerate(raw_entries, 1):
        item = item or {}
        video_id = str(item.get("id") or "")
        title = str(item.get("title") or "Недоступное видео")
        reason = ""
        if not re.fullmatch(r"[\w-]+", video_id) or item.get("_type") in {"playlist", "multi_video"}:
            reason = "Нет ссылки на видео"
        elif title.lower() in {"[deleted video]", "[private video]", "[unavailable video]"}:
            reason = "Видео удалено или доступ закрыт"
        elif item.get("is_live") or item.get("live_status") in {"is_live", "is_upcoming"}:
            reason = "Текущий или запланированный эфир"
        entries.append(dict(index=index, id=video_id, title=title[:500],
                            url=f"https://www.youtube.com/watch?v={video_id}" if not reason else None,
                            duration=item.get("duration"), available=not reason, reason=reason))
    if not entries:
        raise ValueError("Плейлист пуст или недоступен. Проверьте ссылку и cookies.")
    thumbnails = info.get("thumbnails") or []
    thumbnail = info.get("thumbnail") or (thumbnails[-1].get("url") if thumbnails else "")
    if urlparse(thumbnail or "").scheme not in {"http", "https"}:
        thumbnail = ""
    return dict(kind="playlist", id=info.get("id"), title=info.get("title") or "Плейлист",
                channel=info.get("channel") or info.get("uploader"), thumbnail=thumbnail or "",
                duration=sum(item["duration"] or 0 for item in entries if item["available"]),
                unknown_durations=sum(item["duration"] is None for item in entries if item["available"]),
                entries=entries, entry_count=len(entries), available_count=sum(item["available"] for item in entries),
                formats=[], resolutions=[], audio_languages=[], subtitles=[], chapters=[])
