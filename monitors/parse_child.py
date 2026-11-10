"""Child side of monitors.parse_worker: `python -m monitors.parse_child`.
Reads framed JSON requests on stdin and answers on stdout — nothing else
may write to stdout here (logging goes to stderr). Exits when the bot
closes the pipe. Imports the monitors only: never main.py, the handlers
or aiogram — that is the point of a separate process over multiprocessing."""

import json
import struct
import sys

from monitors.links_checker import extract_links
from monitors.pagemeta import pick_phrase
from monitors.seo_checker import analyze_page

_FRAME = struct.Struct(">I")


def _seo_page(*args, **kwargs) -> tuple[list[dict], int]:
    problems, text_len = analyze_page(*args, **kwargs)
    return [p.as_dict() for p in problems], text_len


OPS = {"seo_page": _seo_page, "links": extract_links, "phrase": pick_phrase}


def serve(stdin=sys.stdin.buffer, stdout=sys.stdout.buffer) -> None:
    while True:
        head = stdin.read(_FRAME.size)
        if len(head) < _FRAME.size:
            return                                   # the bot closed the pipe
        request = json.loads(stdin.read(_FRAME.unpack(head)[0]))
        try:
            reply = {"ok": OPS[request["op"]](*request.get("args", ()), **request.get("kwargs", {}))}
        except Exception as e:                       # a page that breaks the parser
            reply = {"err": f"{type(e).__name__}: {e}"[:300]}
        data = json.dumps(reply, ensure_ascii=False).encode()
        stdout.write(_FRAME.pack(len(data)) + data)
        stdout.flush()


if __name__ == "__main__":
    serve()
