"""A stand-in for the Telegram Bot API, for starting the real image in CI
(tests/smoke/start_image.sh): the bot is pointed at it with TELEGRAM_API_URL
and starts as it does in production — getMe accepts the token, the menu calls
succeed, getUpdates long-polls and returns nothing. Standard library only: it
runs in the bot's own image, on a network with no way out.

It accepts exactly one token (argv[1]) and answers 401 to any other, as
Telegram does — the start check proves the token check still decides.

    python telegram_stub.py <token> [port]
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ME = {"id": 123456, "is_bot": True, "first_name": "CI", "username": "ci_smoke_bot",
      "can_join_groups": False, "can_read_all_group_messages": False, "supports_inline_queries": False}
LONG_POLL_SEC = 5          # shorter than the bot's own getUpdates timeout: a stop never waits for it


def make_handler(token: str) -> type[BaseHTTPRequestHandler]:
    prefix = f"/bot{token}/"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _answer(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not self.path.startswith(prefix):
                self._answer(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
                return
            method = self.path[len(prefix):].split("?")[0]
            print(f"stub: {method}", flush=True)                 # the token never reaches the log
            if method == "getMe":
                self._answer(200, {"ok": True, "result": ME})
            elif method == "getUpdates":
                time.sleep(LONG_POLL_SEC)
                self._answer(200, {"ok": True, "result": []})
            else:
                self._answer(200, {"ok": True, "result": True})

        do_GET = do_POST

        def log_message(self, *args) -> None:                    # request lines carry the token
            pass

    return Handler


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    port = int(argv[2]) if len(argv) > 2 else 8081
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(argv[1])).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
