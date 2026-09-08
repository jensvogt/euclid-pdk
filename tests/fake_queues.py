"""A euclid queue module small enough to read, for the EQS tests.

Messages in a list, and the two things about a queue that a client can get wrong implemented rather
than stubbed: a receive hands out a receipt handle and makes the message invisible to the next one,
and a long poll is either held open for the window it was asked for or declined outright. Those are
what :meth:`euclid.modules.eqs.EuclidEqs.receive_messages` is written against, so they are what a
test of it has to have.

Registered on a :class:`fake_gateway.FakeGateway`, which authenticates the requests before any of
this is reached.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from fake_gateway import FakeGateway, RecordedRequest


def queue_ern(name: str) -> str:
    return f"ern:euclid:eqs:eu-central-1:000000000000:queue/{name}"


class FakeQueues:
    """The state a queue module keeps, and the actions that read and write it."""

    def __init__(self) -> None:
        #: queue ERN -> the messages on it, in the order they were sent.
        self.messages: dict[str, list[dict[str, Any]]] = {}
        #: The ``waitTime`` of every receive-messages request, in order.
        self.waits: list[int] = []
        #: How many of the next long polls to decline - answer at once rather than wait, as the
        #: server does when it has no long-poll slot free.
        self.decline_waits = 0
        #: The longest a held long poll actually waits here. The client's window is what is being
        #: tested, not the wall clock, so the stand-in never holds one for more than this.
        self.max_hold = 1.0
        self._lock = threading.Lock()
        self._sent = 0

    # -- setup -------------------------------------------------------------------------------

    def install(self, gateway: FakeGateway) -> "FakeQueues":
        """Registers every action this stand-in implements."""
        handlers = {
            "send-message": self.send_message,
            "receive-messages": self.receive_messages,
            "delete-message": self.delete_message,
            "get-message-count": self.get_message_count,
            "list-messages": self.list_messages,
            "purge-queue": self.purge_queue,
        }
        for action, handler in handlers.items():
            gateway.on("eqs", action, handler)
        return self

    # -- actions -----------------------------------------------------------------------------

    def send_message(self, request: RecordedRequest) -> tuple[int, Any]:
        body = request.json()
        with self._lock:
            self._sent += 1
            message = {
                "ern": f"{body['ern']}/message/{self._sent}",
                "queueErn": body["ern"],
                "messageId": f"message-{self._sent}",
                "status": "AVAILABLE",
                "priority": body.get("priority", "MIDDLE"),
                "body": body.get("body", ""),
                "receiptHandle": "",
                "size": len(body.get("body", "")),
                "receivedCount": 0,
                "contentType": "application/json",
                "attributes": body.get("attributes", {}),
                "systemAttributes": body.get("systemAttributes", {}),
            }
            self.messages.setdefault(body["ern"], []).append(message)
            return 200, {"messageId": message["messageId"]}

    def receive_messages(self, request: RecordedRequest) -> tuple[int, Any]:
        body = request.json()
        wait = int(body.get("waitTime", 0))
        with self._lock:
            self.waits.append(wait)
            if self.decline_waits:
                # No slot free: whatever is on the queue comes back at once rather than the request
                # queueing behind the waiters. Here, that is nothing.
                self.decline_waits -= 1
                return 200, {"messages": [], "total": 0}

            available = [m for m in self.messages.get(body.get("ern", ""), [])
                         if m["status"] == "AVAILABLE"]
            taken = available[:int(body.get("maxCount", 10))]
            for index, message in enumerate(taken):
                message["status"] = "INVISIBLE"
                message["receiptHandle"] = f"receipt-{message['messageId']}-{index}"
                message["receivedCount"] += 1

        if not taken and wait > 0:
            # The server holding the request open for the window it was asked for, which is what a
            # client must not abandon at its ordinary timeout.
            time.sleep(min(wait, self.max_hold))
        return 200, {"messages": taken, "total": len(taken)}

    def delete_message(self, request: RecordedRequest) -> tuple[int, Any]:
        body = request.json()
        with self._lock:
            for ern, messages in self.messages.items():
                for message in messages:
                    if (body.get("receiptHandle") and message["receiptHandle"] == body["receiptHandle"]) \
                            or (body.get("messageId") and message["messageId"] == body["messageId"]):
                        messages.remove(message)
                        return 200, {"messageId": message["messageId"], "queueErn": ern}
        return 404, {"error": "Message not found"}

    def get_message_count(self, request: RecordedRequest) -> tuple[int, Any]:
        ern = request.json().get("ern", "")
        messages = self.messages.get(ern, [])
        return 200, {"ern": ern,
                     "available": len([m for m in messages if m["status"] == "AVAILABLE"]),
                     "delayed": len([m for m in messages if m["status"] == "DELAYED"]),
                     "invisible": len([m for m in messages if m["status"] == "INVISIBLE"]),
                     "total": len(messages)}

    def list_messages(self, request: RecordedRequest) -> tuple[int, Any]:
        messages = self.messages.get(request.json().get("queueErn", ""), [])
        return 200, {"messages": messages, "total": len(messages)}

    def purge_queue(self, request: RecordedRequest) -> tuple[int, Any]:
        self.messages.pop(request.json().get("ern", ""), None)
        return 200, {}
