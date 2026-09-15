"""
Tests for the code registries' internal consistency and general spec compliance.

Specific spec compliance is tested in `test_spec_conformance.py`.
"""

from enum import IntEnum
from types import ModuleType

import pytest

import rnmmp_core
from rnmmp_core import (
    INITIAL_STATE_TOKEN,
    SPECIFIC_ERRORS,
    Collection,
    MetadataKey,
    RequestType,
    ServerTag,
)
from rnmmp_core import SPECIFIC_ERROR_TO_EXCEPTION as ERROR_CLASSES

PUBLIC_ENUMS = sorted(
    (
        value
        for value in vars(rnmmp_core).values()
        if isinstance(value, type) and issubclass(value, IntEnum) and value is not IntEnum
    ),
    key=lambda enum: enum.__name__,
)

ENUM_PARAMS = [pytest.param(enum, id=enum.__name__) for enum in PUBLIC_ENUMS]


class TestEnumSmoke:
    """Smoke tests for enums"""

    def test_the_enums_were_found(self) -> None:
        """Ensure enums are being picked up by tests"""
        assert len(PUBLIC_ENUMS) > 15

    @pytest.mark.parametrize("enum", ENUM_PARAMS)
    def test_an_enum_has_no_duplicate_members(self, enum: type[IntEnum]) -> None:
        """No two members of an enum share a code"""
        assert len(enum.__members__) == len(set(enum.__members__.values()))


class TestReservedRanges:
    """Ids stay inside the ranges the specification reserves for it to define"""

    def test_request_types_are_standard(self) -> None:
        """The numbers from 0-127 inclusive are reserved for standard request types"""
        assert all(0 <= member.value <= 127 for member in RequestType)

    def test_collections_are_standard(self) -> None:
        """The integers 0-127 inclusive are reserved for standard Collections"""
        assert all(0 <= member.value <= 127 for member in Collection)

    def test_metadata_keys_are_standard(self) -> None:
        """Integer Metadata keys between 0 and 127 inclusive are reserved for standard metadata"""
        assert all(0 <= member.value <= 127 for member in MetadataKey)

    def test_server_defined_tags_are_negative_and_reserved(self) -> None:
        """Tags with IDs from -32 to -1 inclusive are reserved, and user tags take positive ids"""
        assert all(-32 <= member.value <= -1 for member in ServerTag)


class TestSpecificErrorTables:
    """`SPECIFIC_ERRORS` and `SPECIFIC_ERROR_TO_EXCEPTION` describe the same set of codes"""

    @pytest.mark.parametrize(
        "request_type",
        [pytest.param(request_type, id=request_type.name) for request_type in SPECIFIC_ERRORS],
    )
    def test_every_specific_error_code_has_an_exception(self, request_type: RequestType) -> None:
        """Every registered code has a typed exception"""
        missing = [
            member.name for member in SPECIFIC_ERRORS[request_type] if (request_type, member) not in ERROR_CLASSES
        ]

        assert missing == []

    def test_no_exception_is_mapped_for_an_unregistered_code(self) -> None:
        """The exception table maps no code that the registry does not define"""
        unreachable = [
            (RequestType(request_type).name, code)
            for request_type, code in ERROR_CLASSES
            if RequestType(request_type) not in SPECIFIC_ERRORS
            or code not in [member.value for member in SPECIFIC_ERRORS[RequestType(request_type)]]
        ]

        assert unreachable == []


class TestConstants:
    """The bare constants the protocol defines"""

    def test_the_initial_state_token_is_the_zero_length_byte_string(self) -> None:
        """The zero-length byte array is reserved to represent the Initial State."""
        assert INITIAL_STATE_TOKEN == b""


class TestReExportSurface:
    """`__all__` is the package's public API"""

    def test_every_exported_name_exists(self) -> None:
        """Not missing definitions"""
        missing = [name for name in rnmmp_core.__all__ if not hasattr(rnmmp_core, name)]

        assert missing == []

    def test_every_public_name_is_exported(self) -> None:
        """Private package members should start with `_` and not be exported"""
        defined = {
            name
            for name, value in vars(rnmmp_core).items()
            if not name.startswith("_") and not isinstance(value, ModuleType)
        }

        assert defined - set(rnmmp_core.__all__) == set()

    def test_nothing_is_exported_twice(self) -> None:
        """No name appears in `__all__` twice"""
        assert len(rnmmp_core.__all__) == len(set(rnmmp_core.__all__))
