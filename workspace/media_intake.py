from __future__ import annotations

import ipaddress
import json
import re
import shutil
import socket
import tempfile
import unicodedata
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlparse, urlunparse
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)

import cv2

MAX_VIDEO_BYTES = 2_000_000_000
CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 45
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
VIDEO_MIME_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-m4v": ".m4v",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "video/x-msvideo": ".avi",
    "video/avi": ".avi",
}
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _normalize_youtube_url(value: str) -> tuple[str, str] | None:
    """Return (canonical URL, video ID) for recognized YouTube video URLs."""
    parsed = urlparse(value.strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in YOUTUBE_HOSTS:
        return None

    video_id = None
    path_parts = [part for part in parsed.path.split("/") if part]
    if host in {"youtu.be", "www.youtu.be"}:
        video_id = path_parts[0] if path_parts else None
    elif parsed.path == "/watch":
        video_id = parse_qs(parsed.query).get("v", [None])[0]
    elif len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live", "v"}:
        video_id = path_parts[1]

    if not video_id or not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise ValueError(
            "This is a YouTube link, but it does not contain a recognized video ID. "
            "Use a Shorts, watch, youtu.be, or embed video URL."
        )
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    return canonical, video_id


def _fetch_youtube_metadata(canonical_url: str) -> dict:
    """Fetch public oEmbed metadata only; never request video or stream bytes."""
    from urllib.request import urlopen

    endpoint = (
        "https://www.youtube.com/oembed?url="
        + quote(canonical_url, safe="")
        + "&format=json"
    )
    request = Request(
        endpoint,
        headers={
            "User-Agent": "ShortsPrototype/1.0",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = response.read(1_000_001)
        if len(payload) > 1_000_000:
            raise ValueError("YouTube returned an unexpectedly large metadata response.")
        data = json.loads(payload.decode("utf-8"))
        return {
            "title": data.get("title"),
            "creator": data.get("author_name"),
            "thumbnail_url": data.get("thumbnail_url"),
            "provider": data.get("provider_name", "YouTube"),
        }
    except Exception as exc:
        return {
            "metadata_unavailable": True,
            "metadata_note": f"YouTube metadata lookup was unavailable ({type(exc).__name__}).",
        }


def _validate_public_http_url(value: str) -> str:
    """Validate an HTTP(S) URL and reject private/local network destinations."""
    parsed = urlparse(value.strip())
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("Direct media URLs must use HTTP or HTTPS.")
    if not parsed.hostname:
        raise ValueError("The direct media URL has no hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing embedded usernames or passwords are not accepted.")
    if parsed.fragment:
        parsed = parsed._replace(fragment="")
    port = parsed.port or (443 if scheme == "https" else 80)
    if port not in {80, 443}:
        raise ValueError("Direct media URLs may only use standard HTTP/HTTPS ports.")

    host = parsed.hostname.rstrip(".")
    try:
        literal_ip = ipaddress.ip_address(host)
        addresses = {literal_ip}
    except ValueError:
        try:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError(f"Could not resolve the media host: {host}") from exc
        addresses = {
            ipaddress.ip_address(record[4][0].split("%", 1)[0])
            for record in records
        }

    if not addresses:
        raise ValueError("The media host did not resolve to an address.")
    for address in addresses:
        if not address.is_global:
            raise ValueError(
                "The media URL resolves to a private, local, reserved, or non-public address."
            )
    return urlunparse(parsed)


class _SafeRedirectHandler(HTTPRedirectHandler):
    """Revalidate every redirect so a public URL cannot redirect to localhost."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _filename_from_headers(content_disposition: str | None) -> str | None:
    if not content_disposition:
        return None
    message = Message()
    message["content-disposition"] = content_disposition
    value = message.get_filename()
    return unquote(value) if value else None


def _safe_filename(candidate: str, extension: str) -> str:
    name = unicodedata.normalize("NFKC", candidate or "inspiration-video")
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "inspiration-video"
    supplied_suffix = Path(name).suffix.lower()
    if supplied_suffix in ALLOWED_EXTENSIONS:
        name = Path(name).stem + supplied_suffix
    else:
        # A period inside a title (for example, "Mrs. Asher B. Wertheimer")
        # is part of the title, not a filename extension.
        name = name + extension
    stem = Path(name).stem[:110].rstrip(" .-_") or "inspiration-video"
    return stem + Path(name).suffix.lower()


def _unique_destination(inbox: Path, filename: str) -> Path:
    candidate = inbox / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    index = 2
    while True:
        option = inbox / f"{stem}-{index}{suffix}"
        if not option.exists():
            return option
        index += 1


def _validate_video_file(path: Path) -> dict:
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ValueError(
                "The downloaded file is not a readable video. Check that the URL points "
                "directly to a video file rather than a web page or preview."
            )
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        ok, first_frame = capture.read()
        if not ok or first_frame is None or width < 2 or height < 2 or fps <= 0 or frames < 1:
            raise ValueError("The downloaded file failed video integrity checks.")
        return {
            "width": width,
            "height": height,
            "fps": round(fps, 3),
            "frames": frames,
            "duration_seconds": round(frames / fps, 3),
        }
    finally:
        capture.release()


def _download_direct_media(
    source_url: str,
    inbox_dir: Path,
    filename: str | None,
) -> dict:
    clean_url = _validate_public_http_url(source_url)
    request = Request(
        clean_url,
        headers={
            "User-Agent": "ShortsPrototype/1.0",
            "Accept": "video/*,application/octet-stream;q=0.9,*/*;q=0.1",
        },
        method="GET",
    )
    opener = build_opener(_SafeRedirectHandler())
    temp_path: Path | None = None

    try:
        with opener.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None) or response.getcode()
            if status < 200 or status >= 300:
                raise ValueError(f"The media server returned HTTP {status}.")

            headers = response.headers
            content_type = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            disposition = headers.get("Content-Disposition")
            disposition_name = _filename_from_headers(disposition)

            parsed_path_name = Path(unquote(urlparse(clean_url).path)).name
            preferred_name = filename or disposition_name or parsed_path_name
            chosen_suffix = Path(preferred_name or "").suffix.lower()
            extension = chosen_suffix if chosen_suffix in ALLOWED_EXTENSIONS else (
                VIDEO_MIME_EXTENSIONS.get(content_type) or ".mp4"
            )
            final_name = _safe_filename(preferred_name or "inspiration-video" + extension, extension)

            if content_type and not (
                content_type.startswith("video/")
                or content_type == "application/octet-stream"
                or content_type == "binary/octet-stream"
            ):
                raise ValueError(
                    f"The URL returned {content_type!r}, not a video file. "
                    "Use a direct media-file URL."
                )

            content_length = headers.get("Content-Length")
            if content_length:
                try:
                    announced_size = int(content_length)
                except ValueError:
                    announced_size = None
                if announced_size is not None and announced_size > MAX_VIDEO_BYTES:
                    raise ValueError("The video exceeds the 2 GB prototype limit.")

            inbox_dir.mkdir(parents=True, exist_ok=True)
            temp_handle = tempfile.NamedTemporaryFile(
                prefix=".shorts-import-",
                suffix=f".part{extension}",
                dir=inbox_dir,
                delete=False,
            )
            temp_path = Path(temp_handle.name)
            total = 0
            try:
                with temp_handle:
                    while True:
                        chunk = response.read(CHUNK_BYTES)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_VIDEO_BYTES:
                            raise ValueError("The video exceeded the 2 GB prototype limit.")
                        temp_handle.write(chunk)
            except Exception:
                raise

        if total < 1024:
            raise ValueError("The downloaded file is too small to be a valid video.")

        media_info = _validate_video_file(temp_path)
        destination = _unique_destination(inbox_dir, final_name)
        temp_path.replace(destination)
        temp_path = None

        return {
            "status": "downloaded",
            "source_type": "authorized_direct_media_url",
            "downloaded": True,
            "file": str(destination),
            "workspace_relative_path": f"inbox/{destination.name}",
            "size_bytes": destination.stat().st_size,
            "media": media_info,
        }
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)



def _download_youtube_media(
    canonical_url: str,
    video_id: str,
    metadata: dict,
    inbox_dir: Path,
    filename: str | None,
) -> dict:
    """Download one explicitly authorized YouTube video with yt-dlp."""
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlp is not installed in the Shorts Prototype environment. "
            "Install the pinned project requirements and restart the tunnel."
        ) from exc

    try:
        import imageio_ffmpeg

        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError("The bundled FFmpeg executable is unavailable.") from exc

    node_path = shutil.which("node")
    if not node_path:
        fallback_node = Path(r"C:\Program Files\nodejs\node.exe")
        if fallback_node.is_file():
            node_path = str(fallback_node)
    if not node_path:
        raise RuntimeError(
            "yt-dlp needs a JavaScript runtime for current YouTube support. "
            "Node.js was not found; install Node.js or add it to PATH, then restart the tunnel."
        )

    inbox_dir.mkdir(parents=True, exist_ok=True)

    def enforce_size_limit(progress: dict) -> None:
        observed_sizes = (
            progress.get("downloaded_bytes") or 0,
            progress.get("total_bytes") or 0,
            progress.get("total_bytes_estimate") or 0,
        )
        if max(observed_sizes) > MAX_VIDEO_BYTES:
            raise DownloadError("The video exceeds the 2 GB prototype download limit.")

    with tempfile.TemporaryDirectory(prefix=".shorts-youtube-", dir=inbox_dir) as temp_dir:
        ydl_options = {
            "format": "bv*+ba/b",
            "paths": {"home": temp_dir, "temp": temp_dir},
            "outtmpl": {"default": "%(id)s.%(ext)s"},
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "ignoreerrors": False,
            "overwrites": False,
            "windowsfilenames": True,
            "trim_file_name": 160,
            "retries": 3,
            "fragment_retries": 3,
            "extractor_retries": 3,
            "socket_timeout": DOWNLOAD_TIMEOUT_SECONDS,
            "max_filesize": MAX_VIDEO_BYTES,
            "ffmpeg_location": ffmpeg_path,
            "js_runtimes": {"node": {"path": node_path}},
            "progress_hooks": [enforce_size_limit],
        }

        try:
            with YoutubeDL(ydl_options) as downloader:
                info = downloader.extract_info(canonical_url, download=True)
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            raise RuntimeError(f"yt-dlp could not download this YouTube video: {detail[:700]}") from exc

        if not isinstance(info, dict) or info.get("_type") == "playlist":
            raise RuntimeError("yt-dlp did not return a single-video download result.")

        temp_root = Path(temp_dir)
        candidates = [
            item for item in temp_root.rglob("*")
            if item.is_file() and item.suffix.lower() in ALLOWED_EXTENSIONS
            and not item.name.lower().endswith((".part", ".ytdl"))
        ]
        if not candidates:
            raise RuntimeError("yt-dlp completed without producing a recognizable video file.")
        candidates.sort(key=lambda item: (item.stat().st_size, item.stat().st_mtime), reverse=True)
        downloaded_file = candidates[0]
        file_size = downloaded_file.stat().st_size
        if file_size > MAX_VIDEO_BYTES:
            raise RuntimeError("The downloaded video exceeds the 2 GB prototype limit.")
        if file_size < 1024:
            raise RuntimeError("The downloaded file is too small to be a valid video.")

        media_info = _validate_video_file(downloaded_file)
        media_info["container"] = downloaded_file.suffix.lower().lstrip(".")
        media_info["format_id"] = info.get("format_id")
        media_info["format_note"] = info.get("format_note")

        preferred_name = filename or metadata.get("title") or downloaded_file.stem
        final_name = _safe_filename(preferred_name, downloaded_file.suffix.lower())
        destination = _unique_destination(inbox_dir, final_name)
        downloaded_file.replace(destination)

        return {
            "status": "downloaded",
            "source_type": "youtube_video",
            "downloaded": True,
            "video_id": video_id,
            "reference_url": canonical_url,
            "title": metadata.get("title") or info.get("title"),
            "creator": metadata.get("creator") or info.get("uploader"),
            "file": str(destination),
            "workspace_relative_path": f"inbox/{destination.name}",
            "size_bytes": destination.stat().st_size,
            "media": media_info,
        }

def import_inspiration_source(
    source_url: str,
    inbox_dir: str | Path,
    filename: str | None = None,
) -> dict:
    """Import a YouTube video or direct media file submitted by the user."""
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError("source_url is required.")
    source_url = source_url.strip()
    if len(source_url) > 8192:
        raise ValueError("source_url is too long.")

    youtube = _normalize_youtube_url(source_url)
    if youtube is not None:
        canonical_url, video_id = youtube
        metadata = _fetch_youtube_metadata(canonical_url)
        inbox = Path(inbox_dir).resolve()
        return _download_youtube_media(
            canonical_url=canonical_url,
            video_id=video_id,
            metadata=metadata,
            inbox_dir=inbox,
            filename=filename,
        )

    inbox = Path(inbox_dir).resolve()
    return _download_direct_media(source_url, inbox, filename)
