"""
Property tests for delta sync: under any sequence of writes, a delta must carry a client from
the state it knows to the state the server holds.
"""

import contextlib
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from rnmmp_core import INITIAL_STATE_TOKEN, Collection, DuplicateTagNameError, MailListDeltaKey, ServerTag
from rnmmp_server import MailboxModel, MemoryStore

MESSAGE_IDS = [bytes([index]) * 32 for index in range(1, 5)]
METADATA_KEYS = ["k1", "k2"]

OPERATIONS = st.one_of(
    st.tuples(st.just("ingest"), st.integers(0, 3)),
    st.tuples(st.just("upload"), st.integers(0, 255)),
    st.tuples(st.just("delete"), st.integers(0, 7)),
    st.tuples(st.just("create"), st.integers(0, 5)),
    st.tuples(st.just("delete_tag"), st.integers(0, 7)),
    st.tuples(st.just("rename"), st.integers(0, 7), st.integers(0, 5)),
    st.tuples(st.just("tag"), st.integers(0, 7), st.integers(0, 7)),
    st.tuples(st.just("untag"), st.integers(0, 7), st.integers(0, 7)),
    st.tuples(st.just("meta"), st.integers(0, 7), st.integers(0, 1), st.integers(0, 3)),
    st.tuples(st.just("unmeta"), st.integers(0, 7), st.integers(0, 1)),
)


def run(model: MailboxModel, operation: tuple[Any, ...]) -> None:
    """Apply one drawn operation, resolving ids against whatever the mailbox currently holds"""
    kind = operation[0]
    message_ids = model.message_ids()
    user_tags = sorted(tag_id for tag_id in model.tags() if tag_id > 0)
    if kind == "ingest":
        model.ingest(MESSAGE_IDS[operation[1]], b"raw", lxmf=True, tags=[ServerTag.UNREAD])
    elif kind == "upload":
        model.upload([bytes([operation[1]])])
    elif kind == "delete" and message_ids:
        model.delete([message_ids[operation[1] % len(message_ids)]])
    elif kind == "create":
        model.create_tags([f"tag {operation[1]}"])
    elif kind == "delete_tag" and user_tags:
        model.delete_tags([user_tags[operation[1] % len(user_tags)]])
    elif kind == "rename" and user_tags:
        with contextlib.suppress(DuplicateTagNameError):
            model.rename_tags({user_tags[operation[1] % len(user_tags)]: f"renamed {operation[2]}"})
    elif kind in ("tag", "untag") and message_ids:
        message_id = message_ids[operation[1] % len(message_ids)]
        all_tags = sorted(model.tags())
        picked = {message_id: [all_tags[operation[2] % len(all_tags)]]}
        model.add_tags(picked) if kind == "tag" else model.remove_tags(picked)
    elif kind == "meta" and message_ids:
        message_id = message_ids[operation[1] % len(message_ids)]
        model.set_metadata({message_id: {METADATA_KEYS[operation[2]]: operation[3]}})
    elif kind == "unmeta" and message_ids:
        message_id = message_ids[operation[1] % len(message_ids)]
        model.remove_metadata({message_id: [METADATA_KEYS[operation[2]]]})


def canonical(collection: int, delta: dict[Any, Any]) -> Any:
    """A full-state delta as comparable client state"""
    if collection == Collection.MAIL_LIST:
        return set(delta[int(MailListDeltaKey.ADDED)])
    if collection == Collection.MESSAGE_TAG:
        return {message_id: frozenset(tag_ids) for message_id, tag_ids in delta.items()}
    return dict(delta)


def advance(collection: int, state: Any, delta: dict[Any, Any]) -> Any:
    """
    Apply a delta to known client state, the way the spec tells a client to.

    MESSAGE_TAG is a set of pairs and METADATA a map of maps — with an empty value
    and `nil` leaving the client in the same state.
    """
    if collection == Collection.MAIL_LIST:
        return (state | set(delta[int(MailListDeltaKey.ADDED)])) - set(delta[int(MailListDeltaKey.DELETED)])
    updated = dict(state)
    for key, value in delta.items():
        if value is None or (collection != Collection.TAG_LIST and not value):
            updated.pop(key, None)
        elif collection == Collection.MESSAGE_TAG:
            updated[key] = frozenset(value)
        else:
            updated[key] = value
    return updated


class TestDeltaSync:
    """Any client, any checkpoint, any writes after it: one sync catches the client up"""

    @given(operations=st.lists(OPERATIONS, max_size=30), checkpoint=st.integers(0, 30))
    def test_a_delta_carries_a_client_from_its_state_to_the_current_state(
        self, operations: list[tuple[Any, ...]], checkpoint: int
    ) -> None:
        """Applying the Delta to the state the token names reproduces the server's current state"""
        model = MailboxModel(MemoryStore(log_limit=4096))
        checkpoint = min(checkpoint, len(operations))
        for operation in operations[:checkpoint]:
            run(model, operation)

        known: dict[int, Any] = {}
        tokens: dict[int, bytes] = {}
        for collection in Collection:
            full, tokens[collection] = model.sync(collection, INITIAL_STATE_TOKEN)
            known[collection] = canonical(collection, full)

        for operation in operations[checkpoint:]:
            run(model, operation)

        for collection in Collection:
            delta, state = model.sync(collection, tokens[collection])
            current, current_token = model.sync(collection, INITIAL_STATE_TOKEN)

            assert advance(collection, known[collection], delta) == canonical(collection, current)
            assert state == current_token

    @given(operations=st.lists(OPERATIONS, max_size=30), checkpoint=st.integers(0, 30))
    def test_mail_list_deltas_are_disjoint_and_truthful(
        self, operations: list[tuple[Any, ...]], checkpoint: int
    ) -> None:
        """A Message id MUST NOT appear in both lists; ADDED ids exist now and DELETED ids do not"""
        model = MailboxModel(MemoryStore(log_limit=4096))
        checkpoint = min(checkpoint, len(operations))
        for operation in operations[:checkpoint]:
            run(model, operation)
        _, token = model.sync(Collection.MAIL_LIST, INITIAL_STATE_TOKEN)
        for operation in operations[checkpoint:]:
            run(model, operation)

        delta, _ = model.sync(Collection.MAIL_LIST, token)
        added, deleted = set(delta[int(MailListDeltaKey.ADDED)]), set(delta[int(MailListDeltaKey.DELETED)])
        existing = set(model.message_ids())

        assert not added & deleted
        assert added <= existing
        assert not deleted & existing
