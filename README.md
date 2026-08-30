# HLS Channel Proxy

A small production-oriented HLS channel gateway built around `warren-bank/node-HLS-Proxy` (`hlsd`) and a Python resolver.

> **Authorization:** Use this repository only with HLS sources, domains, and playback rights that you are authorized to proxy.

## What it does

The public API uses a simple channel-number URL:

```text
https://prod.example.com/38.m3u8
```

The browser never needs to know the current upstream token, timestamp, signed media URLs, or rotating segment hosts.

For every request to `/38.m3u8`, the gateway:

1. Decodes the channel number from the URL path (no Base64 in the public URL).
2. Runs the resolver for that channel.
3. Fetches the current master playlist.
4. Follows it to the current media playlist.
5. Rewrites each media URI into a public `.ts` URL handled by `hlsd`.
6. Returns the rewritten media playlist with CORS headers.
7. `hlsd` fetches each segment from its current upstream URL using configured `Origin`, `Referer`, and User-Agent headers.

The public architecture is:

```text
Browser
   |
   | HTTPS /38.m3u8
   v
Nginx :443
   |
   +---- *.m3u8 ----> channel-gateway :8090
   |                       |
   |                       +--> resolver.py
   |                       |      |
   |                       |      +--> fresh master
   |                       |      +--> fresh media playlist
   |                       |
   |                       +--> rewritten public .ts URLs
   |
   +---- *.ts ----------> hlsd :8080
                              |
                              +--> current signed media URL
```

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

Recommended VPS baseline:

- Ubuntu 22.04 or 24.04
- Node.js 20+ or 22 LTS
- Python 3.10+
- Nginx
- Certbot / Let's Encrypt
- A VPS with enough network capacity for the stream bitrate and expected viewers

The proxy does not transcode media, so CPU and RAM requirements are modest. Network throughput is normally the limiting resource.

## 1. Clone/install

```bash
sudo mkdir -p /opt/hls-channel-proxy
sudo chown -R $USER:$USER /opt/hls-channel-proxy
cd /opt/hls-channel-proxy

git clone YOUR_REPOSITORY_URL .
```

Install Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install Node dependencies:

```bash
npm install
```

## 2. Configure environment

Copy:

```bash
cp .env.example .env
```

Example:

```dotenv
HOST=127.0.0.1
GATEWAY_PORT=8090

PUBLIC_HLS_URL=https://prod.example.com

UPSTREAM_ORIGIN=https://hamis.romponalis.st
UPSTREAM_REFERER=https://hamis.romponalis.st/
UPSTREAM_USER_AGENT=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36

STREAM_PAGE_BASE=https://dlstreams.st
STREAM_PROVIDER_ORIGIN=https://hamis.romponalis.st
STREAM_PROVIDER_REFERER=https://hamis.romponalis.st/

RESOLVER_TIMEOUT=15
```

### Header behavior

The resolver uses the provider-facing headers configured in `.env`.

The Node `hlsd` process also receives the same `Origin`, `Referer`, and User-Agent values. The gateway does **not** forward arbitrary browser `sec-*` or `sec-fetch-*` headers.

## 3. Install `hlsd`

This project installs the upstream Node HLS proxy package as an npm dependency:

```bash
npm install
```

Verify:

```bash
./node_modules/.bin/hlsd --help
```

The expected package is `@warren-bank/hls-proxy`.

## 4. Test the resolver

Activate the virtual environment:

```bash
source /opt/hls-channel-proxy/.venv/bin/activate
```

Run:

```bash
python3 -m app.resolver_test
```

Or test directly from Python:

```bash
python3 - <<'PY'
from app.resolver import get_stream

result = get_stream("38")
print(result["master_m3u8"])
print(result["media_m3u8"])
print(result["media_playlist"][:1000])
PY
```

A successful result should contain a current media playlist.

## 5. Test the channel gateway

Start it manually:

```bash
source .venv/bin/activate
python3 server.py
```

Then from another shell:

```bash
curl -v http://127.0.0.1:8090/38.m3u8
```

Expected response:

```text
HTTP/1.1 200 OK
Content-Type: application/vnd.apple.mpegurl
Access-Control-Allow-Origin: *
```

The playlist should contain public URLs like:

```text
https://prod.example.com/<encoded-upstream-segment>.ts
```

There should be **no** `127.0.0.1:8080` URLs in the playlist returned to the client.

## 6. Start hlsd

For a manual test:

```bash
./node_modules/.bin/hlsd \
  --host 127.0.0.1 \
  --port 8080 \
  --origin "$UPSTREAM_ORIGIN" \
  --referer "$UPSTREAM_REFERER" \
  --useragent "$UPSTREAM_USER_AGENT" \
  -v 3
```

If the shell does not have the environment variables loaded, substitute their values directly.

## 7. Nginx

Copy the supplied configuration:

```bash
sudo cp nginx/hls-proxy.conf /etc/nginx/sites-available/hls-proxy
sudo ln -sf /etc/nginx/sites-available/hls-proxy /etc/nginx/sites-enabled/hls-proxy
sudo nginx -t
sudo systemctl reload nginx
```

The HTTPS server routes:

```text
*.m3u8 -> 127.0.0.1:8090
*.ts   -> 127.0.0.1:8080
```

This separation is important: `/38.m3u8` must go to the resolver, while rewritten segment URLs must go to `hlsd`.

## 8. TLS

Make sure DNS points your domain at the VPS.

Then install a certificate:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d prod.example.com
```

Certbot will create/update the HTTPS server block. Make sure the `location` blocks from `nginx/hls-proxy.conf` are present in the final `443` server block.

## 9. systemd

Install the services:

```bash
sudo cp systemd/hls-proxy.service /etc/systemd/system/
sudo cp systemd/channel-gateway.service /etc/systemd/system/
```

Reload:

```bash
sudo systemctl daemon-reload
```

Enable:

```bash
sudo systemctl enable hls-proxy
sudo systemctl enable channel-gateway
```

Start:

```bash
sudo systemctl start hls-proxy
sudo systemctl start channel-gateway
```

Check:

```bash
sudo systemctl status hls-proxy
sudo systemctl status channel-gateway
```

Logs:

```bash
sudo journalctl -u hls-proxy -f
```

```bash
sudo journalctl -u channel-gateway -f
```

## 10. Public URL format

The public URL is now channel-based:

```text
https://prod.example.com/38.m3u8
```

No Base64 channel encoding is necessary.

For channel 100:

```text
https://prod.example.com/100.m3u8
```

The public endpoint accepts decimal channel IDs only.

## 11. Live-refresh behavior

Do not permanently cache the resolved upstream media URL.

The gateway resolves the channel on every playlist request. That is intentional for providers that rotate tokens, timestamps, or signed object-storage URLs.

HLS clients normally request the live media playlist repeatedly. Each refresh therefore gets a current playlist with current media URLs.

## 12. Signed URLs

If the upstream media playlist contains signed URLs such as:

```text
https://storage.example/...?...X-Amz-Date=...&X-Amz-Expires=...&X-Amz-Signature=...
```

the gateway treats the URL as an opaque value and hands it to `hlsd`. The signature is not modified.

## 13. Range requests

`hlsd` is responsible for the actual media request and can preserve the media client's `Range` request behavior. Nginx should also leave the `Range` header intact for `.ts` requests.

## 14. CORS

The gateway and Nginx expose CORS headers suitable for a cross-origin HLS player.

For production, replace `*` with your actual player origin if you want tighter access control.

## 15. Security considerations

This repository intentionally has a narrow public interface: only numeric channel paths are accepted.

Do not turn the service into a generic:

```text
/?url=https://anything.example/...
```

proxy.

The current design resolves known channel IDs through the configured resolver and keeps the HLS segment proxy behind your own public hostname.

For stronger access control, add authentication/rate limiting at Nginx or your application layer.

## 16. Monitoring

Check listeners:

```bash
sudo ss -lntp | grep -E ':8080|:8090|:80|:443'
```

Check RAM/CPU:

```bash
free -h
top
```

Check Nginx errors:

```bash
sudo tail -f /var/log/nginx/error.log
```

Check Nginx requests:

```bash
sudo tail -f /var/log/nginx/access.log
```

## 17. Common failures

### `/38.m3u8` returns 502

Check:

```bash
sudo journalctl -u channel-gateway -n 100 --no-pager
```

Then test:

```bash
curl -v http://127.0.0.1:8090/38.m3u8
```

### `.ts` requests fail

Check that `hlsd` is alive:

```bash
sudo systemctl status hls-proxy
```

Then:

```bash
sudo journalctl -u hls-proxy -f
```

### Nginx returns 502

Check:

```bash
sudo tail -n 50 /var/log/nginx/error.log
```

Then verify both local services:

```bash
curl -I http://127.0.0.1:8090/38.m3u8
curl -I http://127.0.0.1:8080/
```

The latter may return `400`; that is still useful because it proves `hlsd` is reachable.

### Stream URL/token expires

This design intentionally resolves the channel again on each playlist refresh instead of storing the old upstream `.m3u8` permanently.

## 18. Updating

Pull application changes:

```bash
cd /opt/hls-channel-proxy
git pull
```

Update dependencies:

```bash
source .venv/bin/activate
pip install -r requirements.txt
npm install
```

Restart:

```bash
sudo systemctl restart hls-proxy
sudo systemctl restart channel-gateway
```

## License

The gateway code in this repository is provided under the MIT license in `LICENSE` if you add one. The upstream Node HLS proxy remains under its own license; see the upstream project's license file before redistributing it.
