"""A basic rnmmp-server Store binding that saves messages to disk"""

import json
from base64 import urlsafe_b64encode
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rnmmp_core import INITIAL_STATE_TOKEN
from rnmmp_server import ChangeSet, LogEntry, MessageIndex, ScanSearch, StoredMessage

from .config import Config


class FileStore(ScanSearch):
    """Simple file-based rnmmp state store"""

    def __init__(self, config: Config, *, log_limit: int = 512) -> None:
        """Initialize the store"""

        self._path = config.storage_path
        self._path.mkdir(parents=True, exist_ok=True)
        # Just JSONs for the example — a database would probably be better
        self._mail_list_path = self._path / "mail_list.json"
        self._tag_list_path = self._path / "tag_list.json"
        self._message_tag_path = self._path / "message_tag.json"
        self._metadata_path = self._path / "metadata.json"
        self._token_path = self._path / "token.json"
        # self._log_path = self._path / "log.json"
        self._log_limit = log_limit

        self._mail_path = self._path / "mail"
        self._mail_path.mkdir(parents=True, exist_ok=True)

    def _load_json_dict(self, path: Path) -> dict[str, Any]:
        """Load JSON from file"""
        try:
            with open(path) as f:
                data: dict[str, Any] = json.load(f)
                return data
        except Exception:
            return {}

    def get_all_message_ids(self) -> list[bytes]:
        """Every message id in the MAIL_LIST Collection"""

        messages = self._load_json_dict(self._mail_list_path)
        return [bytes.fromhex(message_id) for message_id in messages]

    def get_existing_message_ids(self, message_ids: Sequence[bytes]) -> set[bytes]:
        """The subset of the requested ids present in the MAIL_LIST Collection"""
        all_message_ids = self.get_all_message_ids()
        return {message_id for message_id in message_ids if message_id in all_message_ids}

    def get_message_indexes(self, message_ids: Sequence[bytes]) -> list[MessageIndex | None]:
        """The index records, in the order requested, `None` for each id not present; content untouched"""

        data = self._load_json_dict(self._mail_list_path)

        results: list[MessageIndex | None] = []
        for message_id in message_ids:
            index: dict[str, Any] | None = data.get(message_id.hex(), None)
            if index is None:
                results.append(None)
                continue

            assert isinstance(index, dict)

            head = None if index.get("head") is None else bytes.fromhex(index["head"])
            title = None if index.get("title") is None else bytes.fromhex(index["title"])

            results.append(
                MessageIndex(
                    message_id=message_id,
                    lxmf=index.get("lxmf", False),
                    head=head,
                    timestamp=index.get("timestamp"),
                    title=title,
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

            filename = urlsafe_b64encode(index.message_id).replace(b"=", b"").decode()
            try:
                with open(self._mail_path / filename, "rb") as f:
                    raw = f.read()
            except Exception:
                raw = b"<missing message>"

            results.append(index.as_stored_message(raw))

        return results

    def get_all_tags(self) -> dict[int, str]:
        """The TAG_LIST Collection: every tag id and its name, Server-Defined Tags included"""
        data = self._load_json_dict(self._tag_list_path)
        return {int(key): value for key, value in data.items()}

    def get_message_tags(self, message_ids: Sequence[bytes]) -> dict[bytes, set[int]]:
        """The tag ids on each requested message; every requested id is a key, empty for untagged and unknown ids"""
        tag_pairs: list[tuple[str, int]] = self._load_json_dict(self._message_tag_path).get("tags", [])

        message_tags: dict[bytes, set[int]] = {message_id: set() for message_id in message_ids}
        for message_id, tag_id in tag_pairs:
            message_id_bytes = bytes.fromhex(message_id)
            if message_id_bytes in message_tags:
                message_tags[message_id_bytes].add(tag_id)
        return message_tags

    def get_messages_with_tags(self, tag_ids: Sequence[int]) -> dict[int, set[bytes]]:
        """The message ids carrying each requested tag; every requested id is a key, empty for unused and unknown ids"""
        tag_pairs: list[tuple[str, int]] = self._load_json_dict(self._message_tag_path).get("tags", [])

        tag_messages: dict[int, set[bytes]] = {tag_id: set() for tag_id in tag_ids}
        for message_id, tag_id in tag_pairs:
            if tag_id in tag_messages:
                tag_messages[tag_id].add(bytes.fromhex(message_id))
        return tag_messages

    def get_message_metadata(self, message_ids: Sequence[bytes]) -> dict[bytes, dict[Any, Any]]:
        """Each requested message's metadata map; every requested id is a key, bare and unknown ids with an empty map"""
        metadata = self._load_json_dict(self._metadata_path)
        return {message_id: dict(metadata.get(message_id.hex(), {})) for message_id in message_ids}

    def get_current_token(self, collection: int) -> bytes:
        """The Collection's State Token; the Initial State Token if it has never changed"""
        tokens = self._load_json_dict(self._token_path)
        token: str | None = tokens.get(str(collection))
        return INITIAL_STATE_TOKEN if token is None else bytes.fromhex(token)

    def get_entries_since(self, collection: int, token: bytes) -> list[LogEntry] | None:
        """
        The change-log entries after `token`, oldest first.

        Returns `[]` when `token` is the Collection's current token, and `None` when the token
        is not one this store can still answer for — pruned, foreign, or never issued.
        """

        if token == self.get_current_token(collection):
            return []

        # logs = self._load_json_dict(self._log_path)

        # log: list[dict[str, Any]] = logs.get(str(collection), [])
        # for index, entry in enumerate(log):
        #     if entry.get("token_before") == token:
        #         items = list(log)[index:]
        #         return [
        #             LogEntry(
        #                 token_before=bytes.fromhex(item["token_before"]),
        #                 # ok this one doesn't make any sense lol
        #                 # todo: make this reasonable
        #                 priors=...,
        #             )
        #             for item in items
        #         ]

        return None

    def apply(self, changes: ChangeSet) -> None:
        """Apply one write's mutations"""

        # TODO this one is maybe a bit complex
        # probably separate applications would be better

        messages = self._load_json_dict(self._mail_list_path)
        metadata = self._load_json_dict(self._metadata_path)
        tag_pairs: set[tuple[str, int]] = {
            (message_id, tag_id) for message_id, tag_id in self._load_json_dict(self._message_tag_path).get("tags", [])
        }
        tags = self._load_json_dict(self._tag_list_path)
        tokens = self._load_json_dict(self._token_path)

        for message in changes.messages_added:
            messages[message.message_id.hex()] = {
                "head": message.head.hex() if message.head is not None else None,
                "title": message.title.hex() if message.title is not None else None,
                "timestamp": message.timestamp,
                "lxmf": message.lxmf,
            }
            with open(self._mail_path / message.message_id.hex(), "wb") as f:
                f.write(message.raw)

        for message_id in changes.message_ids_deleted:
            messages.pop(message_id.hex(), None)
            metadata.pop(message_id.hex(), None)
            tag_pairs = {pair for pair in tag_pairs if pair[0] != message_id.hex()}

        tags.update({str(key): value for key, value in changes.tags_created.items()})
        tags.update({str(key): value for key, value in changes.tags_renamed.items()})
        for tag_id in changes.tag_ids_deleted:
            tags.pop(str(tag_id), None)
            tag_pairs = {pair for pair in tag_pairs if pair[1] != tag_id}

        tag_pairs.update([(message_id.hex(), tag_id) for message_id, tag_id in changes.tag_pairs_added])
        tag_pairs.difference_update([(message_id.hex(), tag_id) for message_id, tag_id in changes.tag_pairs_removed])

        for message_id, entries in changes.metadata_set.items():
            metadata.setdefault(message_id.hex(), {}).update(entries)
        for message_id, keys in changes.metadata_keys_removed.items():
            item_metadata: dict[str, Any] = metadata.get(message_id.hex(), {})
            for key in keys:
                item_metadata.pop(key, None)

        tokens.update({str(collection_id): token.hex() for collection_id, token in changes.new_tokens.items()})

        # for collection, entry in changes.log_entries.items():
        #     self._logs.setdefault(collection, deque(maxlen=self._log_limit)).append(entry)

        with open(self._mail_list_path, "w") as f:
            f.write(json.dumps(messages))
        with open(self._metadata_path, "w") as f:
            f.write(json.dumps(metadata))
        with open(self._message_tag_path, "w") as f:
            json.dump({"tags": [list(pair) for pair in tag_pairs]}, f)
        with open(self._tag_list_path, "w") as f:
            json.dump(tags, f)
        with open(self._token_path, "w") as f:
            json.dump(tokens, f)
