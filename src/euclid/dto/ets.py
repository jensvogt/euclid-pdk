"""The shapes ETS sends back.

Parsed the same defensive way as every other module's. Field names are the server's, converted to
snake_case; a server is created naming a ``bucket`` and comes back describing a
:attr:`TransferServer.bucket_name` and a :attr:`TransferServer.bucket_ern`, the same way EAP's
deployments do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import _json

__all__ = ["TransferServer"]


@dataclass
class TransferServer:
    """A transfer server definition: an FTP or SFTP endpoint onto an ESM bucket.

    ETS never speaks either protocol itself. It owns the definition - which protocol, which port,
    who may log in, which bucket the files really live in - and euclid's manager is what turns that
    into a running process. So this is a description of what should exist rather than of a socket.

    ``desired_state`` is what somebody asked for and ``state`` is what is observed running, exactly
    as in EAP: the two differing is a server starting up, and differing for long is one that cannot.
    """

    server_id: str = ""
    ern: str = ""
    account_id: str = ""
    region: str = ""
    #: ``FTP`` or ``SFTP``.
    protocol: str = ""
    #: The address the server binds to; ``0.0.0.0`` is every interface.
    address: str = ""
    port: int = 0
    #: The bucket the files live in, by name and by ERN. Named by name at creation.
    bucket_name: str = ""
    bucket_ern: str = ""
    #: The key prefix a logged-in user lands in, which is what makes one bucket serve several
    #: servers without either seeing the other's files.
    home_directory: str = ""
    #: The EAM users who may log in, and the groups whose members may.
    user_ids: list[str] = field(default_factory=list)
    user_groups: list[str] = field(default_factory=list)
    #: Key prefixes presented as directories, for the clients that will not show what they cannot
    #: list.
    directories: list[str] = field(default_factory=list)
    #: ``RUNNING`` or ``STOPPED``, as asked for.
    desired_state: str = ""
    #: ``RUNNING`` or ``STOPPED``, as observed.
    state: str = ""
    #: SFTP only: the private SSH host key, generated on first start when this is empty.
    host_key: str = ""
    #: FTP only: the passive-mode port range, which has to be open in whatever sits in front.
    pasv_min: int = 0
    pasv_max: int = 0
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "TransferServer":
        return TransferServer(
            _json.text(document, "serverId"), _json.text(document, "ern"),
            _json.text(document, "accountId"), _json.text(document, "region"),
            _json.text(document, "protocol"), _json.text(document, "address"),
            _json.number(document, "port"), _json.text(document, "bucketName"),
            _json.text(document, "bucketErn"), _json.text(document, "homeDirectory"),
            _json.strings(document, "userIds"), _json.strings(document, "userGroups"),
            _json.strings(document, "directories"), _json.text(document, "desiredState"),
            _json.text(document, "state"), _json.text(document, "hostKey"),
            _json.number(document, "pasvMin"), _json.number(document, "pasvMax"),
            _json.text(document, "created"), _json.text(document, "modified"))

    @property
    def is_running(self) -> bool:
        """Whether a process is actually answering, which is not the same as having been started."""
        return self.state == "RUNNING"
