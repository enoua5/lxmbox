"""
The Exchange types: Request, Response and Notification, and their encoding.

Every rnmmp Exchange is a msgpack array whose first item says what it is. The rest of the array
depends on that type::

    Request       [0, request id, request type, keyed parameters?, *positional parameters]
    Response      [1, request id, status, *return parameters]
    Notification  [2, event type, *parameters]

The specification's forward-compatibility rules are implemented here:

* An Exchange whose type is not recognised decodes to `UnknownExchange` instead of raising.
* Unrecognised request types and event types are kept as plain integers.
* Unexpected additional parameters and unrecognised Keyed Parameter keys are preserved.

A missing required parameter is rejected. Everything raised here is an `RnmmpError` carrying the
right status and general error code, ready for `Response.failure(request_id, exc)`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, overload, override

from ._utils import TypeSpec, TypeSpecTuple, assert_parameter_type
from .codes import ErrorInfoKey, ExchangeType, NotificationType, RequestType, ResponseStatus
from .errors import _SPECIFIC_CODE_FOR, IncompleteRequestError, MalformedExchangeError, RnmmpError
from .msgpack import pack, unpack

__all__ = [
    "Exchange",
    "Request",
    "Response",
    "Notification",
    "UnknownExchange",
]


def _assert_int(value: Any, name: str) -> int:
    """Return `value` if it's an int, or raise `MalformedExchangeError` otherwise"""
    # `bool` subclasses `int` in Python
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise MalformedExchangeError(message=f"{name} must be an integer (got {type(value).__name__})")


class Exchange(ABC):
    """Any decoded Exchange"""

    __slots__ = ()

    @property
    @abstractmethod
    def exchange_type(self) -> int | ExchangeType:
        """The Exchange Type this Exchange is packed under"""

    @abstractmethod
    def to_array(self) -> list[Any]:
        """Convert to the array that will be packed for transport"""

    def encode(self) -> bytes:
        """Convert to the bytes sent over the transport"""
        return pack(self.to_array())

    @classmethod
    def from_array(cls, array: Any) -> Exchange:
        """
        Parse a decoded msgpack array into an Exchange

        Raises:
            MalformedExchangeError: if this is not an Exchange array, or is missing basic parameters
        """
        if not isinstance(array, Sequence) or isinstance(array, str | bytes) or not array:
            raise MalformedExchangeError(message="an Exchange must be a non-empty msgpack array")

        exchange_type = _assert_int(array[0], "Exchange type")
        rest = list(array[1:])

        match exchange_type:
            case ExchangeType.REQUEST:
                if len(rest) < 2:
                    raise MalformedExchangeError(message="A Request must carry a Request id and a request type")
                request_id = _assert_int(rest[0], "Request id")
                request_type = _assert_int(rest[1], "request type")
                keyed_parameters: dict[Any, Any] = {}
                positional_parameters: list[Any] = []
                if len(rest) > 2:
                    if not isinstance(rest[2], Mapping):
                        raise MalformedExchangeError(
                            message=(
                                f"The first additional parameter of a Request must be the Keyed Parameter map, "
                                f"got {type(rest[2]).__name__}"
                            )
                        )
                    keyed_parameters = dict(rest[2])
                    positional_parameters = rest[3:]
                return Request(request_id, request_type, keyed_parameters, positional_parameters)

            case ExchangeType.RESPONSE:
                if len(rest) < 2:
                    raise MalformedExchangeError(message="A Response must carry a Request id and a status")
                request_id = _assert_int(rest[0], "Request id")
                status = _assert_int(rest[1], "status")
                parameters = rest[2:]
                return Response(request_id, status, parameters)

            case ExchangeType.NOTIFICATION:
                if not rest:
                    raise MalformedExchangeError(message="a Notification must carry an event type")
                event_type = _assert_int(rest[0], "event type")
                parameters = rest[1:]
                return Notification(event_type, parameters)

            case _:
                return UnknownExchange(exchange_type, rest)

    @classmethod
    def decode(cls, data: bytes) -> Exchange:
        """
        Decode an Exchange from the bytes carried over the transport

        Raises:
            MalformedExchangeError: if `data` is not a well-formed Exchange
        """
        return cls.from_array(unpack(data))


@dataclass(slots=True)
class Request(Exchange):
    """An Exchange for which a Response is expected"""

    exchange_type: ClassVar[ExchangeType] = ExchangeType.REQUEST

    request_id: int
    """A client-supplied request count to help match async requests with their responses"""

    request_type: int | RequestType
    """The requested action"""

    keyed_parameters: dict[Any, Any] = field(default_factory=dict)
    """Keyed Parameters for the request"""

    positional_parameters: list[Any] = field(default_factory=list)
    """Positional Parameters for the request"""

    @overload
    def get_required(self, index: int, expected: None = None, *, name: str | None = None) -> Any: ...
    @overload
    def get_required[T](self, index: int, expected: type[T], *, name: str | None = None) -> T: ...
    @overload
    def get_required[T](self, index: int, expected: TypeSpecTuple[T], *, name: str | None = None) -> T: ...
    def get_required(self, index: int, expected: TypeSpec | None = None, *, name: str | None = None) -> Any:
        """
        Return positional parameter `index`, which the request type defines as required.

        An explicit `nil` counts as absent.

        Args:
            index: Index within the Positional Parameters
            expected: Type the parameter must match or `None` to skip type-checking
            name: The parameter's name in the specification, for error messages

        Raises:
            IncompleteRequestError: if the parameter is absent or nil.
            WrongTypeError: if it is present but not of `expected` type.
        """

        # Resolve the parameter name
        idx_string = f"positional parameter {index}"
        name = f"{name} ({idx_string})" if name else idx_string

        if index >= len(self.positional_parameters) or self.positional_parameters[index] is None:
            raise IncompleteRequestError(message=f"{name} is required")

        value = self.positional_parameters[index]

        if expected is None:
            return value
        if not isinstance(expected, tuple):
            expected = (expected,)
        return assert_parameter_type(value, expected, name=name)

    @overload
    def get_optional(
        self, index: int, expected: None = None, *, name: str | None = None, default: Any = None
    ) -> Any: ...
    @overload
    def get_optional[T](
        self, index: int, expected: type[T], *, name: str | None = None, default: None = None
    ) -> T | None: ...
    @overload
    def get_optional[T](self, index: int, expected: type[T], *, name: str | None = None, default: T) -> T: ...
    @overload
    def get_optional[T](
        self, index: int, expected: TypeSpecTuple[T], *, name: str | None = None, default: None = None
    ) -> T | None: ...
    @overload
    def get_optional[T](self, index: int, expected: TypeSpecTuple[T], *, name: str | None = None, default: T) -> T: ...
    def get_optional(
        self,
        index: int,
        expected: TypeSpec | None = None,
        *,
        name: str | None = None,
        default: Any = None,
    ) -> Any:
        """
        Return positional parameter `index`, or `default` if it was not supplied.

        An explicit `nil` counts as absent.

        Args:
            index: Index within the Positional Parameters
            expected: Type the parameter must match or `None` to skip type-checking
            name: The parameter's name in the specification, for error messages
            default: Returned in place of an absent parameter, unchecked

        Raises:
            WrongTypeError: if the parameter is present but not of `expected` type.
        """

        # Resolve the parameter name
        idx_string = f"positional parameter {index}"
        name = f"{name} ({idx_string})" if name else idx_string

        if index >= len(self.positional_parameters) or self.positional_parameters[index] is None:
            return default

        value = self.positional_parameters[index]

        if expected is None:
            return value
        if not isinstance(expected, tuple):
            expected = (expected,)
        return assert_parameter_type(value, expected, name=name)

    @overload
    def get_keyed(self, key: Any, expected: None = None, *, name: str | None = None, default: Any = None) -> Any: ...
    @overload
    def get_keyed[T](
        self, key: Any, expected: type[T], *, name: str | None = None, default: None = None
    ) -> T | None: ...
    @overload
    def get_keyed[T](self, key: Any, expected: type[T], *, name: str | None = None, default: T) -> T: ...
    @overload
    def get_keyed[T](
        self, key: Any, expected: TypeSpecTuple[T], *, name: str | None = None, default: None = None
    ) -> T | None: ...
    @overload
    def get_keyed[T](self, key: Any, expected: TypeSpecTuple[T], *, name: str | None = None, default: T) -> T: ...
    def get_keyed(
        self,
        key: Any,
        expected: TypeSpec | None = None,
        *,
        name: str | None = None,
        default: Any = None,
    ) -> Any:
        """
        Return Keyed Parameter `key`, or `default` if it was not supplied.

        Keyed Parameters are always optional, and an explicit `nil` counts as absent.

        Args:
            key: Key within the Keyed Parameter map
            expected: Type the parameter must match or `None` to skip type-checking
            name: The parameter's name in the specification, for error messages
            default: Returned in place of an absent parameter, unchecked

        Raises:
            WrongTypeError: if the parameter is present but not of `expected` type.
        """

        # Resolve the parameter name
        key_string = f"keyed parameter {key}"
        name = f"{name} ({key_string})" if name else key_string

        value = self.keyed_parameters.get(int(key) if isinstance(key, int) else key)

        if value is None:
            return default

        if expected is None:
            return value
        if not isinstance(expected, tuple):
            expected = (expected,)
        return assert_parameter_type(value, expected, name=name)

    @override
    def to_array(self) -> list[Any]:
        array: list[Any] = [self.exchange_type, self.request_id, self.request_type]
        if self.keyed_parameters or self.positional_parameters:
            # We always need to add the keyed parameters, since they always go first
            array.append(dict(self.keyed_parameters))
            array.extend(self.positional_parameters)
        return array


@dataclass(slots=True)
class Response(Exchange):
    """An Exchange returning the result of a Request"""

    exchange_type: ClassVar[ExchangeType] = ExchangeType.RESPONSE

    request_id: int
    """The Request id this answers, matching the one the Request carried"""

    status: ResponseStatus | int
    """The status code of the response"""

    parameters: list[Any] = field(default_factory=list)
    """Return Parameters for OK, or an error-information map for NO/BAD"""

    @classmethod
    def ok(cls, request_id: int, *returns: Any) -> Response:
        """Build an `OK` Response carrying zero or more Return Parameters"""
        return cls(request_id, ResponseStatus.OK, list(returns))

    @classmethod
    def failure(cls, request_id: int, error: RnmmpError, request_type: int | None = None) -> Response:
        """
        Build the `NO` or `BAD` Response reporting `error`.

        Args:
            request_id: The Request id being answered
            error: The error to report
            request_type: The request type being answered.
                When given, and `error` carries no specific error code of its own, the code the
                error's class represents for this request type is filled in.
        """
        info = error.package_as_dict()
        if request_type is not None and ErrorInfoKey.SPECIFIC_ERROR not in info:
            code = _SPECIFIC_CODE_FOR.get((request_type, type(error)))
            if code is not None:
                info[ErrorInfoKey.SPECIFIC_ERROR] = code
        return cls(request_id, error.status, [info] if info else [])

    @property
    def is_ok(self) -> bool:
        """Whether the action was performed."""
        return self.status == ResponseStatus.OK

    def error(self, request_type: int | None = None) -> RnmmpError | None:
        """
        Convert a non-OK `Response` into an `RnmmpError`, or `None` for `OK`

        Args:
            request_type: The request type of the Request this is a response to,
                to allow for resolving a request-specific error code
        """
        if self.is_ok:
            return None
        raw = self.parameters[0] if self.parameters else None
        info: Mapping[Any, Any] = raw if isinstance(raw, Mapping) else {}
        # An unrecognised status still means the request was not performed; default it to `NO`.
        status = ResponseStatus(self.status) if self.status in ResponseStatus else ResponseStatus.NO
        return RnmmpError.unpackage_from_dict(status, info, request_type)

    @override
    def to_array(self) -> list[Any]:
        return [self.exchange_type, self.request_id, self.status, *self.parameters]


@dataclass(slots=True)
class Notification(Exchange):
    """An Exchange for which no Response is expected"""

    exchange_type: ClassVar[ExchangeType] = ExchangeType.NOTIFICATION

    event_type: int | NotificationType
    """The type of event the notification pertains to"""

    parameters: list[Any] = field(default_factory=list)
    """Parameters, as defined by the event type"""

    @override
    def to_array(self) -> list[Any]:
        return [self.exchange_type, self.event_type, *self.parameters]


@dataclass(slots=True)
class UnknownExchange(Exchange):
    """
    An Exchange with a type this version does not recognize
    """

    exchange_type: int | ExchangeType
    """The unrecognized Exchange Type, preserved so the Exchange can be relayed or re-packed"""

    parameters: list[Any] = field(default_factory=list)

    @override
    def to_array(self) -> list[Any]:
        return [self.exchange_type, *self.parameters]
