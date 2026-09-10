"""ETS - euclid's transfer service: the FTP and SFTP endpoints onto a bucket.

One object, :class:`EuclidEts`, built from a session that has already logged in::

    ets = Euclid.for_server(url).login("jens", "secret").ets()

    ets.create_server("partner-drop", bucket="invoices", port=2222,
                      user_groups=["partners"], home_directory="incoming/")
    ets.start_server("partner-drop")

ETS never speaks either protocol itself. It owns the definitions - which protocol, which port, which
EAM users and groups may log in, which ESM bucket the files really live in - and starting one is
nothing more than writing a desired state onto a definition. euclid's manager is what turns that
into a running process, and the process reads its own definition back from here.

So a file uploaded over FTP is an object in a bucket, with the events and the lifecycle every other
object has: this is a protocol somebody's existing tooling already speaks, put in front of storage,
rather than a second place files live.

Every action here is administrator-only, server-side. :attr:`~euclid.EuclidSession.is_admin` says
whether the logged-in user is one, though the server enforces it regardless.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..dto.ets import TransferServer
from .base import ModuleClient

__all__ = ["EuclidEts", "TARGET", "FTP", "SFTP", "EVERY_INTERFACE",
           "DEFAULT_PASV_MIN", "DEFAULT_PASV_MAX"]

TARGET = "ets"

#: The protocols a transfer server can speak. Fixed when it is created: which one it is decides
#: which process the manager starts, so changing it would be a different server.
FTP = "FTP"
SFTP = "SFTP"

#: What an address of nothing in particular means - every interface the host has.
EVERY_INTERFACE = "0.0.0.0"

#: The passive-mode port range an FTP server takes unless told otherwise. Whatever sits in front of
#: euclid has to let these through as well as the control port, which is what makes the range worth
#: naming rather than leaving to chance.
DEFAULT_PASV_MIN = 6000
DEFAULT_PASV_MAX = 6100


class EuclidEts(ModuleClient):
    """ETS's operations, on the credentials of the session that created it.

    Built by :meth:`euclid.EuclidSession.ets` rather than directly, so that it shares that
    session's identity, namespace and connection settings - and follows them as they change.
    """

    target = TARGET

    # -- definitions -------------------------------------------------------------------------

    def create_server(self, server_id: str, bucket: str, port: int, protocol: str = SFTP,
                      address: str = EVERY_INTERFACE, home_directory: str = "",
                      user_ids: Iterable[str] = (), user_groups: Iterable[str] = (),
                      directories: Iterable[str] = (), host_key: str = "",
                      pasv_min: int = DEFAULT_PASV_MIN,
                      pasv_max: int = DEFAULT_PASV_MAX) -> TransferServer:
        """Defines a transfer server, stopped, and returns it as it was stored.

        Nothing listens yet: a new server's desired state is ``STOPPED``, so
        :meth:`start_server` is what puts it in service. Refused with HTTP 409 if the ID or the port
        is taken, and with 404 if the bucket is not there.

        :param bucket: the name of the bucket the files live in - a name, not an ERN.
        :param port: the port to listen on, 1 to 65535.
        :param protocol: :data:`SFTP` or :data:`FTP`. Fixed from here on.
        :param address: the interface to bind to; :data:`EVERY_INTERFACE` by default.
        :param home_directory: the key prefix a logged-in user lands in, which is what lets one
            bucket serve several servers without either seeing the other's files.
        :param user_ids: the EAM users who may log in.
        :param user_groups: the groups whose members may - usually the better answer, since it
            outlives the individual accounts.
        :param directories: key prefixes to present as directories, for the clients that will not
            show what they cannot list.
        :param host_key: SFTP only: the private SSH host key. Generated on first start when empty -
            which is what to leave it as unless a key that clients already trust has to be kept.
        :param pasv_min: FTP only: the low end of the passive port range.
        :param pasv_max: FTP only: the high end.
        """
        return self._server("create-server", {
            "serverId": server_id, "protocol": protocol, "port": port, "bucket": bucket,
            "address": address, "homeDirectory": home_directory, "userIds": list(user_ids),
            "userGroups": list(user_groups), "directories": list(directories),
            "hostKey": host_key, "pasvMin": pasv_min, "pasvMax": pasv_max})

    def update_server(self, server_id: str, address: str | None = None, port: int | None = None,
                      bucket: str | None = None, home_directory: str | None = None,
                      user_ids: Iterable[str] | None = None,
                      user_groups: Iterable[str] | None = None,
                      directories: Iterable[str] | None = None, host_key: str | None = None,
                      pasv_min: int | None = None, pasv_max: int | None = None) -> TransferServer:
        """Changes a transfer server's definition. Only what this names changes.

        The distinction the server draws is between a field being sent and not being sent, rather
        than between its values - so leaving ``home_directory`` as None leaves the stored one alone,
        while passing ``""`` puts logins back at the root of the bucket. A list that is named
        replaces the stored one rather than adding to it.

        The protocol is not here: which one a server speaks decides which process runs it, so
        changing it would be a different server. Neither is the state - see :meth:`start_server`.

        A running server keeps running on its old definition until it is restarted, since the
        process reads this once at startup.
        """
        payload: dict[str, Any] = {"serverId": server_id}
        if address is not None:
            payload["address"] = address
        if port is not None:
            payload["port"] = port
        if bucket is not None:
            payload["bucket"] = bucket
        if home_directory is not None:
            payload["homeDirectory"] = home_directory
        if user_ids is not None:
            payload["userIds"] = list(user_ids)
        if user_groups is not None:
            payload["userGroups"] = list(user_groups)
        if directories is not None:
            payload["directories"] = list(directories)
        if host_key is not None:
            payload["hostKey"] = host_key
        if pasv_min is not None:
            payload["pasvMin"] = pasv_min
        if pasv_max is not None:
            payload["pasvMax"] = pasv_max
        return self._server("update-server", payload)

    def list_servers(self, prefix: str = "") -> list[TransferServer]:
        """The transfer servers whose ID starts with a prefix; an empty prefix lists them all."""
        servers = self._call("list-servers", {"prefix": prefix}).get("servers")
        return [TransferServer.from_json(s) for s in servers] if isinstance(servers, list) else []

    def get_server(self, server_id: str) -> TransferServer:
        """One transfer server, by its ID, with the state it is observed in."""
        return self._server("get-server", {"serverId": server_id})

    def delete_server(self, server_id: str) -> None:
        """Removes a transfer server's definition. Stop it first - this does not.

        The bucket and its objects are untouched: what is deleted is the endpoint onto them.
        """
        self._call("delete-server", {"serverId": server_id})

    # -- running -----------------------------------------------------------------------------

    def start_server(self, server_id: str) -> TransferServer:
        """Asks for a transfer server to run, and returns it as it stands.

        Asking is all this does: the desired state changes here and the manager acts on it, so the
        server in the answer is usually still ``STOPPED`` - it says what was asked for, not what has
        happened yet.
        """
        return self._server("start-server", {"serverId": server_id})

    def stop_server(self, server_id: str) -> TransferServer:
        """Asks for a transfer server to stop, and returns it as it stands.

        A client mid-transfer is not this call's concern: the process is stopped, and whatever was
        in flight fails the way a dropped connection does.
        """
        return self._server("stop-server", {"serverId": server_id})

    # -- monitoring --------------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """ETS's own metrics, as the server collects them. Returned unparsed - the shape belongs to
        the monitoring module rather than to ETS."""
        return self._call("get-metrics")

    def _server(self, action: str, payload: dict[str, Any]) -> TransferServer:
        """The actions that answer with one transfer server, which is all of them but the listing."""
        return TransferServer.from_json(self._call(action, payload))
