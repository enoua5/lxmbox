# Security model

Read this before deploying lxmbox and understand what the system does and does not
protect.

A central design decision:

> [!DANGER]
> It is assumed all authorized users of an lxmbox/rnmmp server have control over the host system.
> Generally this means a server should have *one* user, though certain design decisions were made
> to allow more comfortable access to a shared servers if users are sure they completely trust each other.

## Why it is this way

An lxmbox mailbox is single-user by design, and its mail is stored **in the clear** on the host.
LXMF messages are encrypted and authenticated in transit, but delivery means decrypting the payload;
the daemon then writes it to the mailbox store, and the client retrieves and renders it.
Both ends necessarily handle cleartext, and the stored message on disk is (most efficiently) cleartext.
Anyone who can read the host's filesystem — or authenticate to the daemon — can read that mail.

Having mailboxes _locked_ to specific logins would mislead users about this setup and encourage
centralization to a "trusted" host that probably shouldn't be given that trust.
To make this setup clear, and discourage centralization, this per-user mailbox access-control is
not supported, and it not planned to be supported.

## Privacy is achieved by separation

**The privacy boundary is the instance.** If two people need private mailboxes, they run two
lxmbox instances on hosts they each control. This follows Reticulum's own philosophy of
decentralization: you get privacy by owning your infrastructure, not by trusting an access-control
layer inside shared infrastructure.

## Profiles are a convenience, not a boundary

lxmbox replaces the traditional notion of "users" with **profiles**.
A profile can its own default mailbox, its own contacts, etc.
Profiles make a shared high-trust (e.g. household) instance pleasant —
different people (or different devices) may get their own view.

Profiles are **not** a security boundary:
- Switching profiles does not gate access to mail. Any profile on an instance can be pointed at any mailbox the instance holds.
- Profile data lives in the same store on the same host. Reading the disk reads all profiles.

Treat a profile the way you would treat a browser profile on a shared family computer: useful
organization among people who already trust each other with the machine, and nothing more.

## Web frontend accounts are a convenience

The NomadNet and web frontends have their own login systems (username/password, passkeys, TOTP, a Reticulum link identity proof, etc),
and an account may be bound to a default profile.
These accounts exist to provide out-of-band access to a rnmmp client identity in cases where a user
wants to access their mail from a device they haven't imported as a trusted client yet.

These frontends can be disabled in the Docker configuration if this feature is unwanted.
