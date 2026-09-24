"""A basic rnmmp-server Store binding that saves messages to disk"""

from base64 import urlsafe_b64encode
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from rnmmp_core import INITIAL_STATE_TOKEN, pack, unpack
from rnmmp_server import ChangeSet, LogEntry, MessageIndex, ScanSearch, StoredMessage

from .config import Config


class FileStore(ScanSearch):
    """Simple file-based rnmmp state store"""

    def __init__(self, config: Config, *, log_limit: int = 512) -> None:
        """Initialize the store"""

        self._path = config.storage_path
        self._path.mkdir(parents=True, exist_ok=True)
        # Just msgpack files for the example — a database would probably be better.
        self._mail_list_path = self._path / "mail_list.msgpack"
        self._tag_list_path = self._path / "tag_list.msgpack"
        self._message_tag_path = self._path / "message_tag.msgpack"
        self._metadata_path = self._path / "metadata.msgpack"
        self._token_path = self._path / "token.msgpack"
        self._log_path = self._path / "log.msgpack"
        self._log_limit = log_limit

        self._mail_path = self._path / "mail"
        self._mail_path.mkdir(parents=True, exist_ok=True)

    def _load_state[T](self, path: Path, default: T) -> T:
        """Load one msgpack state file, `default` when it does not exist yet"""
        try:
            return cast(T, unpack(path.read_bytes()))
        except FileNotFoundError:
            return default

    @contextmanager
    def _staged_writes(self) -> Generator[Callable[[Path, bytes], None]]:
        """
        Stage file writes, then commit all changes at the end of the block.

        This is to prevent a crash from causing a partial state update.
        """
        staged: list[tuple[Path, Path]] = []

        def stage(path: Path, data: bytes) -> None:
            scratch = path.with_name(path.name + ".tmp")
            scratch.write_bytes(data)
            staged.append((scratch, path))

        try:
            yield stage
        except BaseException:
            for scratch, _ in staged:
                scratch.unlink(missing_ok=True)
            raise
        for scratch, path in staged:
            scratch.replace(path)

    def _get_file_path(self, message_id: bytes) -> Path:
        """The file a message's raw content lives in"""
        filename = urlsafe_b64encode(message_id).replace(b"=", b"").decode()
        return self._mail_path / filename

    def get_all_message_ids(self) -> list[bytes]:
        """Every message id in the MAIL_LIST Collection"""

        messages: dict[bytes, Any] = self._load_state(self._mail_list_path, {})
        return list(messages)

    def get_existing_message_ids(self, message_ids: Sequence[bytes]) -> set[bytes]:
        """The subset of the requested ids present in the MAIL_LIST Collection"""
        all_message_ids = set(self.get_all_message_ids())
        return {message_id for message_id in message_ids if message_id in all_message_ids}

    def get_message_indexes(self, message_ids: Sequence[bytes]) -> list[MessageIndex | None]:
        """The index records, in the order requested, `None` for each id not present; content untouched"""

        data: dict[bytes, dict[str, Any]] = self._load_state(self._mail_list_path, {})

        results: list[MessageIndex | None] = []
        for message_id in message_ids:
            index = data.get(message_id)
            if index is None:
                results.append(None)
                continue

            results.append(
                MessageIndex(
                    message_id=message_id,
                    lxmf=index.get("lxmf", False),
                    head=index.get("head"),
                    timestamp=index.get("timestamp"),
                    title=index.get("title"),
                )
            )

        return results

    def get_messages(self, message_ids: Sequence[bytes]) -> list[StoredMessage | None]:
        """
        The stored messages with their content, in the order requested, `None` for each id not present.
        """
        indexes = self.get_message_indexes(message_ids)
        results: list[StoredMessage | None] = []
        for index in indexes:
            if index is None:
                results.append(None)
                continue

            # Might raise — will need to configure handling in actual server
            raw = self._get_file_path(index.message_id).read_bytes()

            results.append(index.as_stored_message(raw))

        return results

    def get_all_tags(self) -> dict[int, str]:
        """The TAG_LIST Collection: every tag id and its name, Server-Defined Tags included"""
        return self._load_state(self._tag_list_path, {})

    def get_message_tags(self, message_ids: Sequence[bytes]) -> dict[bytes, set[int]]:
        """The tag ids on each requested message; every requested id is a key, empty for untagged and unknown ids"""
        tag_pairs: list[tuple[bytes, int]] = self._load_state(self._message_tag_path, [])

        message_tags: dict[bytes, set[int]] = {message_id: set() for message_id in message_ids}
        for message_id, tag_id in tag_pairs:
            if message_id in message_tags:
                message_tags[message_id].add(tag_id)
        return message_tags

    def get_messages_with_tags(self, tag_ids: Sequence[int]) -> dict[int, set[bytes]]:
        """The message ids carrying each requested tag; every requested id is a key, empty for unused and unknown ids"""
        tag_pairs: list[tuple[bytes, int]] = self._load_state(self._message_tag_path, [])

        tag_messages: dict[int, set[bytes]] = {tag_id: set() for tag_id in tag_ids}
        for message_id, tag_id in tag_pairs:
            if tag_id in tag_messages:
                tag_messages[tag_id].add(message_id)
        return tag_messages

    def get_message_metadata(self, message_ids: Sequence[bytes]) -> dict[bytes, dict[Any, Any]]:
        """Each requested message's metadata map; every requested id is a key, bare and unknown ids with an empty map"""
        metadata: dict[bytes, dict[Any, Any]] = self._load_state(self._metadata_path, {})
        return {message_id: dict(metadata.get(message_id, {})) for message_id in message_ids}

    def get_current_token(self, collection: int) -> bytes:
        """The Collection's State Token; the Initial State Token if it has never changed"""
        tokens: dict[int, bytes] = self._load_state(self._token_path, {})
        return tokens.get(collection, INITIAL_STATE_TOKEN)

    def get_entries_since(self, collection: int, token: bytes) -> list[LogEntry] | None:
        """
        The change-log entries after `token`, oldest first.

        Returns `[]` when `token` is the Collection's current token, and `None` when the token
        is not one this store can still answer for — pruned, foreign, or never issued.
        """

        if token == self.get_current_token(collection):
            return []

        # collection id -> (token, opaque state fragment)[]
        logs: dict[int, list[tuple[bytes, bytes]]] = self._load_state(self._log_path, {})
        log = logs.get(collection, [])
        # Find `token` in our index
        for index, (token_before, _) in enumerate(log):
            if token_before == token:
                # return everything after
                return [LogEntry(token_before=before, fragment=fragment) for before, fragment in log[index:]]

        return None

    def apply(self, changes: ChangeSet) -> None:
        """Apply one write's mutations"""

        # TODO this one is maybe a bit complex
        # probably separate applications would be better

        messages: dict[bytes, dict[str, Any]] = self._load_state(self._mail_list_path, {})
        metadata: dict[bytes, dict[Any, Any]] = self._load_state(self._metadata_path, {})
        loaded_pairs: list[tuple[bytes, int]] = self._load_state(self._message_tag_path, [])
        tag_pairs = {(message_id, tag_id) for message_id, tag_id in loaded_pairs}
        tags: dict[int, str] = self._load_state(self._tag_list_path, {})
        tokens: dict[int, bytes] = self._load_state(self._token_path, {})
        logs: dict[int, list[list[bytes]]] = self._load_state(self._log_path, {})

        with self._staged_writes() as stage:
            for message in changes.messages_added:
                messages[message.message_id] = {
                    "head": message.head,
                    "title": message.title,
                    "timestamp": message.timestamp,
                    "lxmf": message.lxmf,
                }
                stage(self._get_file_path(message.message_id), message.raw)

            for message_id in changes.message_ids_deleted:
                messages.pop(message_id, None)
                metadata.pop(message_id, None)
                tag_pairs = {pair for pair in tag_pairs if pair[0] != message_id}

            tags.update(changes.tags_created)
            tags.update(changes.tags_renamed)
            for tag_id in changes.tag_ids_deleted:
                tags.pop(tag_id, None)
                tag_pairs = {pair for pair in tag_pairs if pair[1] != tag_id}

            tag_pairs.update(changes.tag_pairs_added)
            tag_pairs.difference_update(changes.tag_pairs_removed)

            for message_id, entries in changes.metadata_set.items():
                metadata.setdefault(message_id, {}).update(entries)
            for message_id, keys in changes.metadata_keys_removed.items():
                item_metadata = metadata.get(message_id, {})
                for key in keys:
                    item_metadata.pop(key, None)

            tokens.update(changes.new_tokens)

            for collection, entry in changes.log_entries.items():
                log = logs.get(collection, [])
                log.append([entry.token_before, entry.fragment])
                logs[collection] = log[-self._log_limit :]

            stage(self._mail_list_path, pack(messages))
            stage(self._metadata_path, pack(metadata))
            stage(self._message_tag_path, pack(list(tag_pairs)))
            stage(self._tag_list_path, pack(tags))
            stage(self._token_path, pack(tokens))
            stage(self._log_path, pack(logs))
