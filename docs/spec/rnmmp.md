# The Reticulum Network Mail Management Protocol

## Overview

The Reticulum Network Mail Management Protocol (rnmmp — stylized all lowercase because I think the run of similar letter shapes is funny; "RNMMP" is acceptable) is a protocol for managing a collection of messages (a "mailbox") on a remote server over the [Reticulum Network](https://reticulum.network/manual/index.html).
The primary problem this protocol is designed to solve is to allow a user on the Reticulum Network to manage a singular [LXMF](https://github.com/markqvist/lxmf) delivery point that represents *them*, rather than a delivery point per device they wish to use.

### Status

This protocol is an **early draft** and is expected to undergo heavy and breaking changes during its implementation.

### Consideration of other solutions

Other solutions to the problem were considered, but ultimately deemed insufficient for our needs.
Specifically, we had considered serving [IMAP](https://www.rfc-editor.org/info/rfc9051/) over a Reticulum link, and we had considered using the [POPR](https://github.com/faragher/POPR) protocol. This section is to explain why we are using an entirely new protocol.

While we concluded that these protocols do not meet the needs for our problem, they each provided insights that guided the construction of this protocol. This project would not exist without their work.

#### Why not POPR?

[Post Office Protocol — Reticulum (POPR)](https://github.com/faragher/POPR) is a proposed protocol for Reticulum for providing a [POP](https://www.rfc-editor.org/info/rfc1939/)-style interface over Reticulum, adding features such as allowing authentication using Reticulum `PROOF` packets.

While POP was chosen for its simplicity, I believe the choice was misguided.
POPR opts to allow for only a single mailbox per POPR destination, to encourage decentralization
— a restriction we have agreed with and brought into rnmmp.
However, POP was largely created to *allow* for centralization of mail delivery.
POP expects clients will be storing long-term mail on their own machines,
and was intended to be used via a list-fetch-delete flow,
transferring mail from the central shared server to their singular own device.
This is essentially the *inverse* of the problem we are hoping to solve.

#### Why not IMAP?

[The Internet Message Access Protocol (IMAP)](https://www.rfc-editor.org/info/rfc9051/) is a tried-and-trusted protocol for managing a mailbox over the internet. It uses a simple text-based API, and is flexible in its link and authentication requirements.

It is entirely reasonable that we could carry IMAP over Reticulum and implement an extension to `AUTHENTICATE` that uses the built-in Reticulum link authentication. I do not think this is a *bad* solution, and you may want to consider the possibility of modifying an existing IMAP server to have these capabilities if you want the security of using server software that's had far more field testing than rnmmp.

Our main reason for not using IMAP over Reticulum mostly comes down to protocol *specificity*.
IMAP has specific functionality for managing MIME messages, whereas we expect to be using LXMF.
IMAP communicates using line-based text in a format not-required-to-be-but-optimized-for *TCP/IP*, whereas Reticulum APIs tend to be designed around *binary* formats and structured in formats such as [msgpack](https://msgpack.org/).

### Conventions

- In examples, `C:` and `S:` are used to indicate exchanges sent by the client and server, respectively.
  - `C[h]:` and `S[h]:` show the binary data exchanged in hexadecimal format. For example, `C[h]:91 0a` shows a client sending the raw bytes 0x91 and 0x0a
  - `C[m]:` and `S[m]:` show the data exchanged in msgpack format after conversion to JSON. For example, `S[m]:[10]` shows a server sending an array containing a 10 in msgpack format, which corresponds to the raw bytes 0x91 and 0x0a
- "User" is used to refer to a human user, whereas "client" is used to refer to software run by the user.
- The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and "OPTIONAL" when appearing in all caps hold their meanings as assigned in [BCP 14](https://www.rfc-editor.org/info/rfc2119)

## Protocol overview

### Connection

RNMMP offers two connection modes, and an rnmmp server SHOULD implement both.

The "Link" mode uses a raw Reticulum [Link Destination](https://reticulum.network/manual/understanding.html#destinations) to provide an active connection.

The "Single" mode uses the Content part of an LXMF message to carry Exchanges asynchronously.

The Link and Single modes differ in how data is transferred and in how the client is authenticated, but otherwise operate identically. See the "Exchange" and "Authentication" sections for more information.

#### Link mode transport

In Link mode the client establishes a Reticulum Link to the server's Destination, and either peer sends Exchanges over that Link.

An Exchange that fits within a single link packet MAY be sent as one packet whose payload is exactly one encoded Exchange.
An Exchange that does not fit MUST instead be sent as a Reticulum Resource over the Link, whose transferred data is exactly one encoded Exchange.
A sender MAY use a Resource for an Exchange of any size.
A receiver MUST accept Exchanges arriving by either carriage, and so MUST accept incoming Resources on the Link.

An Exchange MUST NOT be split across link packets, and a packet or Resource MUST NOT carry more than one Exchange.

Link packets are not retransmitted by Reticulum.
A Request is acknowledged by its Response: a client that receives no Response in a reasonable time MAY send the same Request again, unchanged.
Notifications are not acknowledged, as described in the "Notifications" section.

#### Single mode transport

In Single mode each Exchange is carried as the Content of a signed LXMF message, as described in the "Authentication" section.
The Content of such a message MUST be exactly one encoded Exchange.

### Exchange

An Exchange is some unit of information transferred between the client and the server.
This can include Requests, Responses, and Notifications.

All exchanges are formatted as a [msgpack](https://msgpack.org/) array, with the first item being an integer acting as the exchange type:

| Exchange Type | Code |
|---------------|------|
| Request       | 0    |
| Response      | 1    |
| Notification  | 2    |

Additional items in the array are Parameters carrying the content of the Exchange.

Implementations MUST ignore Exchanges with types they do not recognize.
Implementations MUST reject Exchanges with missing required parameters.
Implementations SHOULD accept Exchanges with unexpected additional parameters.

When a received Exchange cannot be parsed, or is rejected as malformed, the receiver SHOULD answer with a `BAD` Response carrying the General Error `MALFORMED` — but only when the Exchange is identifiable as a Request with a readable Request id.
When no Request id is recoverable the receiver MUST NOT create one: in Link mode the Exchange MUST be discarded silently, while in Single mode the receiver MAY still respond using Request id `0`, since Single mode Responses are matched by `FIELD_REPLY_TO` rather than by Request id.

#### Requests

A Request is an Exchange for which a Response is expected.

The format of a Request consists of at least two Parameters.

The first parameter is an integer serving as the "Request id".
This helps match a Response back to the Request that initiated it.

When using the Link connection mode, each Request SHOULD use a Request id the sender has not yet used.
The receiver MUST accept requests with duplicate IDs.

When using the Single mode, responses can be matched to requests without needing the Request id; the Request id SHOULD be set to `0` in this case.

The second Parameter is the request type, indicating an action the sender wants the receiver to complete.
See section "Request types" for more information.

Additional parameters may be required as defined by the request type.
The first of these additional parameters, if any additional parameters are provided,
will always be a map containing additional non-positional arguments,
with meanings assigned according to request type — referred to as Keyed Parameters.
A sender SHOULD NOT include keys in this parameter that are not defined in this spec.
A receiver MUST accept and ignore keys in this parameter it does not expect.
Additional parameters after the Keyed Parameter map are referred to as Positional Parameters.

When any additional parameters are supplied, the first MUST be the Keyed Parameter map itself: a Request whose first additional parameter is not a map is malformed. `nil` MUST NOT be supplied in place of an empty Keyed Parameter map.
A sender MAY omit trailing Optional parameters, and MAY supply `nil` in place of an Optional parameter it does not use; a receiver MUST treat an explicitly `nil` parameter, keyed or positional, as absent.

#### Responses

A Response is an Exchange returning information requested by a Request.

The format of a Response consists of at least two parameters.

The first parameter is the Request id.
This value MUST be the same as the one included in the request.

The second parameter is the status code:

| Status | Code | Description                                                                    |
|--------|------|--------------------------------------------------------------------------------|
| OK     | 0    | The action was performed                                                       |
| NO     | 1    | The request was understood, but was either ignored or an error was encountered |
| BAD    | 2    | The request was not understood                                                 |

For the OK status, zero or more additional Return Parameters are REQUIRED as defined for the Request Type.

For the NO and BAD statuses, one additional Parameter MAY be supplied.
If supplied, the parameter MUST be a map.
The map MAY include implementation-defined key-value pairs indicating information about why the request failed.
Integer keys from 0-127 are reserved and MUST NOT be used except as defined in this spec.
The usage of the reserved keys is outlined in the "Error information" section.

When using the Single connection mode,
Responses MUST be sent to the destination listed as the Source in the LXMF packet,
and MUST use the LXMF `FIELD_REPLY_TO` to indicate the message-id of the request.

#### Notifications

A Notification is an Exchange for which a response is not expected.

The format of a Notification consists of at least one Parameter indicating the event type that triggered the notification.
Each event type defines the format of additional Parameters expected.

### Authentication

All Exchanges MUST be made with the sender authenticated.
The method of authentication differs between connection modes.

A receiver MUST NOT process any Exchanges received with missing or invalid authentication.

Clients and servers SHOULD define a list of identities they expect to receive Exchanges from and ignore Exchanges received from unexpected senders.

#### Link mode

For Link mode Exchanges, the client MUST authenticate using a Reticulum `PROOF` packet.
The server is authenticated automatically during the establishment of the Reticulum link.

The server MAY save some Exchanges received from an unauthenticated client and process them after the client has authenticated.
When using this behaviour, the server MAY choose to save or discard Exchanges individually per Exchange, but MUST discard all unprocessed exchanges when the link closes.

#### Single mode

For Single mode Exchanges, the sender (client or server) MUST authenticate by including the Exchange as the Content of a valid (signed) LXMF message. The receiver MUST NOT process any Exchange with an invalid signature.

### Error information

The following integer keys have been given specific meanings when used
in the error information map returned in a NO/BAD Response.

| Code | Name           | Value                                           | Description                                                                            |
|------|----------------|-------------------------------------------------|----------------------------------------------------------------------------------------|
| 0    | GENERAL_ERROR  | Int (See "General error codes" section)         | The request was rejected for a generally-applicable reason                             |
| 1    | SPECIFIC_ERROR | Int (See specific error table per request type) | An error code returned as defined by the request type spec                             |
| 2    | ERROR_MESSAGE  | String                                          | An implementation-defined user-facing error message                                    |
| 3    | ERROR_DETAILS  | Map                                             | A map of error details as specified for the combination of Request Type and Error Type |

#### General error codes

Values for the `GENERAL_ERROR` key in error information

| Code | Name            | Description                                                                                    |
|------|-----------------|------------------------------------------------------------------------------------------------|
| 0    | UNAUTHENTICATED | MAY be returned to an unauthenticated client instead of silently ignoring a request            |
| 1    | UNAUTHORIZED    | MAY be returned to a client with an unexpected identity instead of silently ignoring a request |
| 2    | INCOMPLETE      | Request is missing required information                                                        |
| 3    | WRONG_TYPE      | A Request included a field with an unexpected datatype                                         |
| 4    | UNSUPPORTED     | The server understands the request, but has not implemented the functionality                  |
| 5    | TOO_LARGE       | The server refuses to process the request because it exceeds size limits or storage space      |
| 6    | SERVER_ERROR    | The server encountered an error while processing the request and could not continue            |
| 7    | STATE_MISMATCH  | The client expected the mailbox to be in a state it was not found to be in                     |
| 8    | MALFORMED       | The request was malformed and could not be parsed                                              |

## Mailbox state

RNMMP supports updating a client based on changes that have occurred since the client's last known state.
These states are organized into Collections, with each Collection being associated with a server-defined State Token.
The State Token MUST be represented in msgpack using the Bin type family.

Whenever a Collection's state is updated, the server MUST create a new State Token to represent it.
State Tokens are to be interpreted as opaque and MUST NOT be parsed by the client; only used raw.

The zero-length byte array (msgpack `0xC4 0x00`) is reserved to represent the "Initial State".
A client can use the Initial State Token as its last known State Token to indicate that it
requires the full current state of the Collection, rather than the delta from some known state.

Collections are represented by integers.
The integers 0-127 inclusive are reserved for standard Collections.
Extensions MAY use integers outside of this range for other Collections.

| Collection name | Code | Description                                             |
|-----------------|------|---------------------------------------------------------|
| MAIL_LIST       | 0    | The list of messages in the mailbox                     |
| TAG_LIST        | 1    | The list of tag names defined in the mailbox            |
| MESSAGE_TAG     | 2    | The list of tags applied to each message in the mailbox |
| METADATA        | 3    | The non-tag metadata for each message in the mailbox    |

Note that message *content* is immutable.
A client's stored mailbox state does not need to include it.

The `METADATA` Collection is keyed by ids from the `MAIL_LIST` Collection,
and the `MESSAGE_TAG` Collection is keyed by ids from the `MAIL_LIST` and `TAG_LIST` Collections.
The client MUST tolerate items in these Collections referring to ids not known to exist in other Collections.

### Requests that mutate state

All requests that mutate state (UPLOAD, DELETE, CREATE_TAG, etc.) modify the mailbox.
They share the behaviour described here, in addition to behaviour described in the "Request types" section.

Some write requests involve more than one state change.
A write request MUST be applied atomically: either every change it requested is applied, or none are.
If the server returns a `NO` or `BAD` response, the client MUST be able to assume nothing was changed.

`IF_IN_STATE` and Updated States defined below apply to every request that mutates state,
whether or not that request's own section mentions them explicitly.
Where such a request defines Keyed or Return Parameters of its own,
these shared entries are repeated alongside them for clarity.

**Keyed Parameters**

| Key | Name        | Type              | Optional? | Description                                                                                   |
|-----|-------------|-------------------|-----------|-----------------------------------------------------------------------------------------------|
| 0   | IF_IN_STATE | Map[Int -> Bytes] | Yes       | A map of Collection id to the State Token the client believes that Collection is currently in |

A client MAY specify `IF_IN_STATE` to prevent unexpected results when multiple clients are connected simultaneously.
If `IF_IN_STATE` is present, the server MUST compare each supplied State Token against the current State Token of the corresponding Collection *before* applying any change.
If any supplied token does not match, the server MUST reject the request, apply no changes, and SHOULD return the General Error `STATE_MISMATCH`.

When returning `STATE_MISMATCH`, the server SHOULD include an `ERROR_DETAILS` map with key `0` holding a map representing the updated Collections.
The updated Collection map should use Collection IDs as keys and those Collections' current State Tokens as values.

**Return Parameters**

| Index | Name           | Type                       | Optional? | Description                                                                            |
|-------|----------------|----------------------------|-----------|----------------------------------------------------------------------------------------|
| 0     | Updated States | Map[Int -> [Bytes, Bytes]] | No        | A map of Collection id to State Token updates, for each Collection the request changed |

On an `OK` response, a write request returns the updated State Token information of every Collection that had a state change.
A Collection whose state did not change MUST NOT appear.
A request that changed nothing (for example, deleting ids that were already absent) MUST return an empty map.

The values in the Updated States map are 2-tuples holding the previous State Token and new State Token.
If the first value in the tuple matches the client's last known state,
the client SHOULD update their last known state to the second value in the tuple.
If the first value in the tuple *does not* match the client's last known state,
the client MUST NOT update their last known state, and SHOULD mark their state as stale and requiring a sync.

### MAIL_LIST

The MAIL_LIST Collection is a set of IDs representing mail items in the mailbox.

When determining the MAIL_LIST Collection State, this list MUST be considered unordered.
Duplicate values MUST NOT appear in this list.

For LXMF messages, the id value SHOULD be the LXMF message-id.
Identical LXMF messages SHOULD be considered the same message.

For non-LXMF messages, the id SHOULD be a universally unique id such as UUID.
The server MAY deduplicate identical non-LXMF messages, in which case the id MAY be derived from the text.

### TAG_LIST

The TAG_LIST Collection is a set of names assigned to integer IDs.

Tags with negative IDs are Server-Defined Tags, which are a static list the client cannot change.
New Server-Defined Tags MAY be added by an implementation, but SHOULD NOT be removed.

Tags with IDs from -32 to -1 inclusive are reserved for definition within the rnmmp specification.
See the table below for a list of these standard tags.
Many of these standard tags are intended to be managed by the server and client automatically
to track basic information about a message.

| ID  | Name              | Description                                                                    | Suggested automatic management                                                                                                                                 |
|-----|-------------------|--------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| -1  | UNREAD            | User has not opened the message                                                | Set by server on receipt, removed by client when the user opens the message                                                                                    |
| -2  | RESPONDED         | User has responded to the message                                              | Set by client when sending a message in response to the tagged message                                                                                         |
| -3  | IMPORTANT         | The message has been flagged as important                                      | May be set by some implementation-defined metric by the client or server, though typically managed manually by the user                                        |
| -4  | TRASH             | The message has been staged for deletion, but has not yet been deleted         | Client can default to setting this tag instead of deleting a message when requested by the user, server may use this flag to inform automatic message deletion |
| -5  | OUTBOX            | The message was sent using a SEND_\* or UPLOAD command                         | Set based on the source of the message                                                                                                                         |
| -6  | DRAFT             | The message was uploaded using an UPLOAD command with the intent to edit later | Client may set this tag to save user drafts to the server and distinguish them from other uploads                                                              |
| -7  | FORWARDED         | The message has been forwarded to another server                               | Set on a received message when that message is then forwarded                                                                                                  |
| -8  | JUNK              | The message is junk mail or otherwise highly unimportant                       | Typically set by some implementation-defined metric by the client or server, though may be managed manually by the user                                        |
| -9  | SUSPICIOUS        | The message contains suspicious content, such as phishing attempts             | Typically set by some implementation-defined metric by the client or server, though may be managed manually by the user                                        |
| -10 | DELIVERED         | A message in the outbox is known to have been delivered                        | Set by the server upon receiving delivery confirmation                                                                                                         |
| -11 | UNVERIFIED_SENDER | The sender of the message could not be verified                                | Set by the server on receipt when the sender's signature could not be validated for any reason                                                                 |

Users can also define their own tags, which are assigned to positive IDs by the server.

### MESSAGE_TAG

The MESSAGE_TAG Collection is a set of [Message ID, Tag ID] pairs indicating tags present on a message.

When determining the MESSAGE_TAG Collection State, this list MUST be considered unordered.
Duplicate values MUST NOT appear in this list.

When messages or tags are deleted, any MESSAGE_TAG entries referencing them SHOULD be removed.

### METADATA

The METADATA Collection is a map from Message IDs to Metadata Maps.

When determining the METADATA Collection State, this map MUST be considered unordered.
The Metadata Map keys MUST be considered unordered, and SHOULD NOT contain duplicates.

Metadata Map keys are integers or strings. Integer keys between 0 and 127 inclusive are reserved for definition within the rnmmp specification.

| ID | Name         | Type      | Description                              |
|----|--------------|-----------|------------------------------------------|
| 0  | RECEIVE_TIME | Timestamp | The time the server received the message |

A Timestamp is a value of the msgpack timestamp extension type (ext type -1): seconds since the Unix epoch, with optional nanosecond precision, always representing an absolute UTC instant.

## Request types

Request types are represented by an integer code.
The numbers from 0-127 inclusive are reserved for official request types.
Numbers outside of this range MAY be used for implementation-defined request types.
The following request types SHOULD be supported by the server;
the server MAY opt to return a `NO` response with `GENERAL_ERROR` = `UNSUPPORTED` for any request.

| Code | Name               | Description (see subsections for specification)                                                   |
|------|--------------------|---------------------------------------------------------------------------------------------------|
| 0    | NOOP               | No action to be performed, MAY be sent periodically to keep a link alive                          |
| 1    | CAPABILITY         | Fetch information about the server's supported features                                           |
| 2    | SUBSCRIBE          | Indicate that the client would like to receive active updates regarding a Collection state        |
| 3    | UNSUBSCRIBE        | Indicate that the client would like to stop receiving active updates regarding a Collection state |
| 4    | LIST_SUBSCRIPTIONS | List active subscriptions for Single Mode destinations                                            |
| 5    | SYNC               | Get the delta for a Collection from a given State Token                                           |
| 6    | FETCH_FULL         | Fetch raw stored messages                                                                         |
| 7    | FETCH_HEAD         | Fetch the Destination, Source, and Signature fields of stored LXMF messages                       |
| 8    | FETCH_PAYLOAD      | Fetch the Payload portion of stored LXMF messages                                                 |
| 9    | FETCH_CONTENT      | Fetch the Content portion of stored messages                                                      |
| 10   | FETCH_FIELDS       | Fetch the Fields portion of stored LXMF messages                                                  |
| 11   | FETCH_TIMESTAMP    | Fetch the Timestamp portion of stored messages                                                    |
| 12   | FETCH_TITLE        | Fetch the Title portion of stored messages                                                        |
| 13   | FETCH_TAGS         | Fetch the Tags present on messages                                                                |
| 14   | FETCH_METADATA     | Fetch the Metadata present on messages                                                            |
| 15   | SEARCH_TITLE       | Search messages by the Title portion                                                              |
| 16   | SEARCH_CONTENT     | Search messages by the Content portion                                                            |
| 17   | UPLOAD             | Add messages to the MAIL_LIST Collection manually outside of the built-in delivery mechanism      |
| 18   | DELETE             | Remove messages from the MAIL_LIST Collection                                                     |
| 19   | CREATE_TAG         | Add named tags to the TAG_LIST Collection                                                         |
| 20   | DELETE_TAG         | Remove named tags from the TAG_LIST Collection                                                    |
| 21   | RENAME_TAG         | Rename tags in the TAG_LIST Collection                                                            |
| 22   | ADD_TAG            | Add tags to MESSAGE_TAG Collection                                                                |
| 23   | REMOVE_TAG         | Remove tags from the MESSAGE_TAG Collection                                                       |
| 24   | SET_METADATA       | Add entries to items in the METADATA Collection                                                   |
| 25   | REMOVE_METADATA    | Remove entries from items in the METADATA Collection                                              |
| 26   | SEND_RAW           | Send a raw message from the server to another destination                                         |
| 27   | SEND_LXMF          | Send an LXMF message from the server to another destination                                       |

Further details in the subsections below.
Each section might define tables for its Keyed Parameters,
Positional Parameters, Return Parameters, and Specific Error Codes.
Any of these tables missing from a subsection indicates that the
corresponding information is expected to be unused/empty.

### NOOP

No action to be performed, MAY be sent periodically to keep a link alive.

### CAPABILITY

Request a list of optional features and extensions the server supports.

**Return Parameters**

| Index | Name            | Type                                  | Optional? | Description                               |
|-------|-----------------|---------------------------------------|-----------|-------------------------------------------|
| 0     | Capability List | List[Int OR Str OR [Int OR Str, Map]] | No        | The optional features the server supports |

#### Capability list

The server MUST respond with a msgpack list of capabilities.
Each item in the list can be an integer code, a string, or a list.
Integers are reserved for standardized optional features.
Strings MAY be used for implementation-defined extensions.
A list represents a feature-variant pair.
When a list is used, it MUST be length 2;
the first element MUST be an integer or string as defined above;
and the second element MUST be a map containing variant information as defined for the feature code.

The first element of the Capability List MUST be a protocol version,
which will be incremented when breaking changes are made to the spec.
Currently, the only version code supported is `1`.

### SUBSCRIBE

Indicate that the client would like to receive active updates regarding a Collection state.

**Positional Parameters**

| Index | Name        | Type  | Optional? | Description                                                  |
|-------|-------------|-------|-----------|--------------------------------------------------------------|
| 0     | Collection  | Int   | No        | The Collection to subscribe to updates for                   |
| 1     | Destination | Bytes | Yes       | A Reticulum Destination to send LXMF Update Notifications to |

**Specific Error Codes**

| Code | Name                | Description                                                                                                        |
|------|---------------------|--------------------------------------------------------------------------------------------------------------------|
| 0    | UNKNOWN_COLLECTION  | Server does not have a Collection with the requested id                                                            |
| 1    | UNKNOWN_DESTINATION | Server refuses to send LXMF notifications to the requested Destination because it does not recognize it as trusted |
| 2    | NO_PASSIVE_NOTIFS   | Server refuses to send LXMF notifications, only supporting notifications over an active link                       |
| 3    | REFUSED             | Server refuses to send notifications as requested for unspecified/other reasons                                    |

If accepted, the server SHOULD begin to send COLLECTION_UPDATE Notifications whenever the State Token for the specified Collection changes.
The server MAY delay sending COLLECTION_UPDATE Notifications in order to "batch" multiple changes and send a single update for settled state.

If Destination is specified, these notifications will be sent in Single mode as LXMF to the specified Reticulum Destination.

If Destination is not specified, notifications will be sent to the client making the request:
- If the request was made in Single mode, notifications will be sent in Single mode as LXMF to the request's Source Destination.
- If the request was made in Link mode, notifications will be sent in the active link; these updates will be automatically unsubscribed when the link closes.

### UNSUBSCRIBE

Indicate that the client would like to stop receiving active updates regarding a Collection state.

**Positional Parameters**

| Index | Name        | Type  | Optional? | Description                                                 |
|-------|-------------|-------|-----------|-------------------------------------------------------------|
| 0     | Collection  | Int   | No        | The Collection to unsubscribe from updates for              |
| 1     | Destination | Bytes | Yes       | A Reticulum Destination where notifications were being sent |

Request to stop receiving COLLECTION_UPDATE Notifications requested via the SUBSCRIBE command.
Positional parameters are understood the same as with the SUBSCRIBE command.

### LIST_SUBSCRIPTIONS

List active subscriptions for Single Mode destinations.

**Return Parameters**

| Index | Name        | Type               | Optional? | Description                                                                                                             |
|-------|-------------|--------------------|-----------|-------------------------------------------------------------------------------------------------------------------------|
| 0     | Subscribers | List[[Int, Bytes]] | No        | A list of [Collection id, Reticulum Destination] pairs for currently active Single Mode COLLECTION_UPDATE Notifications |

### SYNC

Get the delta for a Collection from a given State Token.

**Positional Parameters**

| Index | Name             | Type  | Optional? | Description                                                                              |
|-------|------------------|-------|-----------|------------------------------------------------------------------------------------------|
| 0     | Collection id    | Int   | No        | The id of the Collection to request a Delta for                                          |
| 1     | Last Known State | Bytes | No        | The client's last known State Token for the Collection, for a delta to be generated from |

**Return Parameters**

| Index | Name  | Type  | Optional? | Description                                                                                 |
|-------|-------|-------|-----------|---------------------------------------------------------------------------------------------|
| 0     | Delta | Map   | No        | Structured details about the changes to the Collection since the specified Last Known State |
| 1     | State | Bytes | No        | The Collection's current State Token, which the Delta brings the client up to date with     |

**Specific Error Codes**

| Code | Name               | Description                                                                                                                |
|------|--------------------|----------------------------------------------------------------------------------------------------------------------------|
| 0    | UNKNOWN_COLLECTION | Server does not have a Collection with the requested id                                                                    |
| 1    | UNKNOWN_STATE      | Server cannot generate a delta from the given state to the current state. Client SHOULD retry with the Initial State Token |

After applying the Delta, the client SHOULD adopt the returned State as its last known State Token for the Collection.

The exact format of the Delta Return Parameter depends on the Collection type.

A Delta SHOULD be minimal, omitting changes for Collection items that have returned to the Last Known State.
For example, if a tag was named `A` at the client's Last Known State, and was later renamed `B` and then `A` again, the composed rename from `A` to `A` SHOULD be omitted.
A server MAY include these redundant entries, and a client MUST apply them as no-ops.

#### MAIL_LIST Delta

The MAIL_LIST Delta has two keys, ADDED (`0`) and DELETED (`1`).
Each key's value is a list of Message IDs,
where ADDED is a complete list of messages that did not exist in the Last Known State but now do,
and DELETED is a complete list of messages that existed in the Last Known State but now do not.

A Message id MUST NOT appear in both the ADDED and DELETED lists.
I.e., if a message was added and then deleted since the Last Known State, it should not appear in the delta.

Messages that have the same existence state as the Last Known State SHOULD NOT appear.

#### TAG_LIST Delta

The TAG_LIST Delta uses Tag IDs as keys, and includes information about that tag as the value.
For tags that have been created or renamed since the Last Known State, the value is a String representing the current name.
For tags that have been deleted since the Last Known State, the value is `nil`.

Intermediary states MUST NOT be represented.
I.e., if a tag is renamed multiple times, only the current name is shown;
and if a tag is deleted and a new tag with the same id is created, the Delta is shown the same as if the tag was renamed.

Tag IDs that have the same name as in the Last Known State SHOULD NOT appear.

#### MESSAGE_TAG Delta

The MESSAGE_TAG Delta uses Message IDs as keys, and includes information about the message's current tags as the value.
For messages that have had tags added or removed, the value is the list of current Tag IDs.
For messages that have been deleted, the value is `nil`.

Intermediary states MUST NOT be represented.
I.e., if a tag is added and then removed, its addition MUST NOT be reported.

If a message has the same set of tags as in the Last Known State, it SHOULD NOT appear in the Delta.
The set of tags does not have an order; if the server represents tags in an order,
it MUST consider a reordering of tags as being the same set of tags.

#### METADATA Delta

The METADATA Delta uses Message IDs as keys, and includes information about the message's current metadata as the value.
For messages that have had Metadata changed, the value is the message's current metadata map.
For messages that have been deleted, the value is `nil`.

Intermediary states MUST NOT be represented.
I.e., if a metadata field is added and then removed, it MUST NOT be included in the Delta.

If a message has the same metadata as the Last Known State, it SHOULD NOT appear in the Delta.
A server MAY consider a Map as ordered or unordered when determining if an update needs to be reported.

### FETCH_FULL

Fetch raw stored messages including all LXMF headers.

**Positional Parameters**

| Index | Name        | Type        | Optional? | Description                      |
|-------|-------------|-------------|-----------|----------------------------------|
| 0     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type               | Optional? | Description                                                                                            |
|-------|----------|--------------------|-----------|--------------------------------------------------------------------------------------------------------|
| 0     | Messages | List[Bytes OR nil] | No        | The messages, returned in the same order requested. For messages that aren't found, `nil` is returned. |

The Messages Return Parameter is a list of raw message data returned byte-for-byte as delivered.
The server MUST return `nil` for any requested id that does not exist in the MAIL_LIST Collection.

Fetch responses can be large.
A server MAY refuse a request that selects too many messages, or whose response would be too large,
with a `NO` response and `GENERAL_ERROR` = `TOO_LARGE`; a client SHOULD then retry with fewer ids.
The server SHOULD utilize Reticulum Resources for large responses.

### FETCH_HEAD

Fetch the Destination, Source, and Signature fields of stored LXMF messages.

**Positional Parameters**

| Index | Name        | Type        | Optional? | Description                      |
|-------|-------------|-------------|-----------|----------------------------------|
| 0     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type               | Optional? | Description                                                                                                   |
|-------|----------|--------------------|-----------|---------------------------------------------------------------------------------------------------------------|
| 0     | Messages | List[Bytes OR nil] | No        | The message headers, returned in the same order requested. For messages that aren't found, `nil` is returned. |

The Messages Return Parameter is a list of each message's header as a Bytes value,
containing the Destination, Source, and Signature portions of the LXMF message:

| Index | Name        | Type  | Description                                  |
|-------|-------------|-------|----------------------------------------------|
| 0     | Destination | Bytes | The destination hash the message was sent to |
| 1     | Source      | Bytes | The source hash the message was sent from    |
| 2     | Signature   | Bytes | The signature on the message                 |

The server MUST return `nil` for any requested id that does not exist in the MAIL_LIST Collection,
or for which the stored message is not in LXMF.

Fetch responses can be large.
A server MAY refuse a request that selects too many messages, or whose response would be too large,
with a `NO` response and `GENERAL_ERROR` = `TOO_LARGE`; a client SHOULD then retry with fewer ids.
The server SHOULD utilize Reticulum Resources for large responses.

### FETCH_PAYLOAD

Fetch the Payload portion of stored LXMF messages.

**Positional Parameters**

| Index | Name        | Type        | Optional? | Description                      |
|-------|-------------|-------------|-----------|----------------------------------|
| 0     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type               | Optional? | Description                                                                                                    |
|-------|----------|--------------------|-----------|----------------------------------------------------------------------------------------------------------------|
| 0     | Messages | List[Bytes OR nil] | No        | The message payloads, returned in the same order requested. For messages that aren't found, `nil` is returned. |

The Messages Return Parameter is a list of each message's packed payload as a Bytes value: every byte after the head returned by FETCH_HEAD.
This should be the msgpack encoding of the message's `[Timestamp, Title, Content, Fields]` returned raw.

The server MUST return `nil` for any requested id that does not exist in the MAIL_LIST Collection,
or for which the stored message is not in LXMF.

Fetch responses can be large.
A server MAY refuse a request that selects too many messages, or whose response would be too large,
with a `NO` response and `GENERAL_ERROR` = `TOO_LARGE`; a client SHOULD then retry with fewer ids.
The server SHOULD utilize Reticulum Resources for large responses.

### FETCH_CONTENT

Fetch the Content portion of stored messages.

**Positional Parameters**

| Index | Name        | Type        | Optional? | Description                      |
|-------|-------------|-------------|-----------|----------------------------------|
| 0     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type               | Optional? | Description                                                                                                    |
|-------|----------|--------------------|-----------|----------------------------------------------------------------------------------------------------------------|
| 0     | Messages | List[Bytes OR nil] | No        | The message contents, returned in the same order requested. For messages that aren't found, `nil` is returned. |

The Messages Return Parameter is a list of each message's Content as a Bytes value, decoded from the payload.

The server MUST return `nil` for any requested id that does not exist in the MAIL_LIST Collection.
For messages not stored as LXMF, the full content is returned as with FETCH_FULL.

Fetch responses can be large.
A server MAY refuse a request that selects too many messages, or whose response would be too large,
with a `NO` response and `GENERAL_ERROR` = `TOO_LARGE`; a client SHOULD then retry with fewer ids.
The server SHOULD utilize Reticulum Resources for large responses.

### FETCH_FIELDS

Fetch the Fields portion of stored LXMF messages.

**Positional Parameters**

| Index | Name        | Type        | Optional? | Description                      |
|-------|-------------|-------------|-----------|----------------------------------|
| 0     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type             | Optional? | Description                                                                                                  |
|-------|----------|------------------|-----------|--------------------------------------------------------------------------------------------------------------|
| 0     | Messages | List[Map OR nil] | No        | The message fields, returned in the same order requested. For messages that aren't found, `nil` is returned. |

The Messages Return Parameter is a list of each message's Fields as a Map, decoded from the payload.

The server MUST return `nil` for any requested id that does not exist in the MAIL_LIST Collection,
or for which the stored message is not in LXMF.

Fetch responses can be large.
A server MAY refuse a request that selects too many messages, or whose response would be too large,
with a `NO` response and `GENERAL_ERROR` = `TOO_LARGE`; a client SHOULD then retry with fewer ids.
The server SHOULD utilize Reticulum Resources for large responses.

### FETCH_TIMESTAMP

Fetch the Timestamp portion of stored messages.

**Positional Parameters**

| Index | Name        | Type        | Optional? | Description                      |
|-------|-------------|-------------|-----------|----------------------------------|
| 0     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type                   | Optional? | Description                                                                                                      |
|-------|----------|------------------------|-----------|------------------------------------------------------------------------------------------------------------------|
| 0     | Messages | List[Timestamp OR nil] | No        | The message timestamps, returned in the same order requested. For messages that aren't found, `nil` is returned. |

The Messages Return Parameter is a list of each message's reported Timestamp, as the Timestamp type described in the "METADATA" section.
For an LXMF message this is the LXMF message timestamp.
For a message in another format, it MAY be derived according to the format's semantics.
For example, a server could opt to derive it from the `Date` header of a MIME message brought in by a gateway.

The server MUST return `nil` for any requested id that does not exist in the MAIL_LIST Collection, or for which no Timestamp is known.
A server MUST NOT substitute a fallback value when the message itself does not specify one.

Fetch responses can be large.
A server MAY refuse a request that selects too many messages, or whose response would be too large,
with a `NO` response and `GENERAL_ERROR` = `TOO_LARGE`; a client SHOULD then retry with fewer ids.
The server SHOULD utilize Reticulum Resources for large responses.

### FETCH_TITLE

Fetch the Title portion of stored messages.

**Positional Parameters**

| Index | Name        | Type        | Optional? | Description                      |
|-------|-------------|-------------|-----------|----------------------------------|
| 0     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type               | Optional? | Description                                                                                                  |
|-------|----------|--------------------|-----------|--------------------------------------------------------------------------------------------------------------|
| 0     | Messages | List[Bytes OR nil] | No        | The message titles, returned in the same order requested. For messages that aren't found, `nil` is returned. |

The Messages Return Parameter is a list of each message's Title as a Bytes value.
For an LXMF message this is the Title portion, decoded from the payload.
For a message in another format, it MAY be derived according to the format's semantics.
For example, a server could opt to derive it from the `Subject` header of a MIME message brought in by a gateway.

The server MUST return `nil` for any requested id that does not exist in the MAIL_LIST Collection, or for which no Title is known.
A server MUST NOT substitute a fallback value when the message itself does not specify one.

Fetch responses can be large.
A server MAY refuse a request that selects too many messages, or whose response would be too large,
with a `NO` response and `GENERAL_ERROR` = `TOO_LARGE`; a client SHOULD then retry with fewer ids.
The server SHOULD utilize Reticulum Resources for large responses.

### FETCH_TAGS

Fetch the Tags present on messages.

**Positional Parameters**

| Index | Name        | Type        | Optional? | Description                      |
|-------|-------------|-------------|-----------|----------------------------------|
| 0     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name | Type                   | Optional? | Description                                                                                                        |
|-------|------|------------------------|-----------|--------------------------------------------------------------------------------------------------------------------|
| 0     | Tags | List[List[Int] OR nil] | No        | The tags on each message, returned in the same order requested. For messages that aren't found, `nil` is returned. |

The Tags Return Parameter is a list of each message's current Tag IDs, as they appear in the MESSAGE_TAG Collection.
A message that exists but carries no tags MUST be returned as an empty list.
The server MUST return `nil` for any requested id that does not exist in the MAIL_LIST Collection.

The Tag IDs returned for a message MUST NOT contain duplicates.

Fetch responses can be large.
A server MAY refuse a request that selects too many messages, or whose response would be too large,
with a `NO` response and `GENERAL_ERROR` = `TOO_LARGE`; a client SHOULD then retry with fewer ids.
The server SHOULD utilize Reticulum Resources for large responses.

### FETCH_METADATA

Fetch the Metadata present on messages.

**Positional Parameters**

| Index | Name        | Type        | Optional? | Description                      |
|-------|-------------|-------------|-----------|----------------------------------|
| 0     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type             | Optional? | Description                                                                                                             |
|-------|----------|------------------|-----------|-------------------------------------------------------------------------------------------------------------------------|
| 0     | Metadata | List[Map OR nil] | No        | The metadata for each message, returned in the same order requested. For messages that aren't found, `nil` is returned. |

The Metadata Return Parameter is a list of each message's current Metadata Map, as it appears in the METADATA Collection.
A message that exists but carries no metadata MUST be returned as an empty map.
The server MUST return `nil` for any requested id that does not exist in the MAIL_LIST Collection.

A client MUST tolerate Metadata Map keys it does not recognize.

Fetch responses can be large.
A server MAY refuse a request that selects too many messages, or whose response would be too large,
with a `NO` response and `GENERAL_ERROR` = `TOO_LARGE`; a client SHOULD then retry with fewer ids.
The server SHOULD utilize Reticulum Resources for large responses.

### SEARCH_TITLE

Search messages by the Title portion.
Only messages with a known Title are searched.

**Positional Parameters**

| Index | Name  | Type   | Optional? | Description                           |
|-------|-------|--------|-----------|---------------------------------------|
| 0     | Query | String | No        | The text to search message Titles for |

**Keyed Parameters**

| Key | Name         | Type      | Optional? | Description                                                    |
|-----|--------------|-----------|-----------|----------------------------------------------------------------|
| 0   | MAX_RESULTS  | Int       | Yes       | The maximum number of Message IDs the client wishes to receive |
| 1   | ONLY_TAGS    | List[Int] | Yes       | Filter results to messages with the specified tag IDs          |
| 2   | EXCLUDE_TAGS | List[Int] | Yes       | Filter results to messages without the specified tag IDs       |

**Return Parameters**

| Index | Name        | Type        | Optional? | Description                                       |
|-------|-------------|-------------|-----------|---------------------------------------------------|
| 0     | Message IDs | List[Bytes] | No        | The ids of messages whose Title matches the Query |

**Specific Error Codes**

| Code | Name                | Description                                                          |
|------|---------------------|----------------------------------------------------------------------|
| 0    | CONFLICTING_FILTERS | The client specified the same tag in both ONLY_TAGS and EXCLUDE_TAGS |

The matching semantics are implementation-defined, but a server SHOULD at minimum perform a case-insensitive substring match.
The order of the returned ids is unspecified.
If MAX_RESULTS is given, the server MUST NOT return more than that many ids;
which matches are dropped when results are truncated is implementation-defined.
A server MAY additionally limit result counts, and MAY return `NO` with `GENERAL_ERROR` = `TOO_LARGE` instead of truncating.

If ONLY_TAGS is set, the server MUST NOT return any Message ID that does not have *every* specified tag set.
If EXCLUDE_TAGS is set, the server MUST NOT return any Message ID that has *any* specified tag set.
A message must therefore carry all of ONLY_TAGS and none of EXCLUDE_TAGS to be returned.

### SEARCH_CONTENT

Search messages by the Content portion.

**Positional Parameters**

| Index | Name  | Type   | Optional? | Description                            |
|-------|-------|--------|-----------|----------------------------------------|
| 0     | Query | String | No        | The text to search message Content for |

**Keyed Parameters**

| Key | Name         | Type      | Optional? | Description                                                    |
|-----|--------------|-----------|-----------|----------------------------------------------------------------|
| 0   | MAX_RESULTS  | Int       | Yes       | The maximum number of Message IDs the client wishes to receive |
| 1   | ONLY_TAGS    | List[Int] | Yes       | Filter results to messages with the specified tag IDs          |
| 2   | EXCLUDE_TAGS | List[Int] | Yes       | Filter results to messages without the specified tag IDs       |

**Return Parameters**

| Index | Name        | Type        | Optional? | Description                                         |
|-------|-------------|-------------|-----------|-----------------------------------------------------|
| 0     | Message IDs | List[Bytes] | No        | The ids of messages whose Content matches the Query |

**Specific Error Codes**

| Code | Name                | Description                                                          |
|------|---------------------|----------------------------------------------------------------------|
| 0    | CONFLICTING_FILTERS | The client specified the same tag in both ONLY_TAGS and EXCLUDE_TAGS |

The matching semantics are implementation-defined, but a server SHOULD at minimum perform a case-insensitive substring match.
The order of the returned ids is unspecified.
If MAX_RESULTS is given, the server MUST NOT return more than that many ids;
which matches are dropped when results are truncated is implementation-defined.
A server MAY additionally limit result counts, and MAY return `NO` with `GENERAL_ERROR` = `TOO_LARGE` instead of truncating.

If ONLY_TAGS is set, the server MUST NOT return any Message ID that does not have *every* specified tag set.
If EXCLUDE_TAGS is set, the server MUST NOT return any Message ID that has *any* specified tag set.
A message must therefore carry all of ONLY_TAGS and none of EXCLUDE_TAGS to be returned.

If non-LXMF messages are present in the mailbox, non-LXMF messages SHOULD be searched by full text.

### UPLOAD

This request updates state. See section "Mailbox state".

Add messages to the MAIL_LIST Collection manually as memos, outside of the built-in delivery mechanism.
This can be used to manage items such as drafts and notes that aren't intended to be sent as mail.

**Keyed Parameters**

| Key | Name        | Type              | Optional? | Description                                       |
|-----|-------------|-------------------|-----------|---------------------------------------------------|
| 0   | IF_IN_STATE | Map[Int -> Bytes] | Yes       | See section "Requests that mutate state"          |
| 1   | TAGS        | List[Int]         | Yes       | Tag IDs to apply to every uploaded message        |
| 2   | METADATA    | Map               | Yes       | Metadata entries to set on every uploaded message |

**Positional Parameters**

| Index | Name     | Type        | Optional? | Description               |
|-------|----------|-------------|-----------|---------------------------|
| 0     | Messages | List[Bytes] | No        | The raw messages to store |

**Return Parameters**

| Index | Name           | Type                       | Optional? | Description                                            |
|-------|----------------|----------------------------|-----------|--------------------------------------------------------|
| 0     | Updated States | Map[Int -> [Bytes, Bytes]] | No        | See section "Requests that mutate state"               |
| 1     | Message IDs    | List[Bytes]                | No        | The id stored for each message, in the order requested |

**Specific Error Codes**

| Code | Name         | Description                                                                       |
|------|--------------|-----------------------------------------------------------------------------------|
| 0    | UNKNOWN_TAG  | A Tag ID in TAGS does not exist in the TAG_LIST Collection                        |
| 1    | RESERVED_KEY | A METADATA key is one the server manages itself and does not accept from a client |

The server MUST store an uploaded message opaquely.
It MUST NOT parse the message, and MUST NOT treat it as LXMF even if it would parse as LXMF.
An uploaded message is therefore a non-LXMF message for every other purpose in this specification:
it is assigned a universally unique id as described in the "MAIL_LIST" section,
and the LXMF-informed fetch requests (FETCH_HEAD, FETCH_PAYLOAD, FETCH_FIELDS) return `nil` for it.

If an uploaded message is identical to one already present in the MAIL_LIST Collection, the server MAY choose whether to store an additional copy.
Because uploaded messages do not take their ids from their contents, an additional copy receives its own distinct id.
If not storing an additional copy, it MUST return the existing id, and MUST still apply any supplied TAGS and METADATA to the existing message.

An UPLOAD changes the MAIL_LIST, MESSAGE_TAG, and METADATA Collections.
The server MAY set Metadata and Tags the client did not explicitly specify.
As with any write, a Collection that did not actually change MUST NOT appear in the Updated States map.

Uploads can be large.
A server MAY refuse a request that is too large with a `NO` response and `GENERAL_ERROR` = `TOO_LARGE`;
a client SHOULD then retry with fewer messages.
The client SHOULD utilize Reticulum Resources for large requests.

There is no request to edit a stored message.
A client can edit an uploaded memo by uploading the new version and deleting the old one,
which it SHOULD do in that order to avoid data loss.

### DELETE

This request updates state. See section "Mailbox state".

Remove messages from the MAIL_LIST Collection.

**Positional Parameters**

| Index | Name        | Type        | Optional? | Description                       |
|-------|-------------|-------------|-----------|-----------------------------------|
| 0     | Message IDs | List[Bytes] | No        | The ids of the messages to delete |

Deletion is permanent and takes effect immediately.
A client offering the user a recoverable delete SHOULD apply the TRASH tag instead,
and commit the delete only when the user empties the trash or a predefined trigger condition is met.

Deleting a message MUST also remove its entries from the MESSAGE_TAG and METADATA Collections,
changing those Collections' State Tokens accordingly.

Deleting an id that is not present in the MAIL_LIST Collection is not an error, and causes no state change.

### CREATE_TAG

This request updates state. See section "Mailbox state".

Add named tags to the TAG_LIST Collection.

**Positional Parameters**

| Index | Name  | Type      | Optional? | Description                     |
|-------|-------|-----------|-----------|---------------------------------|
| 0     | Names | List[Str] | No        | The names of the tags to create |

**Return Parameters**

| Index | Name           | Type                       | Optional? | Description                                             |
|-------|----------------|----------------------------|-----------|---------------------------------------------------------|
| 0     | Updated States | Map[Int -> [Bytes, Bytes]] | No        | See section "Requests that mutate state"                |
| 1     | Tag IDs        | List[Int]                  | No        | The id of the tag for each name, in the order requested |

**Specific Error Codes**

| Code | Name         | Description                               |
|------|--------------|-------------------------------------------|
| 0    | INVALID_NAME | The server considers the tag name invalid |

The server MUST assign each newly created tag a positive integer id that is not in use by another tag.

Tag names MUST be unique within a mailbox.
If a supplied name is already in use — whether by a user-defined tag or by a Server-Defined Tag — the server MUST NOT create a second tag;
it MUST return the existing tag's id, and that name causes no state change.
How names are compared for uniqueness is implementation-defined,
but a server SHOULD compare them case-insensitively.

### DELETE_TAG

This request updates state. See section "Mailbox state".

Remove named tags from the TAG_LIST Collection.

**Positional Parameters**

| Index | Name    | Type      | Optional? | Description                   |
|-------|---------|-----------|-----------|-------------------------------|
| 0     | Tag IDs | List[Int] | No        | The ids of the tags to delete |

**Specific Error Codes**

| Code | Name               | Description                                      |
|------|--------------------|--------------------------------------------------|
| 0    | SERVER_DEFINED_TAG | The request tried to delete a Server-Defined Tag |

Server-Defined Tags — those with negative IDs — cannot be deleted by the client.

Deleting a tag MUST also remove every MESSAGE_TAG Collection entry that references it,
changing that Collection's State Token accordingly.

Deleting an id that is not present in the TAG_LIST Collection is not an error, and causes no state change.

A server MAY assign the id of a deleted tag to a tag created later.
See the "TAG_LIST Delta" section for how a syncing client sees this.

### RENAME_TAG

This request updates state. See section "Mailbox state".

Rename tags in the TAG_LIST Collection.

**Positional Parameters**

| Index | Name    | Type            | Optional? | Description                                  |
|-------|---------|-----------------|-----------|----------------------------------------------|
| 0     | Renames | Map[Int -> Str] | No        | A map of Tag ID to the new name for that tag |

**Specific Error Codes**

| Code | Name               | Description                                                 |
|------|--------------------|-------------------------------------------------------------|
| 0    | SERVER_DEFINED_TAG | The request tried to rename a Server-Defined Tag            |
| 1    | UNKNOWN_TAG        | A supplied Tag ID does not exist in the TAG_LIST Collection |
| 2    | INVALID_NAME       | The server does not support the provided tag name           |
| 3    | DUPLICATE_NAME     | The supplied name is already in use by another tag          |

Renaming a tag to the name it currently has is not an error, and causes no state change.

Renaming does not change a tag's id, so the MESSAGE_TAG Collection is unchanged.

### ADD_TAG

This request updates state. See section "Mailbox state".

Add tags to the MESSAGE_TAG Collection.

**Positional Parameters**

| Index | Name      | Type                    | Optional? | Description                                               |
|-------|-----------|-------------------------|-----------|-----------------------------------------------------------|
| 0     | Additions | Map[Bytes -> List[Int]] | No        | A map of Message ID to the Tag IDs to add to that message |

**Specific Error Codes**

| Code | Name            | Description                                                      |
|------|-----------------|------------------------------------------------------------------|
| 0    | UNKNOWN_MESSAGE | A supplied Message ID does not exist in the MAIL_LIST Collection |
| 1    | UNKNOWN_TAG     | A supplied Tag ID does not exist in the TAG_LIST Collection      |

Adding a tag to a message that already has that tag is not an error, and causes no state change.

### REMOVE_TAG

This request updates state. See section "Mailbox state".

Remove tags from the MESSAGE_TAG Collection.

**Positional Parameters**

| Index | Name     | Type                    | Optional? | Description                                                    |
|-------|----------|-------------------------|-----------|----------------------------------------------------------------|
| 0     | Removals | Map[Bytes -> List[Int]] | No        | A map of Message ID to the Tag IDs to remove from that message |

**Specific Error Codes**

| Code | Name            | Description                                                      |
|------|-----------------|------------------------------------------------------------------|
| 0    | UNKNOWN_MESSAGE | A supplied Message ID does not exist in the MAIL_LIST Collection |
| 1    | UNKNOWN_TAG     | A supplied Tag ID does not exist in the TAG_LIST Collection      |

Removing a tag a message does not have is not an error, and causes no state change.

A tag remains in the TAG_LIST Collection even if not assigned to any message.

### SET_METADATA

This request updates state. See section "Mailbox state".

Add entries to items in the METADATA Collection.

**Positional Parameters**

| Index | Name    | Type              | Optional? | Description                                                        |
|-------|---------|-------------------|-----------|--------------------------------------------------------------------|
| 0     | Entries | Map[Bytes -> Map] | No        | A map of Message ID to the Metadata entries to set on that message |

**Specific Error Codes**

| Code | Name            | Description                                                                               |
|------|-----------------|-------------------------------------------------------------------------------------------|
| 0    | UNKNOWN_MESSAGE | A supplied Message ID does not exist in the MAIL_LIST Collection                          |
| 1    | RESERVED_KEY    | A supplied key is one the server manages itself and does not accept from a client         |
| 2    | INVALID_KEY     | A supplied key is neither an integer nor a string, or is otherwise rejected by the server |

Entries are merged into the message's existing Metadata Map.
A key present in the request sets or replaces the Metadata Map's current value.
A key not present in the request is left unchanged from the Metadata Map's current value.
Setting a key to the value it already holds causes no state change.

### REMOVE_METADATA

This request updates state. See section "Mailbox state".

Remove entries from items in the METADATA Collection.

**Positional Parameters**

| Index | Name     | Type                           | Optional? | Description                                                          |
|-------|----------|--------------------------------|-----------|----------------------------------------------------------------------|
| 0     | Removals | Map[Bytes -> List[Int OR Str]] | No        | A map of Message ID to the Metadata keys to remove from that message |

**Specific Error Codes**

| Code | Name            | Description                                                                           |
|------|-----------------|---------------------------------------------------------------------------------------|
| 0    | UNKNOWN_MESSAGE | A supplied Message ID does not exist in the MAIL_LIST Collection                      |
| 1    | RESERVED_KEY    | A supplied key is one the server manages itself and does not allow a client to remove |

Removing a key not present in the Metadata Map is not an error, and causes no state change.

### SEND_RAW

This request may update state. See section "Mailbox state".

Send a raw message from the server to another destination.

A message sent using this request is considered to be already packed or otherwise containerless, and is to be relayed unchanged.
The server does not pack the message in LXMF or any other message format.

**Keyed Parameters**

| Key | Name        | Type              | Optional? | Description                                                             |
|-----|-------------|-------------------|-----------|-------------------------------------------------------------------------|
| 0   | IF_IN_STATE | Map[Int -> Bytes] | Yes       | See section "Requests that mutate state"                                |
| 1   | STORE       | Bool              | Yes       | Whether to store a copy of the message in the mailbox. Defaults to true |
| 2   | TAGS        | List[Int]         | Yes       | Tag IDs to apply to the stored copy                                     |

**Positional Parameters**

| Index | Name        | Type  | Optional? | Description                                               |
|-------|-------------|-------|-----------|-----------------------------------------------------------|
| 0     | Destination | Bytes | No        | The Reticulum Destination hash to transmit the message to |
| 1     | Message     | Bytes | No        | The message to transmit                                   |

**Return Parameters**

| Index | Name           | Type                       | Optional? | Description                                               |
|-------|----------------|----------------------------|-----------|-----------------------------------------------------------|
| 0     | Updated States | Map[Int -> [Bytes, Bytes]] | No        | See section "Requests that mutate state"                  |
| 1     | Message ID     | Bytes OR nil               | No        | The id of the stored copy, or `nil` if no copy was stored |

**Specific Error Codes**

| Code | Name        | Description                                                |
|------|-------------|------------------------------------------------------------|
| 0    | SEND_FAILED | The server failed to deliver the message                   |
| 1    | UNKNOWN_TAG | A Tag ID in TAGS does not exist in the TAG_LIST Collection |
| 2    | REFUSED     | The server refuses to transmit the message                 |

Delivery over Reticulum may be asynchronous, and a server SHOULD NOT hold the Response open waiting for delivery.
An `OK` Response means the server has accepted the message for transmission; it does not mean the message has arrived.

If a copy is stored, the server SHOULD apply the OUTBOX tag to it in addition to any supplied TAGS,
and the request changes the MAIL_LIST and MESSAGE_TAG Collections as an UPLOAD would.
If `STORE` is false, the request changes no Collection and MUST return an empty Updated States map.

### SEND_LXMF

This request may update state. See section "Mailbox state".

Send an LXMF message from the server to another destination.

Requests that the server construct, sign, and transmit an LXMF message using the mailbox's own identity as the source.
This is how a client sends mail *as* the mailbox, as the mailbox's private key is held only by the server.

**Keyed Parameters**

| Key | Name        | Type              | Optional? | Description                                                             |
|-----|-------------|-------------------|-----------|-------------------------------------------------------------------------|
| 0   | IF_IN_STATE | Map[Int -> Bytes] | Yes       | See section "Requests that mutate state"                                |
| 1   | STORE       | Bool              | Yes       | Whether to store a copy of the message in the mailbox. Defaults to true |
| 2   | TAGS        | List[Int]         | Yes       | Tag IDs to apply to the stored copy                                     |

**Positional Parameters**

| Index | Name        | Type  | Optional? | Description                                               |
|-------|-------------|-------|-----------|-----------------------------------------------------------|
| 0     | Destination | Bytes | No        | The Reticulum Destination hash to transmit the message to |
| 1     | Title       | Bytes | No        | The Title portion of the message to construct             |
| 2     | Content     | Bytes | No        | The Content portion of the message to construct           |
| 3     | Fields      | Map   | Yes       | The Fields portion of the message to construct            |

**Return Parameters**

| Index | Name           | Type                       | Optional? | Description                              |
|-------|----------------|----------------------------|-----------|------------------------------------------|
| 0     | Updated States | Map[Int -> [Bytes, Bytes]] | No        | See section "Requests that mutate state" |
| 1     | Message ID     | Bytes                      | No        | The message-id of the LXMF message       |

**Specific Error Codes**

| Code | Name        | Description                                                |
|------|-------------|------------------------------------------------------------|
| 0    | SEND_FAILED | The server failed to deliver the message                   |
| 1    | UNKNOWN_TAG | A Tag ID in TAGS does not exist in the TAG_LIST Collection |
| 2    | REFUSED     | The server refuses to send the message                     |

The server MUST use the mailbox's own identity as the source of the message, and MUST sign it with that identity.
The server sets the message's Timestamp.

The Message ID returned is the LXMF message-id of the message the server actually produced.

As with SEND_RAW, an `OK` Response means the server has accepted the message for delivery,
not that the message has arrived.

If a copy is stored, the server SHOULD apply the OUTBOX tag to it in addition to any supplied TAGS,
and the request changes the MAIL_LIST and MESSAGE_TAG Collections as an UPLOAD would.
If `STORE` is false, the request changes no Collection and MUST return an empty Updated States map.

A client that sent the message in response to another message
SHOULD apply the RESPONDED tag to the message it responded to, using ADD_TAG.
The server does not infer this.

## Notifications

A Notification is an Exchange for which no Response is expected.

The first Parameter of a Notification is an integer event type.
The integers 0-127 inclusive are reserved for standardized event types; extensions MAY use integers outside this range.
Each event type defines the Parameters that follow it.

| Code | Name              | Description                                       |
|------|-------------------|---------------------------------------------------|
| 0    | COLLECTION_UPDATE | A subscribed Collection's State Token has changed |

A receiver MUST ignore Notifications with an event type it does not recognize.

### COLLECTION_UPDATE

Sent to a subscriber when a Collection's State Token has changed.
See the SUBSCRIBE and UNSUBSCRIBE request types for how a client starts and stops receiving these.

**Parameters**

| Index | Name           | Type  | Optional? | Description                                          |
|-------|----------------|-------|-----------|------------------------------------------------------|
| 0     | Collection id  | Int   | No        | The Collection that has had a State Token change     |
| 1     | Previous State | Bytes | No        | The State Token the Collection had before the change |
| 2     | New State      | Bytes | No        | The State Token the Collection has after the change  |

A COLLECTION_UPDATE carries no information about *what* changed.
A client is expected to use a SYNC request to update its information.

Where a server has batched several changes into a single Notification,
Previous State is the State Token the Collection was in before the first change in the batch.

A server MUST send a COLLECTION_UPDATE for a change the subscriber itself caused.
A client MUST tolerate a Notification describing a state it has already reached;
in that case New State matches its last known State Token and no sync is required.

Notifications are not acknowledged and their delivery is not guaranteed.
A client MUST NOT rely on Notifications as its only means of staying current,
and SHOULD sync on its own schedule regardless of whether it is subscribed.
