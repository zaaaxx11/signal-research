from __future__ import annotations

import pytest

from app.engine.license import LicenseError, LicensePolicy, public_sources
from app.engine.model import LicenseClass, SourceSpec


def source(
    name: str,
    *,
    license_class: LicenseClass,
    redistribution: bool,
    features: frozenset[str] = frozenset({"screen"}),
) -> SourceSpec:
    return SourceSpec(
        name=name,
        license_class=license_class,
        redistribution=redistribution,
        attribution=None,
        terms_url=f"https://{name}.example/terms",
        features=features,
        rate_limit=(1, 5.0),
    )


def test_public_mode_accepts_only_a_public_redistributable_source() -> None:
    policy = LicensePolicy(public_mode=True)
    spec = source("sec", license_class=LicenseClass.PUBLIC, redistribution=True)

    assert policy.require_public(spec) is spec
    assert policy.allows(spec) is True


@pytest.mark.parametrize(
    "license_class,redistribution",
    [
        (LicenseClass.INTERNAL, False),
        (LicenseClass.REFERENCE, True),
        (LicenseClass.PUBLIC, False),
    ],
)
def test_public_mode_rejects_non_redistributable_sources(
    license_class: LicenseClass, redistribution: bool
) -> None:
    policy = LicensePolicy(public_mode=True)
    spec = source("finviz", license_class=license_class, redistribution=redistribution)

    assert policy.allows(spec) is False
    with pytest.raises(LicenseError) as error:
        policy.require_public(spec)
    assert error.value.code == "license_blocked"
    assert spec.name in str(error.value)


def test_private_mode_allows_an_internal_source_for_dev_only() -> None:
    policy = LicensePolicy(public_mode=False)
    spec = source("finviz", license_class=LicenseClass.INTERNAL, redistribution=False)

    assert policy.allows(spec) is True
    assert policy.require_public(spec) is spec


def test_public_sources_filters_and_preserves_order() -> None:
    specs = (
        source("finviz", license_class=LicenseClass.INTERNAL, redistribution=False),
        source("sec", license_class=LicenseClass.PUBLIC, redistribution=True),
        source("vendor", license_class=LicenseClass.PUBLIC, redistribution=True),
        source("oracle", license_class=LicenseClass.REFERENCE, redistribution=True),
    )

    assert [spec.name for spec in public_sources(specs, public_mode=True)] == [
        "sec",
        "vendor",
    ]
    assert [spec.name for spec in public_sources(specs, public_mode=False)] == [
        "finviz",
        "sec",
        "vendor",
        "oracle",
    ]


def test_public_policy_with_no_public_source_raises_actionable_error() -> None:
    policy = LicensePolicy(public_mode=True)
    specs = (
        source("finviz", license_class=LicenseClass.INTERNAL, redistribution=False),
        source("tv", license_class=LicenseClass.INTERNAL, redistribution=False),
    )

    with pytest.raises(LicenseError) as error:
        policy.require_any_public(specs)
    assert error.value.code == "license_unavailable"
    assert "finviz" in str(error.value)
    assert "tv" in str(error.value)


def test_private_policy_any_source_does_not_require_public_licence() -> None:
    policy = LicensePolicy(public_mode=False)
    specs = (
        source("finviz", license_class=LicenseClass.INTERNAL, redistribution=False),
    )

    assert policy.require_any_public(specs)[0].name == "finviz"
