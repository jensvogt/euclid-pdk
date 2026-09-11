"""EQS - euclid's queue module: queues, messages, leases, dead letter queues.

One object, :class:`EuclidEqs`, built from a session that has already logged in::

    eqs = Euclid.for_server(url).login("jens", "secret").eqs()
    queue = eqs.create_queue("orders")

    eqs.send_message(queue.ern, '{"order": 17}')
    for message in eqs.receive_messages(queue.ern, wait_time=20).messages:
        handle(message.body)
        eqs.delete_message(message.receipt_handle)

Receiving is a lease rather than a read: a message a consumer takes is invisible to every other
consumer until its visibility timeout expires, and deleting it with the receipt handle is what says
the work was done. A consumer that dies instead simply stops holding the lease, and the message
comes back - which is why the delete belongs after the work rather than before it.
"""

from __future__ import annotations

import math
import time
from typing import Any, Mapping

from ..dto.com import Variant
from ..dto.eqs import (CreateQueueResult, ListQueuesResult, Message, MessageAttribute, MessageCount,
                       MessageMetadata, MessagesResult, QueueMetadata, QueueStatusResult,
                       RedriveDlqResult)
from .base import ModuleClient

__all__ = ["EuclidEqs", "TARGET", "DEFAULT_VISIBILITY", "DEFAULT_MAX_RETRIES",
           "DEFAULT_MAX_MESSAGE_LENGTH", "EVERY_NAMESPACE"]

TARGET = "eqs"

#: How long a received message stays invisible before it goes back on the queue, in seconds.
DEFAULT_VISIBILITY = 30

#: How many times a message may be received before it goes to the dead letter queue.
DEFAULT_MAX_RETRIES = 3

#: The largest message a queue accepts, in bytes.
DEFAULT_MAX_MESSAGE_LENGTH = 1024 * 1024

#: What the server reads as "every namespace of this account" where a namespace is asked for. Named
#: rather than written as an empty string, because the two things an empty string could plausibly
#: mean here - the unnamed namespace, and all of them - are very different sizes of mistake.
EVERY_NAMESPACE = ""

#: How long to pause before asking again when the server answered a long poll immediately because
#: it had no slot free to wait in. Only reached when the server is short of threads, which is the
#: moment to ask less often rather than more.
SLOTS_BUSY_BACKOFF = 0.5

#: How close to its deadline a long poll may come back and still count as having been waited out
#: rather than answered early. Absorbs the jitter between the server's clock and this one, so an
#: honoured wait is not followed by a pointless extra request for the last few milliseconds.
HONOURED_WAIT_TOLERANCE = 0.25

#: Added to a long poll's wait to give the response time to travel: the server answers at the end of
#: the window it was asked for, so a timeout of exactly that window would race the network.
LONG_POLL_RESPONSE_MARGIN = 10.0


class EuclidEqs(ModuleClient):
    """EQS's operations, on the credentials of the session that created it.

    Built by :meth:`euclid.EuclidSession.eqs` rather than directly, so that it shares that
    session's identity, namespace and connection settings - and follows them as they change.
    """

    target = TARGET

    # -- queues ------------------------------------------------------------------------------

    def create_queue(self, name: str, visibility: int = DEFAULT_VISIBILITY,
                     max_retries: int = DEFAULT_MAX_RETRIES,
                     max_message_length: int = DEFAULT_MAX_MESSAGE_LENGTH, dlq_name: str = "",
                     delay: int = 0, priority: str = "", internal: bool = False) -> CreateQueueResult:
        """Creates a queue, and returns the ERN everything else names it by.

        :param visibility: how long a received message stays invisible, in seconds, unless
            :meth:`receive_messages` is told otherwise.
        :param max_retries: how many times a message may be received before it is moved to
            ``dlq_name``. A queue without a dead letter queue keeps redelivering.
        :param max_message_length: the largest message this queue accepts, in bytes.
        :param dlq_name: the name of the queue that failed messages end up on.
        :param delay: how long a sent message waits before it can be received at all, in seconds.
        :param priority: the priority every message of this queue gets unless :meth:`send_message`
            overrides it - ``"LOW"``, ``"MIDDLE"`` or ``"HIGH"``, and the server's default when
            left empty.
        :param internal: marks the queue as euclid's own plumbing, which leaves it out of an
            ordinary listing.
        """
        return CreateQueueResult.from_json(self._call("create-queue", {
            "name": name, "visibility": visibility, "maxRetries": max_retries,
            "maxMessageLength": max_message_length, "dlqName": dlq_name, "delay": delay,
            "priority": priority, "internal": internal}))

    def delete_queue(self, ern: str) -> None:
        """Deletes a queue and everything on it."""
        self._call("delete-queue", {"ern": ern})

    def list_queues(self, prefix: str = "", page_size: int = 10, page_index: int = 0,
                    sort_column: str = "name", sort_direction: str = "asc",
                    include_internal: bool = False) -> ListQueuesResult:
        """One page of queues, and how many exist in total.

        euclid's own queues - the delivery queue behind a bucket listener, say - are left out
        unless ``include_internal`` asks for them, so a listing shows what a person would
        recognise. A component looking for the queues it created has to ask.
        """
        return ListQueuesResult.from_json(self._call("list-queues", {
            "prefix": prefix, "pageSize": page_size, "pageIndex": page_index,
            "sortColumn": sort_column, "sortDirection": sort_direction,
            "includeInternal": include_internal}))

    def get_queue_ern(self, name: str) -> str:
        """The ERN of the queue of this name, in the session's account and namespace."""
        return self._text("get-queue-ern", {"name": name}, "ern")

    def get_queue_metadata(self, ern: str) -> QueueMetadata:
        """Where a queue lives and how much is in it."""
        return QueueMetadata.from_json(self._call("get-queue-metadata", {"ern": ern}))

    def purge_queue(self, ern: str) -> None:
        """Deletes every message on a queue, leaving the queue itself in place."""
        self._call("purge-queue", {"ern": ern})

    def purge_all_queues(self, region: str = "", account_id: str = "",
                         namespace: str | None = None) -> None:
        """Deletes every message on every queue of one namespace, which defaults to the session's.

        Exactly as blunt as it sounds, and there is no undo: it exists for a test environment
        between runs rather than for anything that has consumers attached.

        :param namespace: the namespace to empty. Left as None it is the session's own, which is
            the answer that matches what the caller can see; :data:`EVERY_NAMESPACE` empties every
            namespace of the account, which is a different and much larger thing to ask for.
        """
        self._call("purge-all-queues", {
            "region": region or self._session.region,
            "accountId": account_id or self._session.account_id,
            "nameSpace": self._session.namespace if namespace is None else namespace})

    def stop_queue(self, ern: str) -> QueueStatusResult:
        """Stops a queue, so it hands no more messages out.

        Messages already in flight are left alone: their consumer took them before the queue was
        stopped and is still entitled to finish, so deleting one still works. Only new receives are
        refused, with HTTP 409.
        """
        return self._set_queue_status("stop-queue", ern)

    def start_queue(self, ern: str) -> QueueStatusResult:
        """Starts a queue that was stopped, so it hands messages out again."""
        return self._set_queue_status("start-queue", ern)

    def set_queue_visibility(self, ern: str, visibility: int) -> int:
        """Changes a queue's default visibility timeout, and returns the one it now has.

        Only the default changes. Messages already in flight keep the window they were given when
        they were received, so this can neither expire a lease a consumer is still working on nor
        hold back a message its consumer has already given up on.
        """
        return self._number("set-queue-visibility", {"ern": ern, "visibility": visibility}, "visibility")

    def redrive_dlq(self, ern: str, target_ern: str = "") -> RedriveDlqResult:
        """Moves messages out of a dead letter queue and back onto the queues they came from.

        ``ern`` has to name a queue that some other queue points at as its dead letter queue; an
        ordinary queue is refused rather than redriven into itself. A named ``target_ern`` has to be
        one of the queues that feed it, since anything else would be a move rather than a redrive.

        Left unnamed, each message goes back where it came from - and a message whose origin was
        never recorded is left alone rather than guessed at. The result says how many, so a caller
        can name a target and deal with them deliberately.
        """
        return RedriveDlqResult.from_json(self._call("redrive-dlq", {"ern": ern, "targetErn": target_ern}))

    def add_queue_tag(self, ern: str, key: str, value: str) -> None:
        """Tags a queue."""
        self._call("add-queue-tag", {"ern": ern, "key": key, "value": value})

    def set_queue_tag(self, ern: str, key: str, value: str) -> None:
        """Sets the value of a tag the queue already has."""
        self._call("set-queue-tag", {"ern": ern, "key": key, "value": value})

    def delete_queue_tag(self, ern: str, key: str) -> None:
        """Removes a tag from a queue."""
        self._call("delete-queue-tag", {"ern": ern, "key": key})

    def _set_queue_status(self, action: str, ern: str) -> QueueStatusResult:
        """stop-queue and start-queue take the same request and differ only in what they record."""
        return QueueStatusResult.from_json(self._call(action, {"ern": ern}))

    # -- messages ----------------------------------------------------------------------------

    def send_message(self, queue_ern: str, body: str, attributes: Mapping[str, Any] | None = None,
                     priority: str = "", system_attributes: Mapping[str, Any] | None = None) -> str:
        """Puts a message on a queue, and returns the ID the server gave it.

        Two attribute maps, and they are not the same one. ``attributes`` are the sender's own, and
        come back on the received message. ``system_attributes`` are euclid's envelope: they travel
        with the message across every hop, which is what lets a service pass on what it received
        rather than what it happens to know.

        ``priority`` is ``"LOW"``, ``"MIDDLE"`` or ``"HIGH"``; left empty, the message takes the
        queue's own default.
        """
        payload: dict[str, Any] = {"ern": queue_ern, "body": body,
                                   "attributes": Variant.map_to_json(attributes)}
        if system_attributes:
            payload["systemAttributes"] = Variant.map_to_json(system_attributes)
        if priority:
            payload["priority"] = priority
        return self._text("send-message", payload, "messageId")

    def receive_messages(self, queue_ern: str, max_messages: int = 10,
                         wait_time: int = 0) -> MessagesResult:
        """Takes up to ``max_messages`` messages off a queue, waiting up to ``wait_time`` seconds.

        The waiting is the server's, not this client's: it holds the request open until a message
        lands or the time runs out, so an idle queue costs one request for the whole window rather
        than one per poll tick, and a message comes back the instant it is sent.

        The one case that loops is the server declining to wait. It keeps a bounded number of
        long-poll slots - one fewer than it has threads - so that consumers sitting in a wait cannot
        starve the producers trying to send to them; with none free it answers at once with whatever
        is on the queue. That comes back empty with time still on the clock, and the answer is to
        wait a moment and ask again rather than immediately, since asking again at once is what a
        server short of threads does not need.

        With no wait asked for, the queue's depth is checked first and an empty queue costs no
        receive at all - a receive is a write, and one that takes nothing is work the server did
        for nothing.
        """
        if wait_time <= 0:
            if self.get_message_count(queue_ern).available <= 0:
                return MessagesResult()
            return self._receive(queue_ern, max_messages, 0)

        deadline = time.monotonic() + wait_time
        while True:
            remaining = deadline - time.monotonic()
            # Rounded up rather than truncated: the wait travels in whole seconds, and a caller who
            # asked for five would otherwise be given four and a round trip to ask for the fifth.
            result = self._receive(queue_ern, max_messages, max(1, math.ceil(remaining)))
            if result.messages:
                return result

            remaining = deadline - time.monotonic()
            if remaining <= HONOURED_WAIT_TOLERANCE:
                return result
            time.sleep(min(SLOTS_BUSY_BACKOFF, remaining))

    def receive_all_messages(self, queue_ern: str, batch_size: int = 10) -> list[Message]:
        """Takes everything off a queue, a batch at a time, until it comes back empty.

        For draining a queue rather than for consuming one: every message comes back on a lease, so
        a caller that does not delete them will see them all again once the visibility timeout
        expires.
        """
        messages: list[Message] = []
        while True:
            batch = self.receive_messages(queue_ern, batch_size, 0).messages
            if not batch:
                return messages
            messages.extend(batch)

    def list_messages(self, queue_ern: str, page_size: int = 10, page_index: int = 0,
                      sort_column: str = "created", sort_direction: str = "asc") -> MessagesResult:
        """One page of a queue's messages, without receiving them.

        A read rather than a lease: nothing here becomes invisible, nothing counts as a delivery,
        and nothing can be deleted by receipt handle afterwards. It is how a queue is inspected,
        not how it is consumed.
        """
        return MessagesResult.from_json(self._call("list-messages", {
            "queueErn": queue_ern, "pageSize": page_size, "pageIndex": page_index,
            "sortColumn": sort_column, "sortDirection": sort_direction}))

    def delete_message(self, receipt_handle: str) -> None:
        """Deletes a received message, by the handle the receive handed out.

        The handle is a lease: this works while the message's visibility timeout is still running
        and fails once it has expired and the message has gone back on the queue.
        """
        self._call("delete-message", {"receiptHandle": receipt_handle})

    def delete_message_by_id(self, message_id: str) -> None:
        """Deletes a message by its ID, including one nobody has received.

        Bypasses the lease :meth:`delete_message` goes through, which is what makes it able to
        remove a message that is still waiting or still delayed. A euclid extension with no SQS
        equivalent.
        """
        self._call("delete-message", {"messageId": message_id})

    def get_message_count(self, ern: str) -> MessageCount:
        """How many messages a queue holds, by the state they are in."""
        return MessageCount.from_json(self._call("get-message-count", {"ern": ern}))

    def get_message_metadata(self, message_id: str) -> MessageMetadata:
        """Everything about one message except its body."""
        return MessageMetadata.from_json(self._call("get-message-metadata", {"messageId": message_id}))

    def set_message_visibility(self, message_id: str, visibility: int) -> None:
        """Changes how long one message stays invisible - extending a lease a consumer needs longer.

        Sent as ``set-message-visibility``, the name that says what it changes and pairs with
        :meth:`set_queue_visibility`. euclid answers to ``set-visibility`` as well, which is what
        euclid-jdk sends and what a server older than the newer name knows it by; such a server
        refuses this with HTTP 404, and :meth:`~euclid.modules.base.ModuleClient.call` is the way
        round that.
        """
        self._call("set-message-visibility", {"messageId": message_id, "visibility": visibility})

    def get_message_attribute(self, message_id: str, name: str) -> MessageAttribute:
        """One attribute of one message."""
        return MessageAttribute.from_json(self._call("get-message-attribute", {
            "messageId": message_id, "name": name}))

    def set_message_attribute(self, message_id: str, name: str, value: Any) -> MessageAttribute:
        """Sets one attribute of one message, creating it if it was not there.

        The value is a :class:`~euclid.dto.com.Variant` or a plain Python value to be tagged as one.

        The attribute's name travels as ``key`` on this action and as ``name`` on the one that reads
        it back - the server's own asymmetry, reproduced rather than papered over, so that a request
        built from this SDK matches what euclid-cli and euclid-jdk send.
        """
        return MessageAttribute.from_json(self._call("set-message-attribute", {
            "messageId": message_id, "key": name, "value": Variant.of(value).to_json()}))

    # -- monitoring --------------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """EQS's own metrics, as the server collects them. Returned unparsed - the shape belongs to
        the monitoring module rather than to EQS."""
        return self._call("get-metrics")

    def as_internal(self) -> "EuclidEqs":
        """A view of this client whose requests are marked as euclid's own traffic.

        Some calls observe the system rather than use it: reading a queue's depth to report it,
        polling for a heartbeat. They are indistinguishable from real work by their action alone -
        the same ``get-message-count`` is a user's question one moment and a metric collector's poll
        the next - so the caller says which it is, and the server logs and scales accordingly.
        Instrumentation that polls every few seconds would otherwise keep a pool permanently awake
        and make an idle module look busy: the monitoring preventing the thing it exists to measure.

        A separate client rather than a flag on this one, so that no call has to remember to set it
        back - but the same connection, since two clients that differ by a header have no reason to
        differ by a socket. Closing either closes both, which the owning session does anyway.
        """
        return EuclidEqs(self._session, client=self._client, headers={"x-euclid-internal": "true"})

    # -- transport ---------------------------------------------------------------------------

    def _receive(self, queue_ern: str, max_messages: int, wait_time: int) -> MessagesResult:
        """One receive-messages request, held open by the server for ``wait_time`` seconds.

        The client's own timeout is sized for an answer that comes straight back, so a long poll
        gets its own: the request is meant to take as long as the server was asked to hold it, and
        abandoning it at the usual deadline would abandon a request being served correctly.
        """
        timeout = wait_time + LONG_POLL_RESPONSE_MARGIN if wait_time > 0 else None
        return MessagesResult.from_json(self._call(
            "receive-messages", {"ern": queue_ern, "maxCount": max_messages, "waitTime": wait_time},
            timeout))
