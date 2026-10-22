"""Row streaming in bounded chunks (02 §3.1, §3.5 gate 6).

The gate is structural, not a memory estimate: ``tests/_phase3.py`` feeds an
iterator-only source that counts every pull. While the consumer holds batch *k*,
the source may have been pulled at most ``k * chunk_size`` times, so a
``list(rows)`` implementation shows 5.000 pulls on the first batch and fails
immediately. A ``tracemalloc`` ceiling backs the same assertion up.

``count`` is the number of rows yielded by the most recent iteration. It is
measured, never predicted: a stream that has to know its length up front is a
list with extra steps.
"""

from __future__ import annotations

from itertools import islice
from typing import Any, Iterable, Iterator, Sequence

#: 02 §5 Fase 3 gate 6: 5.000 instruments, 100-row chunks.
DEFAULT_CHUNK_SIZE = 100


class RowStream:
    """Iterate a row source in ``list`` chunks of at most ``chunk_size`` rows.

    ``rows`` only has to be iterable — no ``__len__``, no ``__getitem__`` — so a
    JSONL reader, a cursor and a generator are all valid sources. Nothing is read
    beyond the chunk being handed out.
    """

    def __init__(self, rows: Iterable[Any], chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        if not isinstance(chunk_size, int) or isinstance(chunk_size, bool):
            raise ValueError(f"chunk_size must be an int, got {chunk_size!r}")
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be > 0, got {chunk_size}")
        self._rows = rows
        self.chunk_size = chunk_size
        #: Rows yielded by the most recent (or in-flight) iteration.
        self.count = 0

    def __iter__(self) -> Iterator[list[Any]]:
        self.count = 0
        iterator = iter(self._rows)
        while True:
            chunk = list(islice(iterator, self.chunk_size))
            if not chunk:
                return
            self.count += len(chunk)
            yield chunk

    def iter_rows(self) -> Iterator[Any]:
        """Flatten the stream back to rows without materialising it.

        Used by consumers that write one row at a time (the export writer):
        they keep the chunking contract while never holding more than one chunk.
        """

        for chunk in self:
            yield from chunk

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"RowStream(chunk_size={self.chunk_size}, count={self.count})"


def chunks(rows: Iterable[Any], chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[list[Any]]:
    """Convenience wrapper: ``chunks(rows, n)`` is ``iter(RowStream(rows, n))``."""

    return iter(RowStream(rows, chunk_size))


__all__ = ["DEFAULT_CHUNK_SIZE", "RowStream", "chunks"]
