import tunables
from tunables import errors, validators


def test_the_errors_a_catalogue_raises_are_exported() -> None:
    assert tunables.ConstraintError is errors.ConstraintError
    assert tunables.CatalogueError is errors.CatalogueError


def test_the_validator_helpers_are_exported() -> None:
    assert tunables.describes is validators.describes
    assert tunables.sums_to is validators.sums_to
    assert tunables.descending is validators.descending
    assert tunables.ascending is validators.ascending


def test_service_errors_stay_out_of_the_root() -> None:
    for name in ("ValidationFailed", "VersionConflict", "UnknownVersion", "CatalogueOutOfSync"):
        assert not hasattr(tunables, name)


def test_all_is_sorted_and_every_name_resolves() -> None:
    assert tunables.__all__ == sorted(tunables.__all__)
    assert len(tunables.__all__) == len(set(tunables.__all__))
    for name in tunables.__all__:
        assert hasattr(tunables, name), name


def test_a_catalogue_needs_only_root_imports() -> None:
    from tunables import Catalogue, ConstraintError, Float, Group, Tunable, describes, sums_to

    @describes("Beta must stay under alpha.")
    def beta_under_alpha(values: dict[str, float]) -> None:
        if values["beta"] >= values["alpha"]:
            raise ConstraintError("group", "beta must stay under alpha")

    group = Group(
        "weights",
        [Tunable("alpha", Float(min=0.0, max=1.0), 0.7), Tunable("beta", Float(min=0.0, max=1.0), 0.3)],
        validators=[sums_to(1.0, "alpha", "beta"), beta_under_alpha],
    )
    catalogue = Catalogue([group])
    assert [v.description for v in catalogue.groups["weights"].validators] == [
        "alpha + beta must sum to 1.",
        "Beta must stay under alpha.",
    ]
