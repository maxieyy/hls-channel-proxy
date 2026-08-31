# HLS Channel Proxy

A Python channel gateway plus `warren-bank/node-HLS-Proxy` (`hlsd`) for authorized HLS sources.

> Use only with streams and domains you are authorized to proxy.

## Public URL format

The public endpoint uses plain numeric channel IDs:

```text
https://prod.example.com/38.m3u8
https://prod.example.com/100.m3u8
```

No Base64 encoding is used for channel IDs.

## Architecture

```text
Client
  |
  | HTTPS /38.m3u8
  v
Nginx :443
  |
  | all public requests
  v
channel gateway :8090
  |
  +--> /38.m3u8
  |      |
  |      +--> resolver.py
  |             |
  |             +--> stream page
  |             +--> backend/player page
  |             +--> fresh master playlist
  |             +--> fresh media playlist
  |
  +--> /s/<short-token>.ts
         |
         +--> internal hlsd URL
                |
                v
             hlsd :8080
                |
                +--> upstream media

hlsd remains bound to 127.0.0.1 and is never exposed directly.
```

## Why short segment tokens?

Upstream live playlists may contain very long signed URLs. Exposing those URLs directly can create very long public paths and can also leak the signed object URL to the client.

Instead, the gateway rewrites each media URI to a short opaque token:

```text
https://prod.example.com/s/Ab7K92xQ.ts
```

The gateway keeps the mapping in memory:

```text
Ab7K92xQ -> internal hlsd URL -> signed upstream URL
```

Mappings expire after 900 seconds by default.

## Upstream headers

For HLS/master/media requests handled by the resolver and for media requests handled by `hlsd`, the required upstream identity is:

```http
Origin: https://hamis.romponalis.st
Referer: https://hamis.romponalis.st/
```

The `hlsd` systemd unit intentionally does not require a User-Agent because the tested upstream accepts Origin and Referer alone.

The browser's arbitrary `Origin`, `Referer`, or `sec-*` headers are not blindly forwarded upstream.

## Repository layout

```text
hls-channel-proxy/
├── app/
│   ├── __init__.py
│   ├── channel_gateway.py
│   └── resolver.py
├── nginx/
│   └── hls-proxy.conf
├── systemd/
│   ├── channel-gateway.service
│   └── hls-proxy.service
├── .env.example
├── .gitignore
├── requirements.txt
├── package.json
├── server.py
└── README.md
```

## Requirements

- Linux VPS
- Python 3.10+
- Node.js compatible with the `hlsd` dependency
- Nginx
- TLS certificate for the public hostname

The proxy does not transcode video. Network throughput is usually the main capacity constraint.

## Install

```bash
sudo mkdir -p /opt/hls-channel-proxy
cd /opt/hls-channel-proxy
git clone https://github.com/maxieyy/hls-channel-proxy.git .

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

npm install
```

## Test resolver

```bash
source /opt/hls-channel-proxy/.venv/bin/activate
python3 -m app.resolver_test
```

Or:

```bash
python3 - <<'PY'
from app.resolver import get_stream

result = get_stream("38")
print("Master:", result["master_m3u8"])
print("Media:", result["media_m3u8"])
print(result["media_playlist"][:1000])
PY
```

## Test gateway

```bash
cd /opt/hls-channel-proxy
source .venv/bin/activate
python3 server.py
```

In another shell:

```bash
curl -v http://127.0.0.1:8090/38.m3u8
```

A successful playlist contains URLs resembling:

```text
http://127.0.0.1:8090/s/<short-token>.ts
```

When accessed through the public hostname, the same URLs become:

```text
https://prod.example.com/s/<short-token>.ts
```

## Test hlsd

The gateway uses `hlsd` internally for segment retrieval. Run it manually with:

```bash
./node_modules/.bin/hlsd \
  --host 127.0.0.1 \
  --port 8080 \
  --origin "https://hamis.romponalis.st" \
  --referer "https://hamis.romponalis.st/" \
  -v 3
```

## Nginx

The supplied Nginx configuration intentionally sends **everything** to port 8090. It does not route `.m3u8` to one service and `.ts` to another.

```nginx
location / {
    proxy_pass http://127.0.0.1:8090;
    proxy_http_version 1.1;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_set_header Range $http_range;
    proxy_set_header If-Range $http_if_range;

    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
    proxy_connect_timeout 30s;

    proxy_buffering off;
    proxy_request_buffering off;
}
```

Do not add CORS headers in Nginx when using the supplied gateway; Flask is the single owner of the CORS response headers. This avoids duplicate headers such as:

```text
Access-Control-Allow-Origin: *, *
```

## systemd

Install the services:

```bash
sudo cp systemd/hls-proxy.service /etc/systemd/system/
sudo cp systemd/channel-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hls-proxy
sudo systemctl enable --now channel-gateway
```

Check:

```bash
sudo systemctl status hls-proxy
sudo systemctl status channel-gateway
```

Logs:

```bash
sudo journalctl -u hls-proxy -f
sudo journalctl -u channel-gateway -f
```

## TLS with Certbot

Point DNS at the VPS, then:

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo certbot --nginx -d prod.example.com
```

Make sure the final HTTPS server block contains the `location /` proxy to `127.0.0.1:8090`.

## Live-refresh behavior

The gateway does not permanently store the current upstream master/media URL. Each `/38.m3u8` request resolves the channel again so rotating upstream tokens, timestamps, and signed media URLs can be refreshed.

Segment mappings are short-lived and in-memory. A restart clears them.

## Diagnostics

Check listeners:

```bash
sudo ss -lntp | grep -E ':8080|:8090|:80|:443'
```

Expected local services:

```text
127.0.0.1:8080  hlsd
127.0.0.1:8090  channel gateway
```

Test the public manifest:

```bash
curl -vk https://prod.example.com/38.m3u8
```

Test the health endpoint:

```bash
curl -v http://127.0.0.1:8090/health
```

## Security notes

The public channel route accepts numeric IDs only. Do not convert this into an unrestricted `?url=` internet proxy.

The short-token store is intentionally in memory. For larger deployments, add explicit authentication, rate limiting, monitoring, and a shared state/cache only when required.
