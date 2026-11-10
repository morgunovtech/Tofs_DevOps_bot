"""Docker HEALTHCHECK: the bot's own /health on localhost. The port is read
the way config.py reads it, and no proxy from the environment is followed
(urllib honours http_proxy; the bot's server is local)."""

import os
import sys
import urllib.request


def main() -> int:
    raw = os.environ.get("WEBHOOK_PORT", "").strip()
    port = raw if raw.isascii() and raw.isdigit() else "8080"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=4) as resp:
            return 0 if resp.status == 200 else 1
    except Exception:
        return 1      # refused, 503 (stale) or timed out — all the same: not healthy


if __name__ == "__main__":
    sys.exit(main())
