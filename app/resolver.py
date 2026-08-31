import base64
import os
import re
from urllib.parse import urljoin

import requests


SESSION = requests.Session()

TIMEOUT = float(os.getenv("RESOLVER_TIMEOUT", "15"))

STREAM_PAGE_BASE = os.getenv(
    "STREAM_PAGE_BASE",
    "https://dlstreams.st",
).rstrip("/")

STREAM_PROVIDER_ORIGIN = "https://hamis.romponalis.st"
STREAM_PROVIDER_REFERER = "https://hamis.romponalis.st/"


HLS_HEADERS = {
    "Origin": STREAM_PROVIDER_ORIGIN,
    "Referer": STREAM_PROVIDER_REFERER,
}


def _request(url, *, headers=None):
    response = SESSION.get(
        url,
        headers=headers,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response


def _extract_backend(stream_page_url, html):
    match = re.search(
        r'<iframe[^>]+src="([^"]+)"[^>]*>',
        html,
        re.IGNORECASE,
    )

    if not match:
        raise RuntimeError("Could not find iframe/backend URL")

    return urljoin(stream_page_url, match.group(1))


def _extract_master_url(html):
    match = re.search(
        r"window\.atob\('([^']+)'\)",
        html,
    )

    if not match:
        raise RuntimeError("Could not find encoded M3U8 URL")

    try:
        return base64.b64decode(match.group(1)).decode("utf-8")
    except Exception as exc:
        raise RuntimeError(
            f"Failed to decode master M3U8 URL: {exc}"
        ) from exc


def _extract_media_playlist(master_url, playlist_text):
    for raw_line in playlist_text.splitlines():
        line = raw_line.strip()

        if (
            line
            and not line.startswith("#")
            and line.lower().endswith(".m3u8")
        ):
            return urljoin(master_url, line)

    raise RuntimeError("Could not find media playlist URL")


def get_stream(channel_id):
    """Resolve a channel into a fresh master/media playlist."""

    channel_id = str(channel_id).strip()

    if not re.fullmatch(r"\d+", channel_id):
        raise ValueError("Channel ID must contain digits only")

    stream_page_url = (
        f"{STREAM_PAGE_BASE}/stream/stream-{channel_id}.php"
    )

    page = _request(
        stream_page_url,
        headers={
            "Referer":
            f"{STREAM_PAGE_BASE}/watch.php?id={channel_id}",
        },
    )

    backend_url = _extract_backend(
        stream_page_url,
        page.text,
    )

    backend = _request(
        backend_url,
        headers={
            "Referer": stream_page_url,
        },
    )

    master_url = _extract_master_url(backend.text)

    master = _request(
        master_url,
        headers=HLS_HEADERS,
    )

    if "Not found" in master.text:
        raise RuntimeError("Channel is offline or unavailable")

    media_url = _extract_media_playlist(
        master_url,
        master.text,
    )

    media = _request(
        media_url,
        headers=HLS_HEADERS,
    )

    return {
        "channel_id": channel_id,
        "backend_url": backend_url,
        "master_m3u8": master_url,
        "media_m3u8": media_url,
        "media_playlist": media.text,
    }
