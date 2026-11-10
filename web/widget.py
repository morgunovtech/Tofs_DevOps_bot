"""The feedback widget file: read once, served under its hash, pinned by SRI.

Sites run this file in their own origin. The snippet pins its sha384, so a
file changed on the bot's host is refused by the visitor's browser instead
of running on every site that has the widget."""

import base64
import hashlib
from pathlib import Path

BODY: bytes = (Path(__file__).parent / "feedback-widget.js").read_bytes()
VERSION: str = hashlib.sha256(BODY).hexdigest()[:12]
INTEGRITY: str = "sha384-" + base64.b64encode(hashlib.sha384(BODY).digest()).decode()
