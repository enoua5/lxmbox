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

[The Internet Mail Access Protocol (IMAP)](https://www.rfc-editor.org/info/rfc9051/) is a tried-and-trusted protocol for managing a mailbox over the internet. It uses a simple text-based API, and is flexible in its link and authentication requirements.

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

### Exchange

An Exchange is some unit of information transferred between the client and the server.
This can include Requests, Responses, and Notifications.

All exchanges are formatted as a [msgpack](https://msgpack.org/) array, with the first argument being an integer acting as the exchange type:

| Exchange Type | Code |
|---------------|------|
| Request       | 0    |
| Response      | 1    |
| Notification  | 2    |

Additional items in the array are Parameters carrying the content of the Exchange.

Implementations MUST ignore Exchanges with types they do not recognize.
Implementations SHOULD ignore Exchanges with missing required parameters.
Implementations SHOULD accept Exchanges with unexpected additional parameters.

#### Requests

A Request is an Exchange for which a Response is expected.

The format of a Request consists of at least two Parameters.

The first parameter is an integer serving as the "Request ID".
This helps match a Response back to the Request that initiated it.

When using the Link connection mode, each Request SHOULD use a Request ID the sender has not yet used.
The receiver MUST accept requests with duplicate IDs.

When using the Single mode, responses can be matched to requests without needing the Request ID; the Request ID SHOULD be set to `0` in this case.

The second Parameter is the request type, indicating an action the sender wants the receiver to complete.
See section "Request types" for more information.

Additional parameters may be required as defined by the request type.
The first of these additional parameters, if any additional parameters are provided,
will always be a map containing additional non-positional arguments,
with meanings assigned according to request type — referred to as Keyed Parameters.
A sender SHOULD NOT include keys in this parameter not defined in the spec.
A receiver MUST accept and ignore keys in this parameter it does not expect.
Additional parameters after the Keyed Parameter map are referred to as Positional Parameters.

#### Responses

A Response is an Exchange returning information requested by a Request.

The format of a Response consists of at least two parameters.

The first parameter is the Request ID.
This value MUST be the same as included in the request.

The second parameter is the status code:
| Status        | Code | Description                                                                    |
|---------------|------|--------------------------------------------------------------------------------|
| OK            | 0    | The action was performed                                                       |
| NO            | 1    | The request was understood, but was either ignored or an error was encountered |
| BAD           | 2    | The request was not understood                                                 |

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

| Code | Name               | Description                                                                                    |
|------|--------------------|------------------------------------------------------------------------------------------------|
| 0    | UNAUTHENTICATED    | MAY be returned to an unauthenticated client instead of silently ignoring a request            |
| 1    | UNAUTHORIZED       | MAY be returned to a client with an unexpected identity instead of silently ignoring a request |
| 2    | INCOMPLETE         | Request is missing required information                                                        |
| 3    | WRONG_TYPE         | A Request included a field with an unexpected datatype                                         |
| 4    | UNSUPPORTED        | The server understands the request, but has not implemented the functionality                  |
| 5    | TOO_LARGE          | The server refuses to process the request because it exceeds size limits or storage space      |
| 6    | SERVER_ERROR       | The server encountered an error while processing the request and could not continue            |
| 7    | STATE_MISMATCH     | The client expected the mailbox to be in a state it was not found to be in                     |

## Mailbox state

RNMMP supports updating a client based on changes that have occurred since the client's last known state.
These states are organized into Collections, with each Collection being associated with a server-defined State Token.
The State Token MUST be represented in msgpack using the Bin type family.

Whenever a Collection's state is updated, the server MUST create a new State Token to represent it.
State Tokens are to be intepreted as opaque and MUST NOT be parsed by the client; only used raw.

The zero-length byte array (msgpack `0xC4 0x00`) is reserved to represent the "Initial State".
A client can use the Initial State Token as its last known State Token to indicate that they
require the full current state of the Collection, rather than the delta from some known state.

Collections are represented by integers.
The integers 0-127 inclusive are reserved for standard Collections.
Extensions MAY use integers outside of this range for other Collections.

| Collection name | Code | Description                                                  |
|-----------------|------|--------------------------------------------------------------|
| MAIL_LIST       | 0    | The list of messages in the mailbox                          |
| TAG_LIST        | 1    | The list of tag names defined in the mailbox                 |
| MESSAGE_TAG     | 2    | The list of tags applied to each message in the mailbox      |
| METADATA        | 3    | The non-tag metadata for each message in the mailbox         |

Note that message *content* is immutable.
A client's stored mailbox state does not need to include it.

The `MESSAGE_TAG` and `METADATA` Collections are keyed by ids from the `MAIL_LIST` Collection,
and the `MESSAGE_TAG` Collection is keyed by ids from the `TAG_LIST` Collection.
The client MUST tolerate items in these Collections referring to ids not known to exist in other Collections.

### Requests that mutate state

All requests that mutate state (UPLOAD, DELETE, CREATE_TAG, etc) modify the mailbox.
They share the behaviour described here, in addition to behaviour described in the "Request types" section.

Some write requests involve more than one state change.
A write request MUST be applied atomically: either every change it requested is applied, or none are.
If the server returns a `NO` or `BAD` response, the client MUST be able to assume nothing was changed.

**Keyed Parameters**

| Key | Name        | Type             | Optional? | Description                                                                                   |
|-----|-------------|------------------|-----------|-----------------------------------------------------------------------------------------------|
| 0   | IF_IN_STATE | Map[Int → Bytes] | Yes       | A map of Collection ID to the State Token the client believes that Collection is currently in |

A client MAY specify `IF_IN_STATE` to prevent unexpected results when multiple clients are connected simultaneously.
If `IF_IN_STATE` is present, the server MUST compare each supplied State Token against the current State Token of the corresponding Collection *before* applying any change.
If any supplied token does not match, the server MUST reject the request, apply no changes, and SHOULD return the General Error `STATE_MISMATCH`.

When returning `STATE_MISMATCH`, the server SHOULD include an `ERROR_DETAILS` map with key `0` holding a map representing the updated Collections.
The updated Collection map should use Collection IDs as keys and those Collections' current State Tokens as values.

**Return Parameters**

| Index | Name           | Type                      | Optional? | Description                                                                            |
|-------|----------------|---------------------------|-----------|----------------------------------------------------------------------------------------|
| 1     | Updated States | Map[Int → [Bytes, Bytes]] | No        | A map of Collection ID to State Token updates, for each Collection the request changed |

On an `OK` response, a write request returns the updated State Token information of every Collection that had a state change.
A Collection whose state did not change MUST NOT appear.
A request that changed nothing (for example, deleting ids that were already absent) MUST return an empty map.

The values in the Updated States map are 2-tuples holding the previous State Token and new State Token.
If the first value in the tuple matches the client's last known state,
the client SHOULD update their last known state to the second value in the tuple.
If the first value in the tuple *does not* match the client's last known state,
the client MUST NOT update their last known state, and SHOULD mark their state as stale and requiring a sync.

### MAIL_LIST

### TAG_LIST

### MESSAGE_TAG

### METADATA

## Request types

Request types are represented by an integer code.
The numbers from 0-127 inclusive are reserved for official request types.
Numbers outside of this range MAY be used for implementation-defined request types.
The following request types MUST be supported by the server;
the server MAY opt to return a `NO` response with `GENERAL_ERROR` = `UNSUPPORTED` for any request.

| Code | Name              | Description (see subsections for specification)                                                    |
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
| 9    | FETCH_CONTENT      | Fetch the Content portion of stored LXMF messages                                                 |
| 10   | FETCH_FIELDS       | Fetch the Fields portion of stored LXMF messages                                                  |
| 11   | FETCH_TIMESTAMP    | Fetch the Timestamp portion of stored LXMF messages                                               |
| 12   | FETCH_TITLE        | Fetch the Title portion of stored LXMF messages                                                   |
| 13   | SEARCH_TITLE       | Search LXMF messages by the Title portion                                                         |
| 14   | SEARCH_CONTENT     | Search LXMF messages by the Content portion                                                       |
| 15   | UPLOAD             | Add messages to the Mail List Collection manually outside of the built-in delivery mechanism      |
| 16   | DELETE             | Remove messages from the Mail List Collection                                                     |
| 17   | CREATE_TAG         | Add named tags to the Tag List Collection                                                         |
| 18   | DELETE_TAG         | Remove named tags from the Tag List Collection                                                    |
| 19   | RENAME_TAG         | Rename tags in the Tag List Collection                                                            |
| 20   | ADD_TAG            | Add tags to Message Tag Collection                                                                |
| 21   | REMOVE_TAG         | Remove tags from the Message Tag Collection                                                       |
| 22   | SET_METADATA       | Add entries to items in the Metadata Collection                                                   |
| 23   | REMOVE_METADATA    | Remove entries from items in the Metadata Collection                                              |
| 24   | SEND_RAW           | Send a raw message from the server to another destination                                         |
| 25   | SEND_LXMF          | Send an LXMF message from the server to another destination                                       |


Further details in the subsections below.
Each section might define tables for their Keyed Parameters,
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
| 1     | Capability List | list[int OR str OR [int OR str, map]] | No        | The optional features the server supports |

#### Capability list

The server MUST respond with a msgpack list of capabilities.
Each item in the list can be an integer code, a string, or a list.
Integers are reserved for standardized optional features.
Strings MAY be used for implementation-defined extensions.
A list represents a feature-variant pair.
When a list is used, it MUST be length 2;
the first element MUST be an integer or string as defined above;
and the second element MUST be a map containing variant information as defined for the feature code.

The first element of the feature list MUST be a protocol version,
which will be incremented when breaking changes are made to the spec.
Currently, the only version code supported is `1`.

### SUBSCRIBE

Indicate that the client would like to receive active updates regarding a Collection state.

**Positional Parameters**

| Index | Name        | Type  | Optional? | Description                                                  |
|-------|-------------|-------|-----------|--------------------------------------------------------------|
| 1     | Collection  | Int   | No        | The collection to subscribe to updates for                   |
| 2     | Destination | Bytes | Yes       | A Reticulum Destination to send LXMF Update Notifications to |

**Specific Error Codes**

| Code | Name                | Description                                                                                                        |
|------|---------------------|--------------------------------------------------------------------------------------------------------------------|
| 0    | UNKNOWN_COLLECTION  | Server does not have a Collection with the requested ID                                                            |
| 1    | UNKNOWN_DESTINATION | Server refuses to send LXMF notifications to the requested Destination because it does not recognize it as trusted |
| 2    | NO_PASSIVE_NOTIFS   | Server refuses to send LXMF notifications, only supporting notifications over an active link                       |
| 3    | REFUSED             | Server refuses to send notifications as requested for unspecified/other reasons                                    |

If accepted, the server will begin to send COLLECTION_UPDATE Notifications whenever the State Token for the specified Collection changes.

If Destination is specified, these notifications will be sent in Single mode as LXMF to the specified Reticulum Destination.

If Destination is not specified, notifications will be sent to the client making the request:
- If the request was made in Single mode, notifications will be sent in Single mode as LXMF to the request's Destination.
- If the request was made in Link mode, notifications will be sent in the active link; these updates will be automatically unsubscribed when the link closes.

### UNSUBSCRIBE

Indicate that the client would like to stop receiving active updates regarding a Collection state

**Positional Parameters**

| Index | Name        | Type  | Optional? | Description                                                  |
|-------|-------------|-------|-----------|--------------------------------------------------------------|
| 1     | Collection  | Int   | No        | The collection to unsubscribe from updates for               |
| 2     | Destination | Bytes | Yes       | A Reticulum Destination where notifications were being sent  |

Request to stop receiving COLLECTION_UPDATE Notifications requested via the SUBSCRIBE command.
Positional parameters are understood the same as with the SUBSCRIBE command.

### LIST_SUBSCRIPTIONS

List active subscriptions for Single Mode destinations.

**Return Parameters**

| Index | Name        | Type               | Optional? | Description                                                                                                                         |
|-------|-------------|--------------------|-----------|-------------------------------------------------------------------------------------------------------------------------------------|
| 1     | Subscribers | List[[Int, Bytes]] | No        | A list of 2-tuples of [Collection ID, Reticulum Destination] pairs for currently active Single Mode COLLECTION_UPDATE Notifications |

### SYNC

Get the delta for a Collection from a given State Token

**Positional Parameters**

| Index | Name             | Type  | Optional? | Description                                                                              |
|-------|------------------|-------|-----------|------------------------------------------------------------------------------------------|
| 1     | Collection ID    | Int   | No        | The ID of the Collection to request a Delta for                                          |
| 2     | Last Known State | Bytes | No        | The client's last known State Token for the Collection, for a delta to be generated from |

**Specific Error Codes**

| Code | Name                | Description                                                                                                                  |
|------|---------------------|------------------------------------------------------------------------------------------------------------------------------|
| 0    | UNKNOWN_COLLECTION  | Server does not have a Collection with the requested ID                                                                      |
| 1    | UNKNOWN_STATE       | Server cannot generate a delta from the given state to the requested state. Client SHOULD retry with the Initial State Token |

**Return Parameters**

| Index | Name  | Type | Optional? | Description                                                                                 |
|-------|-------|------|-----------|---------------------------------------------------------------------------------------------|
| 1     | Delta | Map  | No        | Structured details about the changes to the collection since the specified Last Known State |

The exact format of the Delta Return Parameter depends on the Collection type.

#### MAIL_LIST Delta

The MAIL_LIST Delta has two keys, ADDED (`0`) and DELETED (`1`).
Each key's value is a list of Message IDs,
where ADDED is a complete list of messages that did not exist in the Last Known State but now do,
and DELETED is a complete list of messages that existed in the Last Known State but now do not.

A Message ID MUST NOT appear in both the ADDED and DELETED list.
I.E., If a message was added and then deleted since the Last Known State, it should not appear in the delta.

Messages that have the same existence state as the Last Known State MUST NOT appear.

#### TAG_LIST Delta

The TAG_LIST Delta uses Tag IDs as keys, and includes information about that tag as the value.
For tags that have been created or renamed since the Last Known State, the value is a String representing the current name.
For tags that have been deleted since the Last Known State, the value is `nil`.

Intermediary states MUST NOT be represented.
I.E., If a tag is renamed multiple times, only the current name is shown;
and if a tag is deleted and a new tag with the same ID is created, the Delta is shown the same as if the tag was renamed.

Tags ids that have the same name as in the Last Known State MUST NOT appear.

#### MESSAGE_TAG Delta

The MESSAGE_TAG Delta uses Message IDs as keys, and includes information about the Message's current tags as the value.
For messages that have had tags added or removed, the value is the list of current Tag IDs.
For messages that have been deleted, the value is `nil`.

Intermediary states MUST NOT be represented.
I.E., if a tag is added and then removed, its addition MUST NOT be reported.

If a message has the same set of tags as in the Last Known State, it MUST NOT appear in the Delta.
The set of tags does not have an order; if the server represents tags in an order,
it MUST consider a reordering of tags as being the same set of tags and not include it in the Delta.

#### METADATA Delta

The METADATA Delta uses Message IDs as keys, and includes information about the message's current metadata as the value.
For messages that have had Metadata changed, the value is the message's current metadata map.
For messages that have been deleted, the value is `nil`.

Intermediary states MUST NOT be represented.
I.E., if a metadata field is added and then removed, it MUST NOT be included in the Delta.

If a message has the same metadata as the Last Known State, it MUST NOT appear in the Delta.
A server MAY consider a Map as ordered or unordered when determining if an update needs to be reported.

### FETCH_FULL

Fetch raw stored messages including all LXMF headers.

**Positional Parameters**

| Index | Name        | Type        | Optional? | Description                      |
|-------|-------------|-------------|-----------|----------------------------------|
| 1     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type               | Optional? | Description                                                                                           |
|-------|----------|--------------------|-----------|-------------------------------------------------------------------------------------------------------|
| 1     | Messages | List[Bytes OR nil] | No        | The messages, returned in the same order requested. For messages that aren't found, `nil` is returned |

The Messages Return Parameter is a list of message raw message data returned byte-for-byte as delivered.
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
| 1     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type               | Optional? | Description                                                                                                   |
|-------|----------|--------------------|-----------|---------------------------------------------------------------------------------------------------------------|
| 1     | Messages | List[Bytes OR nil] | No        | The message headers, returned in the same order requested. For messages that aren't found, `nil` is returned. |

The Messages Return Parameter is a list of each message's header as a Bytes value:

| Index | Name        | Type  | Description                                  |
|-------|-------------|-------|----------------------------------------------|
| 1     | Destination | Bytes | The destination hash the message was sent to |
| 2     | Source      | Bytes | The source hash the message was sent from    |
| 3     | Signature   | Bytes | The signature on the message                 |

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
| 1     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type               | Optional? | Description                                                                                                    |
|-------|----------|--------------------|-----------|----------------------------------------------------------------------------------------------------------------|
| 1     | Messages | List[Bytes OR nil] | No        | The message payloads, returned in the same order requested. For messages that aren't found, `nil` is returned. |


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
| 1     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type               | Optional? | Description                                                                                                    |
|-------|----------|--------------------|-----------|----------------------------------------------------------------------------------------------------------------|
| 1     | Messages | List[Bytes OR nil] | No        | The message contents, returned in the same order requested. For messages that aren't found, `nil` is returned. |

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
| 1     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type             | Optional? | Description                                                                                                  |
|-------|----------|------------------|-----------|--------------------------------------------------------------------------------------------------------------|
| 1     | Messages | List[Map OR nil] | No        | The message fields, returned in the same order requested. For messages that aren't found, `nil` is returned. |

The Messages Return Parameter is a list of each message's Fields as a Map, decoded from the payload.

The server MUST return `nil` for any requested id that does not exist in the MAIL_LIST Collection,
or for which the stored message is not in LXMF.

Fetch responses can be large.
A server MAY refuse a request that selects too many messages, or whose response would be too large,
with a `NO` response and `GENERAL_ERROR` = `TOO_LARGE`; a client SHOULD then retry with fewer ids.
The server SHOULD utilize Reticulum Resources for large responses.

### FETCH_TIMESTAMP

Fetch the Timestamp portion of stored LXMF messages.

**Positional Parameters**

| Index | Name        | Type        | Optional? | Description                      |
|-------|-------------|-------------|-----------|----------------------------------|
| 1     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type               | Optional? | Description                                                                                                      |
|-------|----------|--------------------|-----------|------------------------------------------------------------------------------------------------------------------|
| 1     | Messages | List[Float OR nil] | No        | The message timestamps, returned in the same order requested. For messages that aren't found, `nil` is returned. |

The Messages Return Parameter is a list of each message's reported Timestamp as a float: the LXMF message timestamp, in seconds since the Unix epoch.

The server MUST return `nil` for any requested id that does not exist in the MAIL_LIST Collection,
or for which the stored message is not in LXMF.

Fetch responses can be large.
A server MAY refuse a request that selects too many messages, or whose response would be too large,
with a `NO` response and `GENERAL_ERROR` = `TOO_LARGE`; a client SHOULD then retry with fewer ids.
The server SHOULD utilize Reticulum Resources for large responses.

### FETCH_TITLE

Fetch the Title portion of stored LXMF messages.

**Positional Parameters**

| Index | Name        | Type        | Optional? | Description                      |
|-------|-------------|-------------|-----------|----------------------------------|
| 1     | Message IDs | List[Bytes] | No        | The ids of the messages to fetch |

**Return Parameters**

| Index | Name     | Type               | Optional? | Description                                                                                                  |
|-------|----------|--------------------|-----------|--------------------------------------------------------------------------------------------------------------|
| 1     | Messages | List[Bytes OR nil] | No        | The message titles, returned in the same order requested. For messages that aren't found, `nil` is returned. |

The Messages Return Parameter is a list of each message's Title as a Bytes value, decoded from the payload.

The server MUST return `nil` for any requested id that does not exist in the MAIL_LIST Collection,
or for which the stored message is not in LXMF.

Fetch responses can be large.
A server MAY refuse a request that selects too many messages, or whose response would be too large,
with a `NO` response and `GENERAL_ERROR` = `TOO_LARGE`; a client SHOULD then retry with fewer ids.
The server SHOULD utilize Reticulum Resources for large responses.

### SEARCH_TITLE

Search LXMF messages by the Title portion.

**Positional Parameters**

| Index | Name  | Type   | Optional? | Description                           |
|-------|-------|--------|-----------|---------------------------------------|
| 1     | Query | String | No        | The text to search message Titles for |

**Keyed Parameters**

| Key | Name        | Type | Optional? | Description                                                    |
|-----|-------------|------|-----------|----------------------------------------------------------------|
| 0   | MAX_RESULTS | Int  | Yes       | The maximum number of Message IDs the client wishes to receive |

**Return Parameters**

| Index | Name        | Type        | Optional? | Description                                       |
|-------|-------------|-------------|-----------|---------------------------------------------------|
| 1     | Message IDs | List[Bytes] | No        | The ids of messages whose Title matches the Query |

The matching semantics are implementation-defined, but a server SHOULD at minimum perform a case-insensitive substring match.
The order of the returned ids is unspecified.
If MAX_RESULTS is given, the server MUST NOT return more than that many ids;
which matches are dropped when results are truncated is implementation-defined.
A server MAY additionally limit result counts, and MAY return `NO` with `GENERAL_ERROR` = `TOO_LARGE` instead of truncating.

### SEARCH_CONTENT

Search messages by the Content portion.

**Positional Parameters**

| Index | Name  | Type   | Optional? | Description                            |
|-------|-------|--------|-----------|----------------------------------------|
| 1     | Query | String | No        | The text to search message Content for |

**Keyed Parameters**

| Key | Name        | Type | Optional? | Description                                                    |
|-----|-------------|------|-----------|----------------------------------------------------------------|
| 0   | MAX_RESULTS | Int  | Yes       | The maximum number of Message IDs the client wishes to receive |

**Return Parameters**

| Index | Name        | Type        | Optional? | Description                                         |
|-------|-------------|-------------|-----------|-----------------------------------------------------|
| 1     | Message IDs | List[Bytes] | No        | The ids of messages whose Content matches the Query |

The matching semantics are implementation-defined, but a server SHOULD at minimum perform a case-insensitive substring match.
The order of the returned ids is unspecified.
If MAX_RESULTS is given, the server MUST NOT return more than that many ids;
which matches are dropped when results are truncated is implementation-defined.
A server MAY additionally limit result counts, and MAY return `NO` with `GENERAL_ERROR` = `TOO_LARGE` instead of truncating.

If non-LXMF messages are present in the mailbox, non-LXMF messages SHOULD be searched by full text.

### UPLOAD

This request updates state. See section "Mailbox state".

### DELETE

This request updates state. See section "Mailbox state".

### CREATE_TAG

This request updates state. See section "Mailbox state".

### DELETE_TAG

This request updates state. See section "Mailbox state".

### RENAME_TAG

This request updates state. See section "Mailbox state".

### ADD_TAG

This request updates state. See section "Mailbox state".

### REMOVE_TAG

This request updates state. See section "Mailbox state".

### SET_METADATA

This request updates state. See section "Mailbox state".

### REMOVE_METADATA

This request updates state. See section "Mailbox state".

### SEND_RAW

This request may update state. See section "Mailbox state".

Send a raw message from the server to another destination

### SEND_LXMF

This request may update state. See section "Mailbox state".

Send an LXMF message from the server to another destination

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
