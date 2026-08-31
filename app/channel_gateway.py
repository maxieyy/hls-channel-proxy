import base64
import logging
import secrets
import threading
import time
from urllib.parse import urljoin

import requests
from flask import Flask, Response, request

from .resolver import get_stream


app = Flask(__name__)

PUBLIC_HLS_URL = ""  # Set to the public HTTPS base URL when needed; request host is preferred.
UPSTREAM_REFERER = "https://hamis.romponalis.st/"
HLSD_URL = "http://127.0.0.1:8080"
SEGMENT_TTL = 900
TOKEN_BYTES = 12
CLEANUP_INTERVAL = 60

SEGMENTS = {}
SEGMENTS_LOCK = threading.Lock()
HLSD_SESSION = requests.Session()

logger = logging.getLogger("hls-channel-gateway")


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Expose-Headers": (
            "Content-Length, Content-Range, Accept-Ranges, Content-Type, ETag"
        ),
        "Cache-Control": "no-store, no-cache, must-revalidate",
    }


def apply_cors(response):
    for key, value in cors_headers().items():
        response.headers[key] = value
    return response


def make_response(body, status=200, content_type=None):
    response = Response(
        body,
        status=status,
        content_type=content_type,
    )
    return apply_cors(response)


def public_base_url():
    if PUBLIC_HLS_URL:
        return PUBLIC_HLS_URL.rstrip("/")
    return request.host_url.rstrip("/")


def make_hlsd_target(upstream_url):
    """Create the internal hlsd URL without exposing it publicly."""
    payload = f"{upstream_url}|{UPSTREAM_REFERER}"
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    return f"{HLSD_URL}/{encoded}.ts"


def new_segment_token(upstream_url):
    token = secrets.token_urlsafe(TOKEN_BYTES)
    now = time.time()

    with SEGMENTS_LOCK:
        while token in SEGMENTS:
            token = secrets.token_urlsafe(TOKEN_BYTES)

        SEGMENTS[token] = {
            "hlsd_url": make_hlsd_target(upstream_url),
            "created": now,
            "expires_at": now + SEGMENT_TTL,
        }

    return token


def get_segment(token):
    now = time.time()

    with SEGMENTS_LOCK:
        item = SEGMENTS.get(token)
        if not item:
            return None

        if item["expires_at"] <= now:
            SEGMENTS.pop(token, None)
            return None

        return item


def cleanup_segments():
    now = time.time()
    with SEGMENTS_LOCK:
        expired = [
            token
            for token, item in SEGMENTS.items()
            if item["expires_at"] <= now
        ]
        for token in expired:
            SEGMENTS.pop(token, None)

    if expired:
        logger.info("Removed %d expired segment mappings", len(expired))


def cleanup_loop():
    while True:
        try:
            cleanup_segments()
        except Exception:
            logger.exception("Segment cleanup failed")
        time.sleep(CLEANUP_INTERVAL)


def public_segment_url(token):
    return f"{public_base_url()}/s/{token}.ts"


def rewrite_uri_attributes(line, base_url):
    def replace(match):
        uri = match.group(1)
        absolute = urljoin(base_url, uri)
        token = new_segment_token(absolute)
        return f'URI="{public_segment_url(token)}"'

    import re

    return re.sub(
        r'URI="([^"]+)"',
        replace,
        line,
        flags=re.IGNORECASE,
    )


def rewrite_media_playlist(playlist_text, media_url):
    output = []

    for raw_line in playlist_text.splitlines():
        line = raw_line.strip()

        if not line:
            output.append("")
            continue

        if line.startswith("#"):
            output.append(
                rewrite_uri_attributes(
                    line,
                    media_url,
                )
            )
            continue

        absolute = urljoin(media_url, line)
        token = new_segment_token(absolute)
        output.append(public_segment_url(token))

    return "\n".join(output) + "\n"


@app.route("/", methods=["GET", "HEAD", "OPTIONS"])
def index():
    if request.method == "OPTIONS":
        return make_response("", status=204)

    return make_response(
        "HLS channel gateway is running\n",
        content_type="text/plain",
    )


@app.route("/health", methods=["GET", "HEAD"])
def health():
    return make_response(
        "OK\n",
        content_type="text/plain",
    )


@app.route("/<channel>.m3u8", methods=["GET", "HEAD", "OPTIONS"])
def channel_playlist(channel):
    if request.method == "OPTIONS":
        return make_response("", status=204)

    if not channel.isdigit():
        return make_response(
            "Invalid channel ID\n",
            status=400,
            content_type="text/plain",
        )

    logger.info("Resolving channel %s", channel)

    try:
        result = get_stream(channel)
    except Exception as exc:
        logger.exception("Channel %s resolution failed", channel)
        return make_response(
            f"Unable to resolve channel {channel}: {exc}\n",
            status=502,
            content_type="text/plain",
        )

    media_url = result.get("media_m3u8")
    media_playlist = result.get("media_playlist")

    if not media_url or media_playlist is None:
        return make_response(
            "Resolver returned no media playlist\n",
            status=502,
            content_type="text/plain",
        )

    try:
        rewritten = rewrite_media_playlist(
            media_playlist,
            media_url,
        )
    except Exception as exc:
        logger.exception("Playlist rewrite failed")
        return make_response(
            f"Playlist rewrite error: {exc}\n",
            status=502,
            content_type="text/plain",
        )

    if request.method == "HEAD":
        return make_response(
            "",
            content_type="application/vnd.apple.mpegurl",
        )

    return make_response(
        rewritten,
        content_type="application/vnd.apple.mpegurl",
    )


@app.route("/s/<token>.ts", methods=["GET", "HEAD", "OPTIONS"])
def segment_proxy(token):
    if request.method == "OPTIONS":
        return make_response("", status=204)

    item = get_segment(token)

    if not item:
        return make_response(
            "Segment not found or expired\n",
            status=404,
            content_type="text/plain",
        )

    headers = {}

    for name in ("Range", "If-Range"):
        value = request.headers.get(name)
        if value:
            headers[name] = value

    try:
        upstream = HLSD_SESSION.request(
            method=request.method,
            url=item["hlsd_url"],
            headers=headers,
            stream=True,
            timeout=60,
        )
    except requests.RequestException as exc:
        logger.exception("hlsd request failed")
        return make_response(
            f"hlsd error: {exc}\n",
            status=502,
            content_type="text/plain",
        )

    if upstream.status_code >= 400:
        status = upstream.status_code
        upstream.close()
        return make_response(
            f"hlsd returned HTTP {status}\n",
            status=status,
            content_type="text/plain",
        )

    response = Response(
        stream_response(upstream),
        status=upstream.status_code,
    )

    for name in (
        "Content-Type",
        "Content-Length",
        "Content-Range",
        "Accept-Ranges",
        "ETag",
        "Last-Modified",
        "Content-Encoding",
    ):
        value = upstream.headers.get(name)
        if value:
            response.headers[name] = value

    return apply_cors(response)


def stream_response(upstream):
    try:
        for chunk in upstream.iter_content(chunk_size=64 * 1024):
            if chunk:
                yield chunk
    finally:
        upstream.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    cleanup_thread = threading.Thread(
        target=cleanup_loop,
        daemon=True,
    )
    cleanup_thread.start()

    logger.info("Listening on %s:%d", "127.0.0.1", 8090)
    logger.info("hlsd endpoint: %s", HLSD_URL)
    logger.info("Upstream Referer: %s", UPSTREAM_REFERER)

    app.run(
        host="127.0.0.1",
        port=8090,
        debug=False,
        threaded=True,
    )
