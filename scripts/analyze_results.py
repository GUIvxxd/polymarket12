from __future__ import annotations

import sys

from polybot.main import main


if __name__ == "__main__":
    raise SystemExit(main(["analyze", *sys.argv[1:]]))

