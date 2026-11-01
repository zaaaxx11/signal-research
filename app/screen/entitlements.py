"""Which filter features are free and which need a plan — one editable table.

The old refusals said *Elite-only*. That was a **tier**, not a missing metric:
the after-hours close, the after-hours change, the news keyword search, the ETF
held-by list and the trade count are all things the source panel charges for.
Conflating the two made the product refuse work it could do. So the two facts
are now separate:

* **availability** — can this deployment answer the question at all? That is
  :data:`app.screen.filter_fields.UNAVAILABLE`, the bucket registry's
  ``available`` flag and the snapshot modules' own reasons;
* **tier** — may *this caller* ask it? That is this module, and it is **data**:
  ``app/screen/data/entitlements.json`` maps a feature id to ``free`` or
  ``premium``. Flipping a feature is an edit to that file, never a code change,
  and :func:`tier_for` re-reads it (mtime-cached) so no restart is needed.

Two vocabularies, on purpose
----------------------------
A **tier** is a property of a feature (``premium``). A **plan** is a property of
a caller (``free``, ``premium``). The request's plan comes from
:func:`resolve_plan`:

1. ``X-Api-Key`` — the fingerprint of the presented key is looked up in the
   config's ``api_keys`` map. A **recognised** key decides the plan and a header
   cannot upgrade it. A key that identifies no plan grants nothing either: the
   request falls through to the declaration, exactly as an anonymous one would.
2. ``X-Plan`` — an explicit declaration, honoured when the key (if any) is not
   recognised. There is no billing system behind it yet, so it is a
   *declaration*, not a proof; a deployment that bills sets it at the gateway and
   strips a client's copy.
3. otherwise ``free`` — the default for an anonymous request.

The body may also carry ``criteria.entitlement``: the tier the request asks to
be judged at. It can only **narrow** the plan (a premium caller may ask for the
free view); naming a tier above the plan is a 403 ``premium_required``, never a
silent downgrade.

Why a refused-for-tier feature still gets a reason
--------------------------------------------------
``sh_trades`` is gated *and* sourceless: no trade-count feed exists, FINRA
publishes short-sale volume. Its tier lives here like every other feature, and
its refusal lives in :mod:`app.screen.filter_fields` — tier and availability are
reported side by side in the document, never as one word.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from app.auth import DEFAULT_PLAN, fingerprint

#: The two tiers, ascending. ``premium`` is a superset of ``free``: every
#: feature is answerable to a premium caller, a free caller only sees the free
#: ones. The order is the comparison :func:`allows` and :func:`narrow` use.
FREE = "free"
PREMIUM = "premium"
TIERS: tuple[str, ...] = (FREE, PREMIUM)

#: The features a tier can be assigned to: the five controls the source panel
#: gates behind its paid tier (``sh_trades``, ``ah_close``, ``ah_change``,
#: ``news_keywords``, ``etf_heldby``). The id **is** the catalogue key the
#: request names, so the gate reads the same spelling the filter does — no
#: second vocabulary. A key outside this tuple is not gateable: its tier is the
#: config's default, and :func:`_validate` fails the import if the config tries
#: to gate something else.
FEATURES: tuple[str, ...] = (
    "ah_close",
    "ah_change",
    "news_keywords",
    "etf_heldby",
    "sh_trades",
)

#: Where the table lives. It ships inside ``app.screen``'s ``data/*.json``
#: (``pyproject.toml``), so an installed wheel carries it and a deployment can
#: edit it in place.
CONFIG_PATH = Path(__file__).with_name("data") / "entitlements.json"

#: A deployment that keeps its table elsewhere names it here. The path is read
#: on every :func:`load`, so a test (or an operator) can move it without a
#: restart and without a module reload.
CONFIG_ENV = "ZTOCKZ_ENTITLEMENTS"

#: The header a caller declares a plan with; the header a key is presented in.
PLAN_HEADER = "x-plan"
API_KEY_HEADER = "x-api-key"


class ConfigError(RuntimeError):
    """The entitlements table cannot be read as one. A boot error, not a 422."""


@dataclass(frozen=True, slots=True)
class Config:
    """One parsed table: the default tier, the feature map and the key map."""

    default: str
    features: Mapping[str, str]
    api_keys: Mapping[str, str]
    path: Path | None

    def tier(self, feature: str) -> str:
        """The configured tier of ``feature``; an unlisted feature is the default.

        Unlisted is **not** an error: the default is the owner's floor (today
        ``free``), so a new control is answerable until it is gated. The key
        still has to be a real feature id — :func:`_validate` refuses a table
        that gates a key nobody can request.
        """

        return self.features.get(feature, self.default)

    def as_dict(self) -> dict[str, Any]:
        return {
            "default": self.default,
            "features": dict(self.features),
            "api_keys": len(self.api_keys),
            "path": None if self.path is None else str(self.path),
        }


def parse(document: object, *, path: Path | None = None) -> Config:
    """One JSON object → :class:`Config`, or :class:`ConfigError`.

    Validated here rather than at the call site because a typo in this file is
    a *boot* problem: a feature gated as ``Premuim`` would silently fall back to
    free (or to premium) and the document would publish a tier no request can
    match.
    """

    if not isinstance(document, Mapping):
        raise ConfigError("entitlements must be a JSON object")
    default = document.get("default", FREE)
    if default not in TIERS:
        raise ConfigError(
            f"entitlements 'default' must be one of {', '.join(TIERS)}, got {default!r}"
        )
    raw_features = document.get("features", {})
    if not isinstance(raw_features, Mapping):
        raise ConfigError("entitlements 'features' must be an object of feature → tier")
    features: dict[str, str] = {}
    for feature, tier in raw_features.items():
        name = str(feature)
        if name not in FEATURES:
            raise ConfigError(
                f"entitlements gates {name!r}, which is not one of the gateable "
                f"features ({', '.join(FEATURES)})"
            )
        if tier not in TIERS:
            raise ConfigError(
                f"entitlements gives {name!r} the tier {tier!r}; the tiers are "
                f"{', '.join(TIERS)}"
            )
        features[name] = str(tier)
    raw_keys = document.get("api_keys", {})
    if not isinstance(raw_keys, Mapping):
        raise ConfigError("entitlements 'api_keys' must be an object of fingerprint → plan")
    keys: dict[str, str] = {}
    for raw, plan in raw_keys.items():
        token = str(raw).strip()
        if not token.startswith("sha256:"):
            raise ConfigError(
                f"entitlements api_keys holds {token[:12]!r}…: keys are stored as "
                "'sha256:<hex>' fingerprints, never as the key itself"
            )
        if plan not in TIERS:
            raise ConfigError(
                f"entitlements gives key {token[:17]}… the plan {plan!r}; the plans "
                f"are {', '.join(TIERS)}"
            )
        keys[token] = str(plan)
    return Config(
        default=str(default),
        features=MappingProxyType(features),
        api_keys=MappingProxyType(keys),
        path=path,
    )


def _validate() -> None:
    """Fail at import: the shipped table parses, and it gates only real features."""

    path = configured_path()
    if not path.exists():
        raise ConfigError(
            f"the entitlements table is missing ({path}); every feature would fall "
            "back to the default tier, which is a policy nobody wrote"
        )


#: path (str) → (mtime_ns, size, Config). A read is one ``stat`` per call, which
#: is what makes "flip it in the file" a live edit instead of a restart.
_CACHE: dict[str, tuple[int, int, Config]] = {}


def configured_path() -> Path:
    """The table to read: ``ZTOCKZ_ENTITLEMENTS`` if set, else the shipped file."""

    override = os.environ.get(CONFIG_ENV, "").strip()
    return Path(override) if override else CONFIG_PATH


def load(path: Path | str | None = None) -> Config:
    """The table, re-read when the file's stamp moves; cached between reads.

    A missing file is **not** a silent fallback: the import gate refuses a build
    without it, and :func:`tier_for` raises :class:`ConfigError` rather than
    inventing a policy at request time.
    """

    target = Path(path) if path is not None else configured_path()
    try:
        stat = target.stat()
    except OSError as exc:
        raise ConfigError(f"cannot read the entitlements table {target}: {exc}") from exc
    cached = _CACHE.get(str(target))
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot parse the entitlements table {target}: {exc}") from exc
    config = parse(document, path=target)
    _CACHE[str(target)] = (stat.st_mtime_ns, stat.st_size, config)
    return config


def clear() -> None:
    """Forget every cached read (a test seam; also what a reload CLI calls)."""

    _CACHE.clear()


def tier_for(feature: str) -> str:
    """The configured tier of one feature id, live from the table."""

    return load().tier(feature)


def default_tier() -> str:
    """The tier of a control nobody gated: the table's ``default``."""

    return load().default


def feature_tiers() -> Mapping[str, str]:
    """Every gateable feature with its resolved tier, in :data:`FEATURES` order."""

    config = load()
    return MappingProxyType({feature: config.tier(feature) for feature in FEATURES})


def rank(tier: str) -> int:
    """``free`` → 0, ``premium`` → 1. An unknown word is a :class:`ConfigError`."""

    try:
        return TIERS.index(tier)
    except ValueError:
        raise ConfigError(
            f"unknown tier {tier!r}; the tiers are {', '.join(TIERS)}"
        ) from None


def allows(plan: str, tier: str) -> bool:
    """May ``plan`` ask a feature whose tier is ``tier``?"""

    return rank(plan) >= rank(tier)


def narrow(plan: str, declared: str | None) -> str:
    """The tier a request is judged at: the plan, or the narrower declaration.

    A request cannot widen its own access by asking for a higher tier — that is
    :func:`overreach`'s job to report — so this only ever lowers.
    """

    if declared is None:
        return plan
    return declared if rank(declared) < rank(plan) else plan


def overreach(plan: str, declared: str | None) -> bool:
    """Does the request declare a tier above the caller's plan?"""

    return declared is not None and rank(declared) > rank(plan)


def blocked(features: set[str] | frozenset[str], entitlement: str) -> tuple[str, ...]:
    """The requested features this entitlement cannot ask, sorted.

    A feature whose tier the caller's entitlement does not reach is **blocked**;
    the caller answers 403 ``premium_required`` naming these ids. A feature that
    is unlisted resolves to the config's default, so flipping the default is one
    edit for the whole panel.
    """

    config = load()
    return tuple(
        sorted(
            feature
            for feature in features
            if not allows(entitlement, config.tier(feature))
        )
    )


def resolve_plan(headers: Mapping[str, str] | None) -> str:
    """The caller's plan from the request headers: key first, then declaration.

    Precedence, and why:

    1. a **recognised** ``X-Api-Key`` (its sha256 is in the config) decides the
       plan, and a header cannot upgrade it — that is the one case where a
       declaration must lose;
    2. an **unrecognised** key identifies no plan. It is neither a refusal nor an
       upgrade: the request falls through to the declaration exactly as an
       anonymous one would. (Every request this product's own clients send
       carries a key, so refusing to fall through would make ``X-Plan``
       unusable — and it would grant nothing, since the header grants nothing on
       its own until billing sits in front of it.)
    3. ``X-Plan`` naming one of :data:`TIERS`;
    4. otherwise :data:`app.auth.DEFAULT_PLAN`.

    Nothing here raises: an unknown ``X-Plan`` word is not a plan, and the
    premium filter it asks for still 403s — no silent grant.
    """

    key = _header(headers, API_KEY_HEADER)
    if key:
        found = load().api_keys.get(fingerprint(key))
        if found is not None:
            return found
    declared = _header(headers, PLAN_HEADER)
    if declared:
        token = declared.strip().casefold()
        if token in TIERS:
            return token
    return DEFAULT_PLAN


def _header(headers: Mapping[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    for candidate, value in headers.items():
        if str(candidate).casefold() == name:
            text = str(value).strip()
            return text or None
    return None


def _label(path: Path | None) -> str | None:
    """The config's name as a client should read it — never a host path.

    A public document must not publish ``/home/<operator>/...``: the file's
    identity is its path inside the package (``app/screen/data/entitlements
    .json``), which is the same string in a checkout and in a wheel.
    """

    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(Path(__file__).resolve().parents[2]))
    except ValueError:
        return path.name


def config_label() -> str:
    """The short name of the table in force (for a document and a 403 message)."""

    return _label(configured_path()) or CONFIG_PATH.name


def describe() -> dict[str, Any]:
    """The policy block the filter document publishes.

    A client must be able to say *why* a control is greyed out without guessing:
    the tier of every feature, the plans, the two headers that name one and the
    request field that narrows it all travel with the panel.
    """

    config = load()
    return {
        "config": _label(config.path),
        "default": config.default,
        "features": dict(feature_tiers()),
        "plans": list(TIERS),
        "plan_header": PLAN_HEADER,
        "api_key_header": API_KEY_HEADER,
        "request": "criteria.entitlement",
        "anonymous_plan": DEFAULT_PLAN,
        "keys_configured": len(config.api_keys),
    }


_validate()


__all__ = [
    "API_KEY_HEADER",
    "CONFIG_ENV",
    "CONFIG_PATH",
    "Config",
    "ConfigError",
    "FEATURES",
    "FREE",
    "PLAN_HEADER",
    "PREMIUM",
    "TIERS",
    "allows",
    "blocked",
    "clear",
    "config_label",
    "configured_path",
    "default_tier",
    "describe",
    "feature_tiers",
    "load",
    "narrow",
    "overreach",
    "parse",
    "rank",
    "resolve_plan",
    "tier_for",
]
