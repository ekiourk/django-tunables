# Django admin

The package registers three pages in the Django admin under the Tunables app: the
group index with its edit forms, the change set history, and the snapshots. Nothing is
required beyond `django.contrib.admin` and its usual dependencies, which the host
already has if it uses the admin at all.

## Group index

`Tunable definitions` in the admin menu opens the group index at
`admin/tunables/tunabledefinition/` instead of a changelist. It lists every group under
its category, categories and groups in catalogue order, with each group's title,
description, number of tunables, and the descriptions of its validators. The rules that
span groups follow the last category. Each row has an Edit link when the user may write
and the database is in sync with the code. A link at the top opens the definitions page.

When the catalogue in code differs from the database, the index shows a warning asking
for `tunables_sync`, the Edit links disappear, and the edit page redirects back here
with the same message.

When the stored values break a group or catalogue rule, which happens when a rule is
added or tightened in code after the values were set, the index shows the broken rules
in a red note above the table. Nothing is changed automatically; someone has to set
values that satisfy the rule.

## Browsing definitions

`admin/tunables/tunabledefinition/definitions/` lists every tunable with its key, title,
category, group, type and tags. Selects narrow the list by category, group and tag, and a
search box matches the key, title or description. The same matching serves the API's
`definitions/` filters. Users who may write see an "Edit tags" link per row, limited by
`EDITABLE_GROUPS` to the groups they may change.

## Tags

The tags page of one definition shows the tags that come from the catalogue, which only
the code can change, and one field with the manual tags as comma-separated names. Saving
replaces the manual tags: names that do not exist yet become new tags. Tag edits change
no value and create no change set.

`Tags` in the admin menu lists every tag with the number of definitions carrying it.
Tags can be created and their descriptions edited by hand; the flag that marks a tag as
coming from the catalogue is read only, and so is the name of a tag the catalogue seeds,
since the code owns that name. A seeded tag cannot be deleted, and there is no bulk
delete action. Deleting a manual tag removes its assignments. Tag edits made through the
API leave no record; edits made in the admin appear in Django's admin log.

## Editing a group

`admin/tunables/tunabledefinition/edit/<group>/` shows one form for the whole group:

- One field per tunable, built from the type's Django form field. The label is the
  tunable's title, the help text is its description followed by the unit in parentheses,
  and a deprecated tunable shows its reason below the field.
- The field starts at the current effective value, whether that is the default or an
  override.
- A "Reset to default" box next to every tunable that currently has an override, naming
  the default it would restore, for example "Reset to default (0.24)" or "Reset to
  default (30.0 s)". Ticking it removes the override whatever the field says. The value
  is shown as JSON, so a list reads `["EUR"]`. A default longer than 40 characters is
  cut in the label and shown in full under the field.
- A required Reason, stored on the change set.
- A hidden version number, used to detect concurrent edits.

Saving sends every field through the same validation as the API. Only tunables whose
value differs from the current one end up in the change set, and submitting an untouched
form is refused with "Nothing changed." On success the admin returns to the index with a
message naming the new version. The change
set records `source: admin` and the logged-in user's username as a verified actor.

Errors appear where you would expect: a bad value on its field, a group or catalogue
validator's message at the top of the form, and "Enter a value or tick reset to default." on a
field that was emptied without its reset box.

Cross-group rules, the catalogue validators, are enforced on every save, the same as in
the API. Because the admin saves one group per change set, a rule that can only be
satisfied by changing two groups at once cannot be met from the admin: the first save
fails the rule. Such a change goes through `POST changesets/`, which accepts keys from
any number of groups in one change set.

If someone else saved the group while the form was open, the save is refused. The page
reloads with the new values and a message naming the version you started from and the
version that exists now. Your edits are not kept, so re-enter them and save again.

## Change sets

`admin/tunables/changeset/` lists every change set, newest first, with its version,
time, actor, source, reason, and number of items. The list filters by source and by how
the actor was identified, and searches reason, actor, and item keys. Opening a change
set shows its fields and, below them, one row per item with the key, the value before,
the value after, and whether the item was a reset. Everything is read only.

## Rolling back

Select one change set in the list and choose "Roll back to this version" from the
actions menu. A confirmation page explains what will happen and asks for a reason.
Confirming creates a new change set with `source: rollback` that restores every
override to what it was at that version. The history itself is untouched; a rollback is
one more version on top.

Selecting more than one change set is refused with a message. If the values already
match the chosen version the admin says so and creates nothing. If the rollback fails
validation, for example because a stored value no longer fits a changed type, the
message carries the reason.

## Snapshots

`admin/tunables/snapshot/` lists every snapshot with its version, time, catalogue
version, and the change set that produced it. Opening one shows the snapshot document
formatted for reading. See `snapshot-format.md` for what the document contains.

## Language

Every label, message and heading the admin pages add goes through Django's translation
machinery, so they follow the active language like the rest of the admin. The package
ships no translation files; a host or contributor adds a language with `makemessages`
against the package's source and templates. Constraint messages shown on the form come
from the same strings the API returns as `detail`.

## Permissions

The admin uses Django's standard permissions with one addition:

| To | The user needs |
|---|---|
| see the group index and the definitions page | `tunables.view_tunabledefinition` |
| open the edit form and save | `tunables.add_changeset` |
| attach an existing tag to a definition | `tunables.change_tag` |
| name a tag that does not exist yet | also `tunables.add_tag` |
| see, create, edit and delete tags in the tag admin | Django's `view`, `add`, `change` and `delete` permissions on `Tag` |
| see the change set history | `tunables.view_changeset` |
| roll back | `tunables.view_changeset` and `tunables.add_changeset` |
| see snapshots | `tunables.view_snapshot` |

Every value write in the admin creates a change set, which is why `add_changeset` is
the write permission for values. Tags are metadata and create no change set, so they
take the `Tag` permissions instead. Tag edits in the admin record the logged-in user on
the assignment row and write a line to the `tunables.tags` logger. Someone can label tunables without being able to
change them, and the other way round. `TUNABLES["EDITABLE_GROUPS"]` narrows it further, with the same
callable the API uses: the index shows an Edit link only for groups the callable
returns, the edit view refuses the others, and the rollback action refuses a rollback
that would change a group outside the set, naming the group. The callable receives the
admin's `HttpRequest`, whose `user` is the logged-in user. The admin grants no add, change or delete permission on the three
models themselves, so their detail pages are read only for everyone, superusers
included. Users without `add_changeset` see the index without Edit links and the
history without the rollback action.
