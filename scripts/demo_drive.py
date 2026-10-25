"""Drive the demo project's panels the way a browser would: real sessions, CSRF tokens and redirects.

Usage: demo_drive.py http://localhost:8000. Expects a freshly set up demo database at version 0. Prints one
line per check and exits non-zero when any fails. Standard library only, so it runs in the bare venv that
demo_check.sh creates. It checks what only an installed wheel can prove; tests/test_panel.py covers the
panel's behaviour in detail.
"""

import http.cookiejar
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

PAGE = "/shop/settings/"
SAVE = "/tunables/panel/save/"
PASSWORD = "demo"


class Browser:
    """One logged-in session with a cookie jar."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _open(self, request: urllib.request.Request) -> tuple[int, str, str]:
        try:
            with self.opener.open(request) as response:
                return response.status, response.url, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.url or "", error.read().decode()

    def get(self, path: str) -> tuple[int, str, str]:
        return self._open(urllib.request.Request(self.base + path))

    def post(self, path: str, data: dict[str, str], headers: dict[str, str] | None = None) -> tuple[int, str, str]:
        body = urllib.parse.urlencode(data).encode()
        return self._open(urllib.request.Request(self.base + path, data=body, headers=headers or {}, method="POST"))

    def csrf(self) -> str:
        for cookie in self.jar:
            if cookie.name == "csrftoken" and cookie.value:
                return cookie.value
        raise SystemExit("no csrftoken cookie: the server did not set one on the last page")

    def login(self, username: str) -> "Browser":
        self.get("/login/")
        _, url, _ = self.post(
            "/login/", {"username": username, "password": PASSWORD, "csrfmiddlewaretoken": self.csrf()}
        )
        if not url.endswith(PAGE):
            raise SystemExit(f"login of {username} landed on {url}")
        return self

    def section(self, element_id: str) -> tuple[str, str]:
        return panel(self.get(PAGE)[2], element_id)


def panel(html: str, element_id: str) -> tuple[str, str]:
    """The mode and inner HTML of the panel section with ``element_id``, or ("", "") when absent."""
    match = re.search(
        rf'<section class="tunables-panel tunables-panel--(\w+)" id="{element_id}">(.*?)</section>', html, re.S
    )
    return (match.group(1), match.group(2)) if match else ("", "")


def inputs(section: str) -> dict[str, str]:
    """Every input of a panel section that carries a value, plus the selected mode."""
    data = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', section))
    selected = re.search(r'name="thermostat.mode".*?<option value="(\w+)" selected', section, re.S)
    if selected:
        data["thermostat.mode"] = selected.group(1)
    return data


def save(browser: Browser, section: str, overrides: dict[str, str], partial: bool = False) -> tuple[int, str, str]:
    """Resubmit a panel's current inputs with ``overrides`` applied, as its form would."""
    data = {**inputs(section), **overrides, "csrfmiddlewaretoken": browser.csrf()}
    return browser.post(SAVE, data, {"HX-Request": "true"} if partial else None)


class Checks:
    def __init__(self) -> None:
        self.failed = 0

    def __call__(self, name: str, ok: bool, detail: str = "") -> None:
        self.failed += not ok
        sys.stdout.write(f"{'PASS' if ok else 'FAIL'} {name}{f'  [{detail}]' if detail and not ok else ''}\n")


def main(base: str) -> int:
    check = Checks()

    _, _, html = Browser(base).get(PAGE)
    check("anonymous: tunable_value prints the value", "Orders over 50.0 EUR ship free" in html)
    check("anonymous: no panel is rendered", '<section class="tunables-panel' not in html)

    writer = Browser(base).login("writer")
    _, _, html = writer.get(PAGE)
    check("writer: the prices panel is a form", panel(html, "prices")[0] == "edit")
    check("writer: a panel touching the weights group is read-only", panel(html, "ranking")[0] == "read")
    check("writer: the extending template renders with its HTMX attributes", "hx-post=" in panel(html, "thermostat")[1])

    section = panel(html, "prices")[1]
    without_token = {name: value for name, value in inputs(section).items() if name != "csrfmiddlewaretoken"}
    status, _, _ = writer.post(SAVE, without_token)
    check("a save without a CSRF token is 403", status == 403, str(status))

    status, _, body = save(writer, section, {"pricing.vat_rate": "0.2", "reason": ""})
    check(
        "an empty required reason answers 200 with the standalone page", status == 200 and "<!doctype" in body.lower()
    )
    check("the standalone page links back to the host page", f'href="{PAGE}"' in body)

    _, url, body = save(writer, section, {"pricing.vat_rate": "0.2", "reason": "autumn rate"})
    check("a save redirects back to the host page", url.endswith(PAGE), url)
    check("the host page shows the saved message", "Saved version 1." in body)
    check("the page shows the new value", "VAT is 0.2." in body)

    _, _, fragment = save(writer, panel(body, "thermostat")[1], {"thermostat.mode": "heat"}, partial=True)
    check("an HTMX save returns a fragment, not a page", "<!doctype" not in fragment.lower())
    check("the fragment is rendered from the demo's template", "hx-post=" in fragment)
    check("the fragment shows the saved version", "Saved version 2." in fragment)
    check("the fragment shows the new value selected", 'value="heat" selected' in fragment)

    admin_ranking = Browser(base).login("admin").section("ranking")[1]
    status, _, _ = save(writer, admin_ranking, {"weights.price": "0.6", "reason": "sneaky"})
    check("a writer may not save a panel over the weights group", status == 403, str(status))

    sys.stdout.write(f"\n{'all checks passed' if not check.failed else f'{check.failed} check(s) failed'}\n")
    return 1 if check.failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: demo_drive.py http://localhost:8000")
    raise SystemExit(main(sys.argv[1].rstrip("/")))
