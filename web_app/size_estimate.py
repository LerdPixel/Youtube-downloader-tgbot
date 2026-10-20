"""Estimate media size using the same format selector as the actual download."""

import math

import yt_dlp

from .options import DownloadSettings, seconds, select_format


def positive(value):
    return value if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 else None


def stream_size(stream: dict, duration: float | None, video_only=False, audio_only=False):
    has_video = stream.get("vcodec") not in {None, "none"}
    has_audio = stream.get("acodec") not in {None, "none"}
    extracting = video_only and has_audio or audio_only and has_video
    if not extracting:
        for key in ("filesize", "filesize_approx"):
            if size := positive(stream.get(key)):
                return size, key != "filesize"
    bitrate = positive(stream.get("vbr" if video_only else "abr" if audio_only else "tbr"))
    if not bitrate and video_only and has_audio:
        total, audio = positive(stream.get("tbr")), positive(stream.get("abr"))
        bitrate = positive(total - audio) if total and audio else None
    if not bitrate and not extracting:
        bitrate = positive(stream.get("abr" if has_audio and not has_video else "vbr"))
    if bitrate and duration:
        return bitrate * 1000 / 8 * duration, True
    return None, True


def estimate_size(info: dict, settings: DownloadSettings) -> dict:
    formats = [dict(item, protocol=item.get("protocol") or "https") for item in info.get("formats", [])]
    base = dict(size_bytes=None, approximate=True, format_ids=[], note="Размер пока неизвестен.")
    if not formats:
        return base
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "check_formats": False, "cachedir": False}, auto_init=False) as downloader:
            selector = downloader.build_format_selector(select_format(settings))
            selected = list(selector(dict(formats=formats,
                has_merged_format=any(item.get("acodec") not in {None, "none"} and item.get("vcodec") not in {None, "none"} for item in formats),
                incomplete_formats=all(item.get("vcodec") == "none" for item in formats) or all(item.get("acodec") == "none" for item in formats))))
    except (ValueError, SyntaxError, KeyError) as exc:
        return dict(base, note=f"Не удалось выбрать потоки: {exc}")
    if not selected:
        return dict(base, note="Для этих настроек нет доступных потоков. Измените формат, качество или язык.")
    streams = [stream for selection in selected for stream in selection.get("requested_formats", [selection])]
    base["format_ids"] = [item["format_id"] for item in streams]
    full_duration = positive(info.get("duration"))
    duration = full_duration
    clipped = bool(settings.start or settings.end)
    if clipped:
        start = seconds(settings.start or "0")
        end = min(seconds(settings.end), full_duration) if settings.end and full_duration else seconds(settings.end) if settings.end else full_duration
        if end is None:
            return dict(base, note="Для оценки фрагмента неизвестна длительность исходного видео.")
        duration = max(0, end - start)
        if duration == 0:
            return dict(base, note="Фрагмент находится за пределами видео.")
    total, approximate = 0, len(streams) > 1 or settings.metadata or settings.chapters
    for stream in streams:
        if settings.mode == "audio":
            if stream.get("acodec") in {None, "none"}:
                continue
            codec = stream.get("acodec") or ""
            copy_audio = settings.audio_format == "best" or (
                settings.audio_format in {"aac", "m4a"} and codec.startswith(("aac", "mp4a"))
                or settings.audio_format == "mp3" and codec.startswith("mp3")
                or settings.audio_format == "opus" and codec.startswith("opus")
                or settings.audio_format == "vorbis" and codec.startswith("vorbis")
                or settings.audio_format in {"flac", "alac"} and codec.startswith(settings.audio_format))
            if settings.audio_format == "wav":
                if not duration or not positive(stream.get("asr")) or not positive(stream.get("audio_channels")):
                    return dict(base, note="Для оценки WAV неизвестны частота дискретизации или число каналов.")
                size = duration * stream["asr"] * stream["audio_channels"] * 2
                uncertain = True
            elif copy_audio:
                size, uncertain = stream_size(stream, full_duration, audio_only=True)
                if clipped and size is not None:
                    size = size * duration / full_duration if full_duration else None
                uncertain = uncertain or stream.get("ext") != settings.audio_format
            elif settings.audio_format in {"flac", "alac"} or settings.audio_quality == "0":
                return dict(base, note="Размер сжатия без потерь или VBR станет известен после конвертации.")
            else:
                size = duration * int(settings.audio_quality) * 1000 / 8 if duration else None
                uncertain = True
        else:
            if settings.mode == "silent" and stream.get("vcodec") in {None, "none"}:
                continue
            size, uncertain = stream_size(stream, full_duration, video_only=settings.mode == "silent")
            if clipped and size is not None:
                size = size * duration / full_duration if full_duration else None
        if size is None:
            return dict(base, note="YouTube не сообщил размер или битрейт одного из выбранных потоков.")
        total += size
        approximate = approximate or uncertain or clipped
    if total <= 0:
        return dict(base, note="Выбранные потоки не содержат нужного видео или аудио.")
    notes = ["Оценка медиафайла; обложки, субтитры и другие дополнительные файлы не включены."]
    if clipped:
        notes.append("Размер фрагмента рассчитан пропорционально длительности; битрейт может меняться.")
    if settings.precise_cuts and clipped:
        notes.append("При точной обрезке перекодирование может изменить размер.")
    return dict(size_bytes=round(total), approximate=bool(approximate), format_ids=base["format_ids"], note=" ".join(notes))
