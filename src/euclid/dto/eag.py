"""The shapes EAG sends back.

Parsed the same defensive way as every other module's. One thing is not just parsed but gathered:
a listener's certificate arrives flat, as a dozen ``certificate*`` fields alongside the listener's
own, and :class:`Listener` collects them into a :class:`ListenerCertificate` - or into nothing,
when the server said it found none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import _json

__all__ = ["Route", "Listener", "ListenerCertificate", "ListListenersResult"]


@dataclass
class Route:
    """One published path: what the gateway answers for, and where it sends it.

    A route goes to an application euclid runs or to a euclid module, never to both and never to
    neither - :attr:`is_module_route` says which this is. ``methods`` empty means every method.
    """

    route_id: str = ""
    ern: str = ""
    account_id: str = ""
    region: str = ""
    namespace: str = ""
    path: str = ""
    application_id: str = ""
    module_target: str = ""
    module_action: str = ""
    methods: list[str] = field(default_factory=list)
    #: ``NONE``, ``EUCLID`` or ``BASIC`` - what a caller has to present before anything is
    #: forwarded. See :mod:`euclid.modules.eag`.
    authentication: str = ""
    #: Whether the gateway is serving this path at all - see
    #: :meth:`~euclid.modules.eag.EuclidEag.set_route_active`.
    active: bool = True
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Route":
        return Route(
            _json.text(document, "routeId"), _json.text(document, "ern"),
            _json.text(document, "accountId"), _json.text(document, "region"),
            _json.text(document, "namespace"), _json.text(document, "path"),
            _json.text(document, "applicationId"), _json.text(document, "moduleTarget"),
            _json.text(document, "moduleAction"), _json.strings(document, "methods"),
            _json.text(document, "authentication"), _json.flag(document, "active", True),
            _json.text(document, "created"), _json.text(document, "modified"))

    @property
    def is_module_route(self) -> bool:
        """Whether this route reaches a euclid module rather than an application."""
        return bool(self.module_target)


@dataclass
class ListenerCertificate:
    """The certificate an HTTPS listener is actually serving.

    ``generated`` is whether euclid minted it itself because the listener needed something to start
    with. Callers reject a self-signed certificate until they are given it, so which of the two this
    is decides whether the port works for anybody who has not been told about it.
    """

    ern: str = ""
    subject: str = ""
    issuer: str = ""
    serial_number: str = ""
    fingerprint: str = ""
    subject_alt_names: list[str] = field(default_factory=list)
    generated: bool = False
    not_before: str = ""
    not_after: str = ""
    #: Whether ``not_after`` is already in the past, as the server judged it against its own clock.
    expired: bool = False

    @staticmethod
    def from_json(document: Any) -> "ListenerCertificate | None":
        """The certificate a listener entry describes, or None when it describes none.

        A plain HTTP listener has no certificate to be missing, and an HTTPS one whose certificate
        is absent is what a port that never came up looks like - neither is a certificate this can
        describe, and the server says which by ``certificateFound``.
        """
        if not _json.flag(document, "certificateFound"):
            return None
        return ListenerCertificate(
            _json.text(document, "certificateErn"), _json.text(document, "certificateSubject"),
            _json.text(document, "certificateIssuer"), _json.text(document, "certificateSerialNumber"),
            _json.text(document, "certificateFingerprint"),
            _json.strings(document, "certificateSubjectAltNames"),
            _json.flag(document, "certificateGenerated"), _json.text(document, "certificateNotBefore"),
            _json.text(document, "certificateNotAfter"), _json.flag(document, "certificateExpired"))


@dataclass
class Listener:
    """One port the gateway was configured to answer on, and what it speaks.

    ``certificate_name`` is the certificate this listener serves, which is the conventional one for
    its namespace when the configuration named none; ``certificate_configured`` is what the
    configuration actually wrote, so "this listener names its certificate" and "this listener takes
    the conventional one" can be told apart.
    """

    namespace: str = ""
    port: int = 0
    #: ``http`` or ``https``.
    protocol: str = ""
    #: Whether the gateway's ports are bound at all - the same answer for every listener, since it
    #: is a property of the proxy rather than of one port.
    serving: bool = False
    certificate_name: str = ""
    certificate_configured: str = ""
    certificate: ListenerCertificate | None = None

    @staticmethod
    def from_json(document: Any) -> "Listener":
        return Listener(
            _json.text(document, "namespace"), _json.number(document, "port"),
            _json.text(document, "protocol"), _json.flag(document, "serving"),
            _json.text(document, "certificate"), _json.text(document, "certificateConfigured"),
            ListenerCertificate.from_json(document))

    @property
    def names_certificate(self) -> bool:
        """Whether the configuration named this listener's certificate rather than leaving it to
        the convention."""
        return bool(self.certificate_configured)


@dataclass
class ListListenersResult:
    """What the gateway was configured to serve, and whether it is serving it.

    A listener whose port was taken, or whose certificate could not be loaded, is still listed - it
    is the one somebody is looking for - and :attr:`serving` is what says whether anything is
    actually bound.
    """

    listeners: list[Listener] = field(default_factory=list)
    total: int = 0
    serving: bool = False

    @staticmethod
    def from_json(document: Any) -> "ListListenersResult":
        listeners = [Listener.from_json(entry) for entry in _json.documents(document, "listeners")]
        return ListListenersResult(listeners, _json.number(document, "total") or len(listeners),
                                   _json.flag(document, "serving"))
