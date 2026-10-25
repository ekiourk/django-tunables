"""Drive the demo project's panels the way a browser would: real sessions, CSRF tokens and redirects.

Usage: demo_drive.py http://localhost:8000. Expects a freshly seeded demo database at version 0. Prints one
line per check and exits non-zero when any fails. Standard library only, so it runs in the bare venv that
demo_check.sh creates.
"""

import http.cookiejar
import json
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

    def get(self, path: str) -> tuple[int, str, str]:
        with self.opener.open(urllib.request.Request(self.base + path)) as response:
            return response.status, response.url, response.read().decode()

    def post(self, path: str, data: dict[str, str], headers: dict[str, str] | None = None) -> tuple[int, str, str]:
        body = urllib.parse.urlencode(data).encode()
        request = urllib.request.Request(
            self.base + path, data=body, headers={"Referer": self.base + PAGE, **(headers or {})}, method="POST"
        )
        try:
            with self.opener.open(request) as response:
                return response.status, response.url, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.url or "", error.read().decode()

    def csrf(self) -> str:
        return next(cookie.value or "" for cookie in self.jar if cookie.name == "csrftoken")

    def login(self, username: str) -> "Browser":
        self.get("/login/")
        data = {"username": username, "password": PASSWORD, "csrfmiddlewaretoken": self.csrf(), "next": PAGE}
        _, url, _ = self.post("/login/", data)
        if not url.endswith(PAGE):
            raise SystemExit(f"login of {username} landed on {url}")
        return self


def panel(html: str, element_id: str) -> tuple[str, str]:
    """The mode and inner HTML of the panel section with ``element_id``, or ("", "") when absent."""
    match = re.search(
        rf'<section class="tunables-panel tunables-panel--(\w+)" id="{element_id}">(.*?)</section>', html, re.S
    )
    return (match.group(1), match.group(2)) if match else ("", "")


def value_of(section: str, name: str) -> str:
    match = re.search(rf'name="{re.escape(name)}" value="([^"]*)"', section)
    return match.group(1) if match else ""


def save(browser: Browser, section: str, overrides: dict[str, str], partial: bool = False) -> tuple[int, str, str]:
    """Resubmit a panel's current fields with ``overrides`` applied, as its form would."""
    data = {name: value_of(section, name) for name in ("panel", "next", "expected_version")}
    data["csrfmiddlewaretoken"] = browser.csrf()
    for name in re.findall(r'name="((?:pricing|thermostat|weights)\.[a-z_]+)"', section):
        data[name] = value_of(section, name)
    selected = re.search(r'name="thermostat.mode".*?<option value="(\w+)" selected', section, re.S)
    if selected:
        data["thermostat.mode"] = selected.group(1)
    data.update(overrides)
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

    _, _, html = Browser(base).login("nobody").get(PAGE)
    check("nobody: no panel is rendered", '<section class="tunables-panel' not in html)

    _, _, html = Browser(base).login("viewer").get(PAGE)
    mode, section = panel(html, "prices")
    check("viewer: the prices panel is read-only", mode == "read" and "<form" not in section, mode)

    writer = Browser(base).login("writer")
    _, _, html = writer.get(PAGE)
    check("writer: the prices panel is a form", panel(html, "prices")[0] == "edit")
    check("writer: a panel touching the weights group is read-only", panel(html, "ranking")[0] == "read")
    check("writer: the thermostat panel uses the demo template", "hx-post=" in panel(html, "thermostat")[1])

    section = panel(html, "prices")[1]
    status, _, _ = writer.post(SAVE, {"panel": value_of(section, "panel"), "expected_version": "0", "reason": "x"})
    check("a save without a CSRF token is 403", status == 403, str(status))

    status, _, body = save(writer, section, {"pricing.vat_rate": "0.2", "reason": ""})
    check("an empty required reason shows the error page", status == 200 and "This field is required" in body)
    check("the error page links back to the page", f'href="{PAGE}"' in body)

    _, url, body = save(writer, section, {"pricing.vat_rate": "0.2", "reason": "autumn rate"})
    check("a save redirects to the page with a message", url.endswith(PAGE) and "Saved version 1." in body, url)
    check("the page shows the new value", "VAT is 0.2." in body)
    section = panel(body, "prices")[1]
    check("the override gets a reset box", 'name="reset_pricing.vat_rate"' in section)

    admin = Browser(base).login("admin")
    save(admin, panel(admin.get(PAGE)[2], "prices")[1], {"pricing.free_shipping_over": "60", "reason": "shipping"})
    status, _, body = save(writer, section, {"pricing.vat_rate": "0.19", "reason": "mine"})
    check(
        "a stale save names the key that moved", "version 1 is now version 2" in body and "free_shipping_over" in body
    )
    section = panel(body, "prices")[1]
    check(
        "a stale save keeps the input and bumps the version",
        (value_of(section, "pricing.vat_rate"), value_of(section, "expected_version")) == ("0.19", "2"),
    )
    _, _, body = save(writer, section, {"reason": "mine"})
    check("resubmitting after the conflict saves", "Saved version 3." in body)

    section = panel(writer.get(PAGE)[2], "thermostat")[1]
    status, _, fragment = save(writer, section, {"thermostat.mode": "heat"}, partial=True)
    check(
        "an HTMX save returns the demo template as a fragment",
        "<!doctype" not in fragment.lower() and "hx-post=" in fragment,
    )
    check(
        "the fragment shows the saved version and value",
        "Saved version 4." in fragment and 'value="heat" selected' in fragment,
    )
    _, _, api = writer.get("/api/tunables/changesets/4/")
    changeset = json.loads(api)
    check(
        "a hidden reason is stored as the page",
        (changeset["reason"], changeset["metadata"]) == ("Changed from /shop/settings/", {"page": PAGE}),
    )
    check("the change set's source is panel", changeset["source"] == "panel")

    section = panel(writer.get(PAGE)[2], "thermostat")[1]
    _, _, fragment = save(writer, section, {"thermostat.target_c": "99"}, partial=True)
    check(
        "an invalid HTMX save returns the fragment with the error",
        "tunables-panel__row--error" in fragment and 'value="99"' in fragment,
    )

    status, _, _ = save(writer, panel(admin.get(PAGE)[2], "ranking")[1], {"weights.price": "0.6", "reason": "sneaky"})
    check("a writer may not save a panel over the weights group", status == 403, str(status))
    status, _, _ = save(writer, panel(writer.get(PAGE)[2], "prices")[1], {"panel": "garbage", "reason": "x"})
    check("an altered token is 400", status == 400, str(status))

    sys.stdout.write(f"\n{'all checks passed' if not check.failed else f'{check.failed} check(s) failed'}\n")
    return 1 if check.failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: demo_drive.py http://localhost:8000")
    raise SystemExit(main(sys.argv[1].rstrip("/")))
