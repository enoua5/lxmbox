"""
Checks every code registry in `rnmmp_core.codes` against the tables in `docs/spec/rnmmp.md`.
This is done statically against the actual document text, so changes to the spec should get flagged in tests.

The document is parsed in this module because `parametrize` needs the tables at collection time
and pytest's `importlib` mode offers no import path to a shared helper.
"""

import re
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

import pytest

from rnmmp_core import (
    PROTOCOL_VERSION,
    SPECIFIC_ERRORS,
    Collection,
    ErrorInfoKey,
    ExchangeType,
    GeneralError,
    MailListDeltaKey,
    MetadataKey,
    NotificationType,
    RequestType,
    ResponseStatus,
    SearchContentParam,
    SearchTitleParam,
    SendLxmfParam,
    SendRawParam,
    ServerTag,
    UploadParam,
    WriteParam,
)

################################################################################
# Locating and parsing the specification
################################################################################

SPEC_RELATIVE_PATH = Path("docs/spec/rnmmp.md")


def _find_spec() -> Path | None:
    """Walk up from this file looking for the specification, which a wheel install will not have"""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / SPEC_RELATIVE_PATH
        if candidate.is_file():
            return candidate
    return None


@dataclass(frozen=True)
class SpecTable:
    """One markdown table, tagged with the heading and bold label it appeared under"""

    heading: str
    label: str | None
    rows: tuple[dict[str, str], ...]

    def mapping(self, code_column: str = "Code", name_column: str = "Name") -> dict[str, int]:
        """Read the table as the {NAME: code} mapping a registry should hold"""
        return {row[name_column]: int(row[code_column]) for row in self.rows}


def _cells(line: str) -> list[str]:
    """Split one markdown table row into its stripped cells"""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(line: str) -> bool:
    """Whether a line is the `|---|---|` rule under a table's header row"""
    return line.startswith("|") and set(line.replace("|", "").replace(" ", "")) <= {"-", ":"}


def _parse_tables(text: str) -> tuple[SpecTable, ...]:
    """Extract every markdown table, tagged with the nearest heading and bold label above it"""
    lines = text.splitlines()
    tables: list[SpecTable] = []
    heading = ""
    label: str | None = None

    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("#"):
            heading, label = line.lstrip("#").strip(), None
        elif bold := re.fullmatch(r"\*\*(.+)\*\*", line.strip()):
            label = bold.group(1)
        elif line.startswith("|") and index + 1 < len(lines) and _is_separator(lines[index + 1]):
            headers = _cells(line)
            rows: list[dict[str, str]] = []
            index += 2
            while index < len(lines) and lines[index].startswith("|"):
                rows.append(dict(zip(headers, _cells(lines[index]), strict=False)))
                index += 1
            tables.append(SpecTable(heading, label, tuple(rows)))
            continue
        index += 1

    return tuple(tables)


SPEC_PATH = _find_spec()
SPEC_TEXT = SPEC_PATH.read_text(encoding="utf-8") if SPEC_PATH else ""
SPEC_TABLES = _parse_tables(SPEC_TEXT)

pytestmark = pytest.mark.skipif(
    SPEC_PATH is None,
    reason=f"{SPEC_RELATIVE_PATH} is not alongside the package, so the registries cannot be cross-checked",
)


def table(heading: str, label: str | None = None) -> SpecTable:
    """Return the single table under `heading` carrying `label`, failing if it is not unique"""
    matches = [found for found in SPEC_TABLES if found.heading == heading and found.label == label]
    assert len(matches) == 1, (
        f"expected exactly one {label or 'unlabelled'} table under {heading!r}, got {len(matches)}"
    )
    return matches[0]


def implemented(enum: type[IntEnum]) -> dict[str, int]:
    """Read an enum as the {NAME: code} mapping to compare against a spec table"""
    return {member.name: member.value for member in enum}


################################################################################
# The parser itself
################################################################################


class TestSmokeSpecLoader:
    """
    Check that we're actually loading tables from the spec
    """

    def test_the_specification_was_found(self) -> None:
        """The document being checked against is the one in this repository"""
        assert SPEC_PATH is not None
        assert SPEC_PATH.name == "rnmmp.md"

    def test_a_substantial_number_of_tables_were_parsed(self) -> None:
        """The spec defines a table per registry plus parameter tables per request type"""
        assert len(SPEC_TABLES) > 50

    def test_a_known_table_parses_to_its_known_content(self) -> None:
        """A table whose content is stated here by hand parses exactly as written in the document"""
        assert table("Exchange").mapping(name_column="Exchange Type") == {
            "Request": 0,
            "Response": 1,
            "Notification": 2,
        }

    def test_every_parsed_table_has_rows(self) -> None:
        """A heading matched with an empty row list means the parser lost the table body"""
        assert [found.heading for found in SPEC_TABLES if not found.rows] == []


################################################################################
# Registries
################################################################################


class TestExchangeStructure:
    """The codes that frame every Exchange"""

    def test_exchange_types_match_the_specification(self) -> None:
        """The first item of an Exchange array is one of the Exchange Type codes"""
        assert implemented(ExchangeType) == {
            name.upper(): code for name, code in table("Exchange").mapping(name_column="Exchange Type").items()
        }

    def test_response_statuses_match_the_specification(self) -> None:
        """The second parameter of a Response is its status code"""
        assert implemented(ResponseStatus) == table("Responses").mapping(name_column="Status")

    def test_request_types_match_the_specification(self) -> None:
        """Every request type the spec assigns a code is implemented, under that code"""
        assert implemented(RequestType) == table("Request types").mapping()

    def test_notification_types_match_the_specification(self) -> None:
        """Every standardized event type the spec assigns a code is implemented, under that code"""
        assert implemented(NotificationType) == table("Notifications").mapping()

    def test_the_protocol_version_matches_the_specification(self) -> None:
        """The first element of the Capability List MUST be a protocol version"""
        # If this text changes, it's probably a re-written implementation anyway
        stated = re.search(r"only version code supported is `(\d+)`", SPEC_TEXT)
        assert stated is not None
        assert int(stated.group(1)) == PROTOCOL_VERSION


class TestErrorCodes:
    """The codes carried in the error-information map of a NO or BAD Response"""

    def test_error_information_keys_match_the_specification(self) -> None:
        """The reserved integer keys of the error-information map"""
        assert implemented(ErrorInfoKey) == table("Error information").mapping()

    def test_general_errors_match_the_specification(self) -> None:
        """Values for the GENERAL_ERROR key, applicable to any request type"""
        assert implemented(GeneralError) == table("General error codes").mapping()


class TestCollectionsAndTags:
    """The codes describing mailbox state"""

    def test_collections_match_the_specification(self) -> None:
        """The four Collections, each with its own State Token"""
        assert implemented(Collection) == table("Mailbox state").mapping(name_column="Collection name")

    def test_server_defined_tags_match_the_specification(self) -> None:
        """Tags with IDs from -32 to -1 inclusive are reserved for definition within this spec"""
        assert implemented(ServerTag) == table("TAG_LIST").mapping(code_column="ID")

    def test_metadata_keys_match_the_specification(self) -> None:
        """Integer Metadata keys between 0 and 127 inclusive are reserved for definition within this spec"""
        assert implemented(MetadataKey) == table("METADATA").mapping(code_column="ID")

    def test_mail_list_delta_keys_match_the_specification(self) -> None:
        """The MAIL_LIST Delta has two keys, stated in prose rather than a table"""
        prose = SPEC_TEXT.split("#### MAIL_LIST Delta")[1].split("####")[0]
        stated = dict(re.findall(r"([A-Z_]+) \(`(\d+)`\)", prose))
        assert implemented(MailListDeltaKey) == {name: int(code) for name, code in stated.items()}


################################################################################
# Per-request-type tables
################################################################################

# Stated by hand so the test asserts the correspondence rather than restating the code's.
KEYED_PARAMETERS = {
    "Requests that mutate state": WriteParam,
    "UPLOAD": UploadParam,
    "SEARCH_TITLE": SearchTitleParam,
    "SEARCH_CONTENT": SearchContentParam,
    "SEND_RAW": SendRawParam,
    "SEND_LXMF": SendLxmfParam,
}

SECTIONS_WITH_KEYED_PARAMETERS = sorted({found.heading for found in SPEC_TABLES if found.label == "Keyed Parameters"})
SECTIONS_WITH_SPECIFIC_ERRORS = sorted(
    {found.heading for found in SPEC_TABLES if found.label == "Specific Error Codes"}
)


class TestKeyedParameters:
    """The Keyed Parameter map each request type defines"""

    def test_every_section_with_keyed_parameters_has_an_enum(self) -> None:
        """A request type that gained Keyed Parameters in the spec needs an enum to name them"""
        assert sorted(KEYED_PARAMETERS) == SECTIONS_WITH_KEYED_PARAMETERS

    @pytest.mark.parametrize("heading", SECTIONS_WITH_KEYED_PARAMETERS)
    def test_keyed_parameters_match_the_specification(self, heading: str) -> None:
        """Each Keyed Parameter is implemented under the key the spec assigns it"""
        assert implemented(KEYED_PARAMETERS[heading]) == table(heading, "Keyed Parameters").mapping(code_column="Key")

    def test_every_write_request_shares_if_in_state(self) -> None:
        """IF_IN_STATE applies to every request that mutates state, under the same key throughout"""
        for enum in (UploadParam, SendRawParam, SendLxmfParam):
            assert enum["IF_IN_STATE"].value == WriteParam.IF_IN_STATE.value


class TestSpecificErrors:
    """The Specific Error Codes each request type defines"""

    def test_specific_errors_are_registered_for_exactly_the_right_request_types(self) -> None:
        """SPECIFIC_ERRORS covers every request type whose spec section defines error codes, and no other"""
        assert sorted(request_type.name for request_type in SPECIFIC_ERRORS) == SECTIONS_WITH_SPECIFIC_ERRORS

    @pytest.mark.parametrize("heading", SECTIONS_WITH_SPECIFIC_ERRORS)
    def test_specific_errors_match_the_specification(self, heading: str) -> None:
        """Each Specific Error Code is implemented under the code the spec assigns it"""
        enum = SPECIFIC_ERRORS[RequestType[heading]]

        assert implemented(enum) == table(heading, "Specific Error Codes").mapping()
