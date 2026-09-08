"""euclid-pdk - the Python SDK for a euclid server.

Start here::

    from euclid import Euclid

    session = Euclid.for_server("https://euclid.example.com").access().credentials("jens", "secret").login()
    for user in session.list_users().users:
        print(user.user_id, user.email)

This release covers the connection, both signing schemes, and EAM. The other modules - EQS, ESM,
ENS, EKM, ESS and the rest - speak the same protocol through the same client, and are not wrapped
yet; :meth:`euclid.modules.eam.EuclidSession.call` reaches any EAM action this SDK does not name.
"""

from .auth import SignableRequest, SigningScheme
from .exceptions import EuclidAuthenticationError, EuclidError, EuclidServiceError
from .http import EuclidHttpClient, Response
from .modules.eam import AUTH_AUTO, AUTH_BEARER, AUTH_SIGNATURE, EuclidEam, EuclidSession

__version__ = "0.1.0"

__all__ = [
    "Euclid",
    "EuclidEam",
    "EuclidSession",
    "EuclidHttpClient",
    "Response",
    "SigningScheme",
    "SignableRequest",
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

    Exists so that a caller writes the server's URL once. Today there is one module behind it;
    the point of the indirection is that ``euclid.eqs()`` and the rest arrive without the calling
    code changing shape.
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
