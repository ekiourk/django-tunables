"""Print README code blocks tagged with <!-- quickstart: NAME --> so scripts can run the README literally."""

import re
import sys
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"


def block(name: str) -> str:
    text = README.read_text()
    match = re.search(rf"<!-- quickstart: {re.escape(name)} -->\n```[a-z]*\n(.*?)```", text, re.S)
    if match is None:
        sys.exit(f"README.md has no block tagged 'quickstart: {name}'")
    return match.group(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: readme_blocks.py NAME [NAME ...]")
    sys.stdout.write("".join(block(name) for name in sys.argv[1:]))
