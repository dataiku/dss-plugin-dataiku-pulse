from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Iterable, Tuple


def iter_list_methods(obj: Any) -> Iterable[Tuple[str, Callable[..., Any]]]:
    """Yield (name, callable) for list_* methods on `obj`."""

    for name, fn in inspect.getmembers(obj, predicate=callable):
        if name.startswith("list_"):
            yield name, fn


def is_callable_without_required_args(fn: Callable[..., Any]) -> bool:
    """True if `fn` can be called with no args.

    We ignore *args/**kwargs, but if any positional/keyword-only param has no
    default, we treat it as required.
    """

    sig = inspect.signature(fn)
    for p in sig.parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.default is inspect._empty:
            return False
    return True


def get_noarg_list_methods(obj: Any) -> Dict[str, Callable[..., Any]]:
    """Return map of list_* methods that can be called with no args."""

    out: Dict[str, Callable[..., Any]] = {}
    for name, fn in iter_list_methods(obj):
        if is_callable_without_required_args(fn):
            out[name] = fn
    return out
