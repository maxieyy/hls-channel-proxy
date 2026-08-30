import base64
import os
import re
from urllib.parse import urljoin

from flask import Flask, Response

from .resolver import get_stream


app = Flask(__name__)

PUBLIC_HLS_URL = os.getenv(
    "PUBLIC_HLS_URL",
    "https://prod.example.com",
).rstrip("/")

UPSTREAM_REFERER = os.getenv(
    "UPSTREAM_REFERER",
    "https://hamis.romponalis.st/",
)



def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Expose-Headers": (
            "Content-Length, Content-Range, Accept-Ranges, Content-Type"
        ),
        "Cache-Control": "no-store, no-cache, must-revalidate",
    }


def decode_channel_id(path_value):
    """Return a numeric channel ID from a URL component."""
    if not re.fullmatch(r"\d+", path_value or ""):
        return None
    return path_value


def make_hlsd_url(video_url):
    """Create the public URL understood by hlsd."""
    payload = f"{video_url}|{UPSTREAM_REFERER}"

    encoded = base64.b64encode(
        payload.encode("utf-8")
    ).decode("ascii")

    return f"{PUBLIC_HLS_URL}/{encoded}.ts"


def rewrite_uri_attributes(line, base_url):
    """Rewrite URI= attributes in HLS tags."""

    def replace(match):
        uri = match.group(1)
        absolute = urljoin(base_url, uri)
        return f'URI="{make_hlsd_url(absolute)}"'

    return re.sub(
        r'URI="([^"]+)"',
        replace,
        line,
        flags=re.IGNORECASE,
    )


def rewrite_media_playlist(playlist_text, media_url):
    """Rewrite segment/key/map URLs through the public hlsd endpoint."""
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
        output.append(make_hlsd_url(absolute))

    return "\n".join(output) + "\n"


def response(data, status=200, content_type=None):
    result = Response(
        data,
        status=status,
        content_type=content_type,
    )

    for key, value in cors_headers().items():
        result.headers[key] = value

    return result


@app.route("/", methods=["GET", "HEAD", "OPTIONS"])
def index():
    if app.request_class is not None:
        # OPTIONS needs no body.
        pass

    if getattr(__import__("flask"), "request").method == "OPTIONS":
        return response("", status=204)

    return response(
        "HLS channel gateway is running\n",
        content_type="text/plain",
    )


@app.route("/<channel>.m3u8", methods=["GET", "HEAD", "OPTIONS"])
def channel_playlist(channel):
    from flask import request

    if request.method == "OPTIONS":
        return response("", status=204)

    channel_id = decode_channel_id(channel)

    if channel_id is None:
        return response(
            "Invalid channel ID",
            status=400,
            content_type="text/plain",
        )

    try:
        result = get_stream(channel_id)
    except Exception as exc:
        app.logger.exception(
            "Channel %s resolution failed",
            channel_id,
        )
        return response(
            f"Unable to resolve channel {channel_id}: {exc}",
            status=502,
            content_type="text/plain",
        )

    media_url = result["media_m3u8"]
    media_playlist = result["media_playlist"]

    rewritten = rewrite_media_playlist(
        media_playlist,
        media_url,
    )

    app.logger.info(
        "channel=%s media=%s",
        channel_id,
        media_url,
    )

    if request.method == "HEAD":
        return response(
            "",
            content_type="application/vnd.apple.mpegurl",
        )

    return response(
        rewritten,
        content_type="application/vnd.apple.mpegurl",
    )
