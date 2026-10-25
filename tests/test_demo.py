"""The demo project: its catalogue matches the README, and its access hook and setup command do what its README says."""

import io
import re
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory

from demo.access import editable_groups
from demo.management.commands.demo_setup import Command as DemoSetup
from tests.conftest import user_with

ROOT = Path(__file__).resolve().parent.parent


def readme_block(name: str) -> str:
    match = re.search(rf"<!-- quickstart: {name} -->\n```[a-z]*\n(.*?)```", (ROOT / "README.md").read_text(), re.S)
    assert match is not None, f"README.md has no block tagged 'quickstart: {name}'"
    return match.group(1)


def readme_group(name: str) -> str:
    """The source of one ``name = Group(...)`` statement in the README quickstart catalogue."""
    match = re.search(rf"^{name} = Group\(.*?^\)\n", readme_block("catalogue"), re.S | re.M)
    assert match is not None, f"README catalogue has no {name} group"
    return match.group(0)


@pytest.mark.parametrize("group", ["pricing", "thermostat"])
def test_the_demo_catalogue_carries_the_readme_quickstart_group_verbatim(group: str) -> None:
    assert readme_group(group) in (ROOT / "demo" / "catalogue.py").read_text()


# demo.access, run against the test catalogue the suite registers


def request_as(user: User) -> object:
    request = RequestFactory().get("/")
    request.user = user
    return request


def test_a_superuser_may_edit_every_group() -> None:
    assert editable_groups(request_as(User(is_superuser=True))) is None


def test_anyone_else_may_edit_every_group_but_weights() -> None:
    # The test catalogue declares pricing, thermostat, weights and limits.
    assert editable_groups(request_as(User())) == {"pricing", "thermostat", "limits"}


# demo_setup


def run_setup() -> str:
    out = io.StringIO()
    DemoSetup(stdout=out).handle()
    return out.getvalue()


def test_setup_creates_the_four_demo_users(db: None) -> None:
    run_setup()
    assert sorted(User.objects.values_list("username", flat=True)) == ["admin", "nobody", "viewer", "writer"]


def test_setup_makes_admin_a_superuser_and_writer_a_plain_user_with_two_permissions(db: None) -> None:
    run_setup()
    assert User.objects.get(username="admin").is_superuser
    writer = User.objects.get(username="writer")
    assert not writer.is_superuser
    assert writer.has_perm("tunables.add_changeset") and writer.has_perm("tunables.view_tunabledefinition")
    assert not writer.has_perm("tunables.add_tag")


def test_setup_run_twice_leaves_existing_users_alone(db: None) -> None:
    user_with(username="writer")
    output = run_setup()
    assert "writer: exists" in output
    assert User.objects.filter(username="writer").count() == 1
