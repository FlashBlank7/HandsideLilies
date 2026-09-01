import json

from PySide6.QtCore import QCoreApplication

from lilies.connectors.slack_socket import SlackSocketService


class Runtime:
    def __init__(self):
        self.received = []

    def ingest(self, envelope):
        self.received.append(envelope)
        return {"id": "event-1"}


class FakeTransport:
    instances = []

    def __init__(self, *, on_open, on_text, on_close, on_error):
        self.on_open = on_open
        self.on_text = on_text
        self.on_close = on_close
        self.on_error = on_error
        self.started_url = ""
        self.close_calls = []
        self.order = []
        type(self).instances.append(self)

    def start(self, url):
        self.started_url = str(url)

    def close(self, **kwargs):
        self.close_calls.append(dict(kwargs))
        return True

    def deliver_after_ack(self, raw):
        # The pure RFC 6455 tests verify the real masked wire ACK. This fake
        # records the service boundary: ingestion starts only after that step.
        self.order.append("ack")
        self.on_text(str(raw))


def _connected_service(runtime):
    FakeTransport.instances.clear()
    service = SlackSocketService(runtime, transport_factory=FakeTransport)
    service._enabled = True
    service._generation = 1
    service._on_url_ready("1", "wss://wss-primary.slack.test/link/?ticket=opaque")
    transport = FakeTransport.instances[-1]
    transport.on_open()
    return service, transport


def test_socket_service_ingests_only_after_transport_ack_boundary():
    app = QCoreApplication.instance() or QCoreApplication([])
    runtime = Runtime()
    service, transport = _connected_service(runtime)

    def ingest(envelope):
        transport.order.append("ingest")
        return {"id": envelope["payload"]["event_id"]}

    runtime.ingest = ingest
    transport.deliver_after_ack(
        json.dumps(
            {
                "type": "events_api",
                "envelope_id": "env-1",
                "payload": {"event_id": "event-1", "event": {"type": "message"}},
            }
        )
    )

    assert transport.started_url.startswith("wss://")
    assert transport.order == ["ack", "ingest"]
    assert service.status == {
        "state": "connected",
        "connected": True,
        "attempt": 0,
        "error": "",
    }
    service.stop()
    app.processEvents()


def test_stale_transport_callbacks_cannot_revive_stopped_service():
    app = QCoreApplication.instance() or QCoreApplication([])
    runtime = Runtime()
    service, transport = _connected_service(runtime)
    generation = service._generation

    service.stop()
    transport.on_text(
        json.dumps(
            {
                "type": "events_api",
                "envelope_id": "late",
                "payload": {"event_id": "late"},
            }
        )
    )
    transport.on_error("late failure")
    transport.on_close(None, "late close")
    app.processEvents()

    assert generation != service._generation
    assert runtime.received == []
    assert service.transport is None
    assert service.status == {
        "state": "stopped",
        "connected": False,
        "attempt": 0,
        "error": "",
    }
    assert transport.close_calls == [{"wait": False}]


def test_transport_failure_schedules_exactly_one_generation_scoped_reconnect():
    app = QCoreApplication.instance() or QCoreApplication([])
    runtime = Runtime()
    service, transport = _connected_service(runtime)
    scheduled = []
    service._schedule = lambda milliseconds=None: scheduled.append(milliseconds)

    transport.on_error("TLS peer closed")
    transport.on_close(None, "")
    transport.on_close(None, "duplicate callback")
    app.processEvents()

    assert service.status == {
        "state": "reconnecting",
        "connected": False,
        "attempt": 1,
        "error": "TLS peer closed",
    }
    assert scheduled == [None]
    service.stop()


def test_stale_url_result_does_not_clear_current_request_guard():
    app = QCoreApplication.instance() or QCoreApplication([])
    FakeTransport.instances.clear()
    service = SlackSocketService(Runtime(), transport_factory=FakeTransport)
    service._enabled = True
    service._generation = 2
    service._requesting = True

    service._on_url_ready("1", "wss://stale.slack.test/link")
    app.processEvents()

    assert service._requesting is True
    assert service.transport is None
    assert FakeTransport.instances == []
    service.stop()
