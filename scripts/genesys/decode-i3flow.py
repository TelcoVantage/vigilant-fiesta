#!/usr/bin/env python3
"""
Decode / re-encode an Architect export (.i3InboundFlow, .i3flow, ...) so you
can inspect or diff the underlying JSON.

The file is Base64 of a URL-encoded JSON document (per Genesys dev forum).

  python scripts/genesys/decode-i3flow.py decode flow.i3InboundFlow > flow.json
  python scripts/genesys/decode-i3flow.py encode flow.json > flow.i3InboundFlow

Editing the JSON by hand is NOT supported by Genesys – use this for
inspection and diffing only.
"""
import base64
import json
import sys
import urllib.parse


def decode(path: str) -> str:
    raw = open(path, "rb").read().strip()
    decoded = base64.b64decode(raw)
    text = urllib.parse.unquote(decoded.decode("utf-8"))
    return json.dumps(json.loads(text), indent=2)


def encode(path: str) -> str:
    obj = json.load(open(path, encoding="utf-8"))
    text = json.dumps(obj, separators=(",", ":"))
    return base64.b64encode(urllib.parse.quote(text, safe="").encode("utf-8")).decode("ascii")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("decode", "encode"):
        sys.exit(__doc__)
    sys.stdout.write(decode(sys.argv[2]) if sys.argv[1] == "decode" else encode(sys.argv[2]))
