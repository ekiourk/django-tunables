"""The demo project stays in step with the documentation it demonstrates."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def readme_block(name: str) -> str:
    match = re.search(rf"<!-- quickstart: {name} -->\n```[a-z]*\n(.*?)```", (ROOT / "README.md").read_text(), re.S)
    assert match is not None, f"README.md has no block tagged 'quickstart: {name}'"
    return match.group(1)


def test_the_demo_catalogue_carries_the_readme_quickstart_groups_verbatim() -> None:
    demo = (ROOT / "demo" / "catalogue.py").read_text()
    for group in ("pricing", "thermostat"):
        match = re.search(rf"^{group} = Group\(.*?^\)\n", readme_block("catalogue"), re.S | re.M)
        assert match is not None, f"README catalogue has no {group} group"
        assert match.group(0) in demo, f"demo/catalogue.py's {group} group differs from the README's"
