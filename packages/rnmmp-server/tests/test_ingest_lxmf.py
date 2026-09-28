"""
Tests for `ingest_lxmf`, the entry point for a delivered LXMF message.
"""

import LXMF
import pytest
import RNS

from rnmmp_core import Collection, MetadataKey, ServerTag, UnknownTagError
from rnmmp_server import MailboxModel, MemoryStore, StoredMessage


def a_destination() -> RNS.Destination:
    """An LXMF delivery destination for a throwaway identity"""
    return RNS.Destination(RNS.Identity(), RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")


def a_message(content: bytes = b"the content", title: bytes = b"the title") -> LXMF.LXMessage:
    """An unpacked message, as a held by the sender before packing"""
    return LXMF.LXMessage(a_destination(), a_destination(), content, title=title)


def delivered(content: bytes = b"the content", title: bytes = b"the title") -> LXMF.LXMessage:
    """A message carrying `.packed`, as it would be when delivered"""
    message = a_message(content, title)
    message.pack()
    return message


class TestIngestLxmf:
    """What a delivered message becomes in the mailbox"""

    def test_the_message_id_is_the_lxmf_hash(self) -> None:
        """Ids are stable across sessions and devices because they are the message's own hash"""
        message = delivered()

        MailboxModel(MemoryStore()).ingest_lxmf(message)

        assert message.message_id == message.hash
        assert len(message.hash) == 32

    def test_a_delivered_message_enters_with_the_mailbox_defaults(self) -> None:
        """Default tags and metadata are added"""
        message = delivered()
        model = MailboxModel(MemoryStore())

        updated = model.ingest_lxmf(message)

        assert set(updated) == {Collection.MAIL_LIST, Collection.MESSAGE_TAG, Collection.METADATA}
        assert model.tags_of([message.hash]) == [[int(ServerTag.UNREAD)]]
        [metadata] = model.metadata_of([message.hash])
        assert metadata is not None and set(metadata) == {int(MetadataKey.RECEIVE_TIME)}

    def test_the_stored_bytes_are_the_delivered_bytes(self) -> None:
        """FETCH_FULL must answer byte-for-byte, so `.packed` is stored verbatim"""
        message = delivered()
        model = MailboxModel(MemoryStore())
        model.ingest_lxmf(message)

        record = model.get_messages([message.hash])[0]

        assert isinstance(record, StoredMessage)
        assert record.raw == message.packed

    def test_the_indexed_portions_come_from_the_real_payload(self) -> None:
        """The head slice and Title parse out of bytes in the raw LXMF"""
        message = delivered(title=b"a real title")
        model = MailboxModel(MemoryStore())
        model.ingest_lxmf(message)

        (index,) = model.index_of([message.hash])

        assert index is not None
        assert index.lxmf
        assert index.title == b"a real title"
        assert index.timestamp == pytest.approx(message.timestamp)

    def test_an_unpacked_message_is_refused(self) -> None:
        """Only a delivered message carries `.packed`; packing it here would re-sign it"""
        with pytest.raises(ValueError, match="packed"):
            MailboxModel(MemoryStore()).ingest_lxmf(a_message())

    def test_a_redelivered_message_changes_nothing(self) -> None:
        """The same delivered bytes arriving twice are the same message"""
        message = delivered()
        model = MailboxModel(MemoryStore())
        model.ingest_lxmf(message)

        assert model.ingest_lxmf(message) == {}


class TestCallerSuppliedTagsAndMetadata:
    """What the server adds alongside what the policies' tags and metadata"""

    def test_caller_tags_join_the_initial_tags(self) -> None:
        """An outbox copy is filed `OUTBOX` and also the initial tags"""
        message = delivered()
        model = MailboxModel(MemoryStore())

        model.ingest_lxmf(message, tags=[ServerTag.OUTBOX])

        assert model.tags_of([message.hash]) == [[int(ServerTag.OUTBOX), int(ServerTag.UNREAD)]]

    def test_caller_metadata_is_added_to_the_record(self) -> None:
        """Server-side metadata is trusted, so a managed key may be set here"""
        message = delivered()
        model = MailboxModel(MemoryStore())

        model.ingest_lxmf(message, metadata={int(MetadataKey.RECEIVE_TIME): 12345, "route": "direct"})

        assert model.metadata_of([message.hash]) == [{int(MetadataKey.RECEIVE_TIME): 12345, "route": "direct"}]

    def test_an_unknown_caller_tag_is_refused(self) -> None:
        """Tags must exist to be added"""
        with pytest.raises(UnknownTagError):
            MailboxModel(MemoryStore()).ingest_lxmf(delivered(), tags=[42])


class TestSignatureValidation:
    """
    The server library trusts what the server hands it.
    If the server hands us an unverified message, we assume they want to store it anyway,
    """

    def test_an_unverified_message_is_stored_anyway(self) -> None:
        """`ingest_lxmf` does not check `signature_validated`"""
        message = delivered()
        message.signature_validated = False
        message.unverified_reason = LXMF.LXMessage.SIGNATURE_INVALID
        model = MailboxModel(MemoryStore())

        model.ingest_lxmf(message)

        assert model.tags_of([message.hash]) == [[int(ServerTag.UNREAD)]]
