from __future__ import annotations

import argparse
import hashlib
import urllib.request
from pathlib import Path

API_URL = "https://models.dev/api.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download models.dev JSON and print its SHA256 digest.",
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--source", default=API_URL)
    args = parser.parse_args()

    request = urllib.request.Request(
        args.source,
        headers={"User-Agent": "modelsdotdev-python-publish"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print(hashlib.sha256(payload).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
