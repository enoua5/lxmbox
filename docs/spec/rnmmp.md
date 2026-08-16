# The Reticulum Network Mail Management Protocol

## Overview

The Reticulum Network Mail Management Protocol (rnmmp — stylized all lowercase because I think the run of similar letter shapes is funny; "RNMMP" is acceptable) is a protocol for managing a collection of messages (a "mailbox") on a remote server over the [Reticulum Network](https://reticulum.network/manual/index.html).
The primary problem this protocol is designed to solve is to allow a user on the Reticulum Network to manage a singular [LXMF](https://github.com/markqvist/lxmf) delivery point that represent *them*, rather than a delivery point per device they wish to use.

### Status

This protocol is an **early draft** and is expected to undergo heavy and breaking changes during its implementation.

### Consideration of other solutions

Other solutions to problem were considered, but ultimately deemed insufficient for our needs.
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
transfering mail from the central shared server to their singular own device.
This is essentially the *inverse* of the problem we are hoping to solve.

#### Why not IMAP?

[The Internet Mail Access Protocol (IMAP)](https://www.rfc-editor.org/info/rfc9051/) is a tried-and-trusted protocol for managing a mailbox over the internet. It uses a simple text-based API, and is flexible in its link and authentication requirements.

It is entirely reasonable that we could carry IMAP over Reticulum and implement an extension to `AUTHENTICATE` that uses the built-in Reticulum link authentication. I do not think this is a *bad* solution, and you may want to consider the possibility of modifying an existing IMAP server to have these capabilities if you want the security of using server software that's had far more field testing than rnmmp.

Our main reason for not using IMAP over Reticulum mostly comes down to protocol *specificity*.
IMAP has specific functionality for managing MIME messages, whereas we expect to be using LXMF.
IMAP communicates using line-based text in a format not-required-to-be-but-optimized-for *TCP/IP*, whereas Reticulum APIs tend to be designed around *binary* formats and structured in formats such as [msgpack](https://msgpack.org/).

### Conventions

- In examples, `C:` and `S:` are used to indicate exchanges sent by the client and server, respectively.
  - `C[h]:` and `S[h]:` show the binary data exchanged in hexadecimal format.For example, `C[h]:91 0a` shows a client sending the raw bytes 0x91 and 0x0a
  - `C[m]:` and `S[m]:` show the data exchanged in msgpack format after conversion to JSON. For example, `S[m]:[10]` shows a server sending an array containing a 10 in msgpack format, which corresponds to the raw bytes 0x91 and 0x0a
- "User" is used to refer to a human user, whereas "client" is used to refer to software run by the user.
- The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and "OPTIONAL" when appearing in all caps hold their meanings as assigned in [BCP 14](https://www.rfc-editor.org/info/rfc2119)

## Protocol overview

### Connection

RNMMP offers two connection modes, and an rnmmp server SHOULD implement both.

The "Link" mode uses a raw Reticulum [Link Destination](https://reticulum.network/manual/understanding.html#destinations) to provide an active connection.

The "Single" mode uses the Content part of an LXMF message to carry Exchanges asynchronously.

The Link and Single modes differ in how data is transfered and in how the client is authenticated, but otherwise operate identically. The "Exchange" and "Authentication" sections for more information.

### Exchange

An Exchange is some unit of information transfered between the client and the server.
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

The second Parameter is the request type, indicating an action the sender wants to sender to complete.

#### Responses

A Response is an Exchange returning information requested by a Request.

The format of a Response consists of at least two parameters.

The first parameter is the Request ID.
This value MUST be the same as included in the request.

The second parameter is the status code:
| Status        | Code | Description                            |
|---------------|------|----------------------------------------|
| OK            | 0    | The action was performed               |
| NO            | 1    | The request was understood and ignored |
| BAD           | 2    | The request was not understood         |

For the OK status, zero or more additional Parameters are REQUIRED as defined for the Request Type.

For the NO and BAD statuses, one additional Parameter MAY be supplied.
If supplied, the parameter MUST be a map.
The map MAY include implementation-defined key-value pairs indicating information about why the request failed.
Integer keys from 0-127 are reserved and MUST NOT be used except as defined in this spec.
Definitions for these keys are planned to be added in a later draft of the spec.

When using the Link connection type,
Responses MUST be sent to the destination listed as the Source in the LXMF packet,
and MUST use the LXMF `FIELD_REPLY_TO` to indicate the message-id of the request.

#### Notifications

A Notification is an Exchange for which a response is not expected.

The format of a Notification consists of at least one Parameter indicating the event type that triggered the notification.
Each event type defines the format of additional Parameters expected.

### Authentication

All Exchanges MUST be made with the sender authenticated.
The method of authentication differs between connection modes.

A reciever MUST ignore the any Exchanges received with missing or invalid authentication.

Clients and servers SHOULD define a list of identities they expect to recieve messages 

#### Link mode

For Link mode Exchanges, the client MUST authenticate using a Reticulum `PROOF` packet.
The server is authenticated automatically during the establishment of the Reticulum link.

The server MAY save some Exchanges received from an unauthenticated client and process them after the client has authenticated.
When using this behaviour, the server MAY choose to save or discard Exchanges individually per Exchange, but MUST discard all unprocessed exchanges when the link closes.

#### Single mode

For Single mode Exchanges, the sender (client or server) MUST authenticate by including the Exchange as the Content of a valid (signed) LXMF message. The receiver MUST NOT process any Exchange with an invalid signature.
