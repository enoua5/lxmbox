"""
Private utils used by the rnmmp_core, do not import from outside
"""

from __future__ import annotations

from types import UnionType
from typing import Any, Union, get_args, get_origin

from .errors import WrongTypeError

type TypeSpec = type | UnionType | tuple[TypeSpec, ...]
"""A type, a union of types, or a (possibly nested) tuple of either."""

type TypeSpecTuple[T] = tuple[type[T] | UnionType | tuple[TypeSpec, ...], ...]
"""A `TypeSpec` tuple, to help MyPy resolve correctly"""


def _strip_type_args(t: TypeSpec) -> tuple[type, ...]:
    """
    Flatten a TypeSpec into a tuple of basic types with no parameters
    """
    if isinstance(t, tuple):
        return tuple(member for element in t for member in _strip_type_args(element))
    origin = get_origin(t)
    if origin is UnionType or origin is Union:
        return tuple(member for argument in get_args(t) for member in _strip_type_args(argument))
    return (origin or t,)  # type: ignore[return-value]


def _describe(t: TypeSpec) -> str:
    """Render a type specification as a human-readable name, for error messages"""
    names = list(dict.fromkeys(member.__name__ for member in _strip_type_args(t)))
    if len(names) == 1:
        return names[0]
    return " | ".join(names)


def _type_matches(t: type, expected: TypeSpec) -> bool:
    """Check if `t` is a subclass of any type `expected` accepts"""
    return issubclass(t, _strip_type_args(expected))


def assert_parameter_type[T](value: Any, expected: TypeSpecTuple[T], *, name: str) -> T:
    """Return `value` if it matches any type in `expected`, else raise `WrongTypeError`."""

    accepted = _strip_type_args(expected)

    if not _type_matches(type(value), accepted):
        raise WrongTypeError(message=f"{name} must be {_describe(accepted)}, got {type(value).__name__}")

    # Special handling of `bool` because Python's type system has `issubclass(bool, int)`
    if isinstance(value, bool) and bool not in accepted:
        raise WrongTypeError(message=f"{name} must be {_describe(accepted)}, got bool")

    return value  # type: ignore[no-any-return]
