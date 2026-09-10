"""euclid-pdk - the Python SDK for a euclid server.

Start here::

    from euclid import Euclid

    session = Euclid.for_server("https://euclid.example.com").access().credentials("jens", "secret").login()
    for user in session.list_users().users:
        print(user.user_id, user.email)

This release covers the connection, both signing schemes, EAM, and the ten modules an application
spends its time in - ESM (storage), EQS (queues), ENS (topics), EES (events), EKM (keys), ESS
(secrets), EKV (key-value tables), EAG (the API gateway), EAP (the applications behind it) and ETS
(FTP and SFTP onto a bucket), each reached from the session the login returns::

    session = Euclid.for_server(url).login("jens", "secret")

    session.esm().upload_file(bucket_ern, "2026/q3.pdf", "q3.pdf")
    session.eqs().send_message(queue_ern, '{"order": 17}')
    session.ens().publish_message(topic_ern, '{"order": 17}')
    session.ees().receive_events("invoice-indexer", wait_time=20)
    session.ekm().encrypt(key_id, b"account 4711")
    session.ess().get_secret("db-password").value
    session.ekv().get_item("sessions", {"userId": "jens"})
    session.eap().start_application("order-service")
    session.eag().create_route("orders", "/api/orders", "order-service")
    session.ets().start_server("partner-drop")

The remaining modules - EMO, EMM, EMD and the rest - speak the same protocol through the same client
and are not wrapped yet; each client's ``call(action, payload)`` reaches an action this SDK does not
name, and :class:`euclid.modules.ModuleClient` is what a module of one's own is built on.
"""

from .auth import SignableRequest, SigningScheme
from .dto.com import Variant
from .exceptions import EuclidAuthenticationError, EuclidError, EuclidServiceError
from .http import EuclidHttpClient, Response
from .modules.base import ModuleClient
from .modules.eag import EuclidEag
from .modules.eam import AUTH_AUTO, AUTH_BEARER, AUTH_SIGNATURE, EuclidEam, EuclidSession
from .modules.eap import EuclidEap
from .modules.ees import EuclidEes
from .modules.ekm import EuclidEkm
from .modules.ekv import EuclidEkv
from .modules.ens import EuclidEns
from .modules.eqs import EuclidEqs
from .modules.esm import EuclidEsm, parse_bucket_event
from .modules.ess import EuclidEss
from .modules.ets import EuclidEts

__version__ = "0.2.0"

__all__ = [
    "Euclid",
    "EuclidEam",
    "EuclidSession",
    "EuclidEsm",
    "EuclidEqs",
    "EuclidEns",
    "EuclidEkm",
    "EuclidEss",
    "EuclidEkv",
    "EuclidEag",
    "EuclidEap",
    "EuclidEes",
    "EuclidEts",
    "ModuleClient",
    "EuclidHttpClient",
    "Response",
    "SigningScheme",
    "SignableRequest",
    "Variant",
    "parse_bucket_event",
    "EuclidError",
    "EuclidAuthenticationError",
    "EuclidServiceError",
    "AUTH_AUTO",
    "AUTH_SIGNATURE",
    "AUTH_BEARER",
    "__version__",
]


class Euclid:
    """Entry point: names a server, and hands out the module clients for it.

    Exists so that a caller writes the server's URL once. Only EAM is reached from here, because
    only EAM is reached before logging in; every other module hangs off the session that login
    returns - :meth:`~euclid.modules.eam.EuclidSession.esm` today, ``eqs()`` and the rest as they
    arrive.
    """

    __slots__ = ("_base_url",)

    def __init__(self, base_url: str) -> None:
        if not base_url:
            raise ValueError("base_url must not be empty")
        self._base_url = base_url

    @staticmethod
    def for_server(base_url: str) -> "Euclid":
        """Targets a euclid server, e.g. ``https://euclid.example.com``."""
        return Euclid(base_url)

    @property
    def base_url(self) -> str:
        return self._base_url

    def access(self) -> EuclidEam:
        """Starts a login against this server."""
        return EuclidEam.for_server(self._base_url)

    def eam(self) -> EuclidEam:
        """Alias for :meth:`access`, for callers who think in module names."""
        return self.access()

    def login(self, username: str | None = None, password: str | None = None, *,
              email: str | None = None, namespace: str | None = None, **options: object) -> EuclidSession:
        """Logs in, for the common case that needs no builder.

        ``options`` are passed through to the matching :class:`~euclid.modules.eam.EuclidEam`
        builder methods - ``ca_cert_path``, ``verify``, ``timeout``, ``signing_scheme``, ``auth``,
        ``use_cache``, ``login_path``.
        """
        builder = self.access()
        if username is not None:
            builder.username(username)
        if password is not None:
            builder.password(password)
        if email is not None:
            builder.email(email)
        if namespace is not None:
            builder.namespace(namespace)
        for name, value in options.items():
            setter = getattr(builder, name, None)
            if setter is None or not callable(setter):
                raise TypeError(f"unknown login option {name!r}")
            setter(value)
        return builder.login()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Euclid({self._base_url!r})"
