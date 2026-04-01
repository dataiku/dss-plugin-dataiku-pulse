from __future__ import annotations

from typing import Iterator, List, Sequence, TypeVar


T = TypeVar("T")


def chunked(items: Sequence[T], chunk_size: int) -> Iterator[List[T]]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    for i in range(0, len(items), chunk_size):
        yield list(items[i : i + chunk_size])
