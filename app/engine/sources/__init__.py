"""Adapters from upstream documents into engine value objects.

The package is the only place where a provider's JSON shape is known. A parser
here turns one document into :class:`~app.engine.model.SecurityRecord` values
and performs no I/O, so the same function can be fed a live body, a fixture or
a recorded probe.

The SEC slice is the first adapter: :mod:`app.engine.sources.sec` reads the
registrant index and a submissions body. A source description
(:data:`~app.engine.sources.sec.SEC_SOURCE`) travels with it so licence and
rate-limit facts are declared next to the parser that relies on them.
"""

from __future__ import annotations

from app.engine.sources.sec import SEC_SOURCE

__all__ = ["SEC_SOURCE"]
