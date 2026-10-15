"""AI 后台线程：信号顺序、失败、列模型、取消。"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator

import httpx

from pdfreader.services.ai.client import ChatClient
from pdfreader.workers.ai_worker import AiWorker


def _client(handler) -> ChatClient:  # type: ignore[no-untyped-def]
    return ChatClient("https://api.example.com/v1", "k", transport=httpx.MockTransport(handler))


def _sse(*parts: dict[str, object]) -> bytes:
    lines = [f"data: {json.dumps(p)}\n\n" for p in parts] + ["data: [DONE]\n\n"]
    return "".join(lines).encode("utf-8")


def _stream(body: bytes) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def test_chat_emits_deltas_then_finished(qtbot) -> None:  # type: ignore[no-untyped-def]
    body = _sse(
        {"choices": [{"delta": {"reasoning_content": "想"}}]},
        {"choices": [{"delta": {"content": "答"}}]},
        {"choices": [{"delta": {"content": "案"}}]},
    )
    worker = AiWorker()
    worker.setup_chat(_client(lambda r: _stream(body)), [], model="m")
    deltas: list[str] = []
    reasoning: list[str] = []
    worker.delta.connect(deltas.append)
    worker.reasoning.connect(reasoning.append)
    with qtbot.waitSignal(worker.finished_text, timeout=5000) as blocker:
        worker.start()
    worker.wait(2000)
    assert blocker.args == ["答案"]
    assert deltas == ["答", "案"]
    assert reasoning == ["想"]


def test_chat_failure(qtbot) -> None:  # type: ignore[no-untyped-def]
    worker = AiWorker()
    worker.setup_chat(_client(lambda r: httpx.Response(401, json={})), [], model="m")
    with qtbot.waitSignal(worker.failed, timeout=5000) as blocker:
        worker.start()
    worker.wait(2000)
    assert "API Key" in blocker.args[0]


def test_unexpected_exception_reported(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = _client(lambda r: _stream(b""))

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ValueError("x")

    monkeypatch.setattr(client, "stream_chat", boom)
    worker = AiWorker()
    worker.setup_chat(client, [], model="m")
    with qtbot.waitSignal(worker.failed, timeout=5000) as blocker:
        worker.start()
    worker.wait(2000)
    assert "ValueError" in blocker.args[0]


def test_no_client(qtbot) -> None:  # type: ignore[no-untyped-def]
    worker = AiWorker()
    with qtbot.waitSignal(worker.failed, timeout=5000):
        worker.start()
    worker.wait(2000)


def test_list_models(qtbot) -> None:  # type: ignore[no-untyped-def]
    worker = AiWorker()
    worker.setup_models(_client(lambda r: httpx.Response(200, json={"data": [{"id": "m1"}]})))
    with qtbot.waitSignal(worker.modelsListed, timeout=5000) as blocker:
        worker.start()
    worker.wait(2000)
    assert blocker.args == [["m1"]]


def test_shutdown_cancels_stream(qtbot) -> None:  # type: ignore[no-untyped-def]
    started = threading.Event()
    release = threading.Event()

    class Slow(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
            started.set()
            release.wait(5)
            yield b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'

    worker = AiWorker()
    worker.setup_chat(
        _client(
            lambda r: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Slow()
            )
        ),
        [],
        model="m",
    )
    finished: list[str] = []
    worker.finished_text.connect(finished.append)
    with qtbot.waitSignal(worker.cancelled, timeout=5000):
        worker.start()
        assert started.wait(5)
        worker.shutdown()
        release.set()
    worker.wait(2000)
    assert worker.stopped
    assert finished == []
