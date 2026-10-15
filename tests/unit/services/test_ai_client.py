"""AI 客户端：SSE 解析、思考内容、错误映射、取消（httpx.MockTransport，不联网）。"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator

import httpx
import pytest

from pdfreader.services.ai.client import (
    AiError,
    ChatClient,
    StreamEvent,
    ThinkSplitter,
    describe_status,
)

BASE = "https://api.example.com/v1"


def _sse(*chunks: object, done: bool = True, extra: str = "") -> bytes:
    lines = [extra] if extra else []
    for chunk in chunks:
        lines.append("data: " + (chunk if isinstance(chunk, str) else json.dumps(chunk)))
        lines.append("")
    if done:
        lines.append("data: [DONE]")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _delta(content: str | None = None, reasoning: str | None = None) -> dict[str, object]:
    delta: dict[str, object] = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    return {"choices": [{"index": 0, "delta": delta}]}


def _client(handler: Callable[[httpx.Request], httpx.Response], key: str = "sk-test") -> ChatClient:
    return ChatClient(BASE, key, transport=httpx.MockTransport(handler))


def _sse_response(body: bytes) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def _collect(client: ChatClient) -> list[StreamEvent]:
    return list(client.stream_chat([{"role": "user", "content": "hi"}], model="m"))


def _text(events: list[StreamEvent], kind: str = "content") -> str:
    return "".join(e.text for e in events if e.kind == kind)


def test_stream_basic_request_shape() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _sse_response(_sse(_delta("你"), _delta("好")))

    events = _collect(_client(handler))
    assert _text(events) == "你好"
    assert seen["url"] == f"{BASE}/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    # 最小参数：不发 temperature / max_tokens
    assert seen["body"] == {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }


def test_stream_optional_params_and_no_key() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _sse_response(_sse(_delta("x")))

    client = ChatClient(BASE, "", transport=httpx.MockTransport(handler))
    list(client.stream_chat([], model="m", temperature=0.3, max_tokens=50))
    assert seen["auth"] is None
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0.3
    assert body["max_tokens"] == 50


def test_stream_tolerates_comments_empty_choices_and_garbage() -> None:
    body = _sse(
        {"choices": []},
        "not json",
        [1, 2],
        {"choices": [{"delta": None}]},
        _delta("ok"),
        {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
        extra=": OPENROUTER PROCESSING",
    )
    events = _collect(_client(lambda r: _sse_response(body)))
    assert _text(events) == "ok"
    usage = [e for e in events if e.kind == "usage"]
    assert usage and usage[0].prompt_tokens == 7 and usage[0].completion_tokens == 3


def test_stream_without_done_marker_still_finishes() -> None:
    body = _sse(_delta("a"), _delta("b"), done=False)
    assert _text(_collect(_client(lambda r: _sse_response(body)))) == "ab"


def test_reasoning_content_separated() -> None:
    body = _sse(_delta(reasoning="想一想"), _delta(reasoning="……"), _delta("答案"))
    events = _collect(_client(lambda r: _sse_response(body)))
    assert _text(events, "reasoning") == "想一想……"
    assert _text(events) == "答案"


def test_inline_think_tags_split_across_chunks() -> None:
    body = _sse(_delta("<thi"), _delta("nk>推理"), _delta("过程</th"), _delta("ink>结论"))
    events = _collect(_client(lambda r: _sse_response(body)))
    assert _text(events, "reasoning") == "推理过程"
    assert _text(events) == "结论"


def test_think_splitter_edge_cases() -> None:
    s = ThinkSplitter()
    assert s.feed("a < b") == [("content", "a < b")]
    assert s.feed("<") == []
    assert s.feed("x") == [("content", "<x")]
    s2 = ThinkSplitter()
    assert s2.feed("<think>未闭合") == [("reasoning", "未闭合")]
    assert s2.feed("</") == []
    assert s2.flush() == [("reasoning", "</")]
    assert s2.flush() == []


def test_non_streaming_json_fallback() -> None:
    payload = {"choices": [{"message": {"role": "assistant", "content": "<think>r</think>全文"}}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    events = _collect(_client(handler))
    assert _text(events) == "全文"
    assert _text(events, "reasoning") == "r"


def test_non_streaming_garbage_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, content=b"<html>")

    with pytest.raises(AiError, match="无法识别"):
        _collect(_client(handler))


@pytest.mark.parametrize(
    ("status", "needle"),
    [
        (401, "API Key"),
        (403, "API Key"),
        (404, "/v1"),
        (413, "过长"),
        (429, "额度"),
        (400, "400"),
        (502, "服务端出错"),
        (418, "418"),
    ],
)
def test_http_errors_mapped(status: int, needle: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "bad sk-test thing"}})

    with pytest.raises(AiError) as info:
        _collect(_client(handler))
    assert needle in str(info.value)
    assert info.value.status == status
    assert "sk-test" not in str(info.value)  # Key 脱敏
    assert "***" in str(info.value)


def test_error_detail_variants() -> None:
    assert "plain" in describe_status(500, "plain")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "顶层 message"})

    with pytest.raises(AiError, match="顶层 message"):
        _collect(_client(handler))

    def handler2(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "字符串错误"})

    with pytest.raises(AiError, match="字符串错误"):
        _collect(_client(handler2))

    def handler3(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b"x" * 1000)

    with pytest.raises(AiError, match="…"):
        _collect(_client(handler3))


def test_error_inside_stream() -> None:
    body = _sse(_delta("部分"), {"error": {"message": "context too long"}})
    with pytest.raises(AiError, match="context too long"):
        _collect(_client(lambda r: _sse_response(body)))


@pytest.mark.parametrize(
    ("exc", "needle"),
    [
        (httpx.ConnectTimeout("t"), "超时"),
        (httpx.ReadTimeout("t"), "等待模型响应超时"),
        (httpx.ProxyError("p"), "代理"),
        (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]"), "证书"),
        (httpx.ConnectError("refused"), "无法连接到 api.example.com"),
        (httpx.UnsupportedProtocol("x"), "格式不正确"),
        (httpx.RemoteProtocolError("x"), "网络请求失败"),
    ],
)
def test_transport_errors_mapped(exc: Exception, needle: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    with pytest.raises(AiError, match=needle):
        _collect(_client(handler))


def test_stream_interrupted_midway() -> None:
    class Broken(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield _sse(_delta("前半"), done=False)
            raise httpx.ReadError("reset")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Broken())

    with pytest.raises(AiError, match="连接中断"):
        _collect(_client(handler))


def test_missing_base_or_model() -> None:
    with pytest.raises(AiError, match="Base URL"):
        list(ChatClient("").stream_chat([], model="m"))
    with pytest.raises(AiError, match="模型"):
        list(ChatClient(BASE).stream_chat([], model=""))


def test_cancel_stops_stream_silently() -> None:
    started = threading.Event()
    release = threading.Event()

    class Slow(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield _sse(_delta("第一块"), done=False)
            started.set()
            release.wait(5)
            yield _sse(_delta("不该出现"), done=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Slow())

    client = _client(handler)
    out: list[str] = []

    def consume() -> None:
        for event in client.stream_chat([], model="m"):
            out.append(event.text)

    thread = threading.Thread(target=consume)
    thread.start()
    assert started.wait(5)
    client.cancel()
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert client.cancelled
    assert out == ["第一块"]


def test_cancel_before_send_returns_nothing() -> None:
    client = _client(lambda r: _sse_response(_sse(_delta("x"))))
    client.cancel()
    assert _collect(client) == []


def test_list_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{BASE}/models"
        return httpx.Response(
            200, json={"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}, {"x": 1}, "bad"]}
        )

    assert _client(handler).list_models() == ["a", "b"]


def test_list_models_errors() -> None:
    with pytest.raises(AiError, match="401"):
        _client(lambda r: httpx.Response(401, json={})).list_models()
    with pytest.raises(AiError, match="无法识别"):
        _client(lambda r: httpx.Response(200, content=b"nope")).list_models()

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(AiError, match="无法连接"):
        _client(boom).list_models()
    # 也接受直接返回数组的实现
    assert _client(lambda r: httpx.Response(200, json=[{"id": "z"}])).list_models() == ["z"]


def test_base_url_is_normalized() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": []})

    ChatClient(BASE + "/chat/completions/", transport=httpx.MockTransport(handler)).list_models()
    assert seen == [f"{BASE}/models"]
