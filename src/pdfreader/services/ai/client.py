"""OpenAI Chat Completions 兼容客户端（httpx，同步流式，供工作线程调用）。

只发最小参数（``model`` / ``messages`` / ``stream``，``temperature`` 仅在用户设置时发），
最大程度兼容各家。流式响应逐行解析 ``data:`` 行、``[DONE]`` 结束，容忍空行、注释行与空 ``choices``；
思考内容统一拆成 ``reasoning`` 事件——``delta.reasoning_content``（DeepSeek / 千问 / 硅基流动）
与正文里内联的 ``<think>…</think>``（部分本机模型）都算。

错误一律转成带中文原因的 :class:`AiError`；信息里从不回显 API Key。
"""

from __future__ import annotations

import contextlib
import json
import math
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from pdfreader.services.ai.provider import normalize_base_url

__all__ = ["AiError", "ChatClient", "StreamEvent", "ThinkSplitter", "describe_status"]

# 连接 10 秒；推理模型首字可能很慢，读超时放宽到 120 秒
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)


class AiError(Exception):
    """请求失败（消息已是给用户看的中文）。"""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class StreamEvent:
    """流式事件：正文增量 / 思考增量 / 用量。"""

    kind: Literal["content", "reasoning", "usage"]
    text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


def describe_status(status: int, detail: str = "") -> str:
    """把 HTTP 状态码翻译成中文原因（附上服务端给出的说明）。"""
    if status in (401, 403):
        msg = f"API Key 无效或没有权限（{status}）"
    elif status == 404:
        msg = "地址或模型名不正确（404）：请检查 Base URL 是否缺少 /v1 等路径，以及模型名是否存在"
    elif status == 413:
        msg = "发送的内容过长（413）：请缩小范围后重试"
    elif status == 429:
        msg = "请求过于频繁或账户额度不足（429）"
    elif status == 400:
        msg = "请求被服务端拒绝（400）：模型名或参数可能不受支持"
    elif status >= 500:
        msg = f"服务端出错（{status}），请稍后重试"
    else:
        msg = f"请求失败（HTTP {status}）"
    return f"{msg}\n服务端说明：{detail}" if detail else msg


def _error_detail(body: bytes, api_key: str) -> str:
    """从错误响应体里取 ``error.message``（截断、去掉可能回显的 Key）。"""
    text = body.decode("utf-8", errors="replace").strip()
    with contextlib.suppress(ValueError):
        data = json.loads(text)
        if isinstance(data, dict):
            err = data.get("error", data)
            if isinstance(err, dict):
                text = str(err.get("message") or err.get("msg") or text)
            elif isinstance(err, str):
                text = err
            elif isinstance(data.get("message"), str):
                text = data["message"]
    if api_key:
        text = text.replace(api_key, "***")
    text = " ".join(text.split())
    return text[:300] + ("…" if len(text) > 300 else "")


class ThinkSplitter:
    """把正文流里内联的 ``<think>…</think>`` 拆成思考 / 正文两路（标签可能被切在两个块之间）。"""

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._thinking = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        self._buf += text
        while self._buf:
            tag = self.CLOSE if self._thinking else self.OPEN
            kind = "reasoning" if self._thinking else "content"
            idx = self._buf.find(tag)
            if idx >= 0:
                if idx:
                    out.append((kind, self._buf[:idx]))
                self._buf = self._buf[idx + len(tag) :]
                self._thinking = not self._thinking
                continue
            # 末尾可能是半个标签：留到下一块再判断
            keep = 0
            for n in range(min(len(tag) - 1, len(self._buf)), 0, -1):
                if tag.startswith(self._buf[-n:]):
                    keep = n
                    break
            emit = self._buf[: len(self._buf) - keep]
            if emit:
                out.append((kind, emit))
            self._buf = self._buf[len(self._buf) - keep :]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        if not self._buf:
            return []
        kind = "reasoning" if self._thinking else "content"
        rest, self._buf = self._buf, ""
        return [(kind, rest)]


class ChatClient:
    """一个 OpenAI 兼容服务的客户端。``cancel()`` 可在别的线程调用以中断流式请求。"""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base = normalize_base_url(base_url)
        self._key = api_key
        headers = {"Accept": "application/json, text/event-stream"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        # trust_env=True：跟随系统 / 环境变量代理（Windows 下 urllib 会读注册表里的系统代理）
        self._http = httpx.Client(
            headers=headers, timeout=timeout, transport=transport, trust_env=True
        )
        self._lock = threading.Lock()
        self._response: httpx.Response | None = None
        self._cancelled = False

    # ------------------------------------------------------------------ 生命周期
    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._http.close()

    def cancel(self) -> None:
        """中断进行中的请求（关闭响应流，阻塞的读取会立即返回 / 抛错）。"""
        with self._lock:
            self._cancelled = True
            response = self._response
        if response is not None:
            with contextlib.suppress(Exception):
                response.close()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    # ------------------------------------------------------------------ 接口
    def list_models(self) -> list[str]:
        """``GET /models`` → 模型 id 列表（已排序）。"""
        try:
            response = self._http.get(self._url("/models"))
        except httpx.HTTPError as exc:
            raise self._transport_error(exc) from None
        if response.status_code != 200:
            raise AiError(
                describe_status(response.status_code, _error_detail(response.content, self._key)),
                status=response.status_code,
            )
        try:
            data = response.json()
        except ValueError:
            raise AiError("模型列表的响应格式无法识别") from None
        items = data.get("data") if isinstance(data, dict) else data
        ids = {
            str(item["id"])
            for item in (items if isinstance(items, list) else [])
            if isinstance(item, dict) and item.get("id")
        }
        return sorted(ids)

    def embeddings(self, texts: list[str], *, model: str) -> list[list[float]]:
        """可选的 /embeddings 兼容接口；不下载模型，不回退到其他服务。"""
        if (
            not model.strip()
            or not texts
            or len(texts) > 32
            or any(len(text) > 6000 for text in texts)
        ):
            raise AiError("向量请求的模型、数量或分片长度无效")
        if self._cancelled:
            raise AiError("已取消")
        request = self._http.build_request(
            "POST",
            self._url("/embeddings"),
            json={"model": model, "input": texts, "encoding_format": "float"},
        )
        try:
            response = self._http.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise self._transport_error(exc) from None
        with self._lock:
            self._response = response
        try:
            content = bytearray()
            for part in response.iter_bytes():
                if self._cancelled:
                    raise AiError("已取消")
                content.extend(part)
                if len(content) > 16_000_000:
                    raise AiError("向量响应过大")
            if response.status_code != 200:
                raise AiError(
                    describe_status(response.status_code, _error_detail(bytes(content), self._key))
                )
            try:
                payload = json.loads(content)
            except ValueError:
                raise AiError("向量响应不是有效 JSON") from None
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or len(data) != len(texts):
                raise AiError("向量响应数量与输入不匹配")
            vectors: dict[int, list[float]] = {}
            dimension = 0
            for item in data:
                if not isinstance(item, dict):
                    raise AiError("向量响应格式无效")
                index, values = item.get("index"), item.get("embedding")
                if (
                    not isinstance(index, int)
                    or isinstance(index, bool)
                    or not 0 <= index < len(texts)
                    or index in vectors
                ):
                    raise AiError("向量响应序号无效")
                if (
                    not isinstance(values, list)
                    or not 1 <= len(values) <= 8192
                    or any(
                        not isinstance(value, (int, float))
                        or isinstance(value, bool)
                        or not math.isfinite(value)
                        for value in values
                    )
                ):
                    raise AiError("向量内容无效")
                vector = [float(value) for value in values]
                if not any(vector) or (dimension and len(vector) != dimension):
                    raise AiError("向量维度不一致或向量为零")
                dimension = len(vector)
                vectors[index] = vector
            return [vectors[i] for i in range(len(texts))]
        except httpx.HTTPError as exc:
            raise self._transport_error(exc) from None
        finally:
            with self._lock:
                self._response = None
            response.close()

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]:
        """流式对话：逐个产出 :class:`StreamEvent`。被 :meth:`cancel` 中断时静默结束。"""
        if not self._base:
            raise AiError("尚未填写 Base URL")
        if not model:
            raise AiError("尚未选择模型")
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        request = self._http.build_request("POST", self._url("/chat/completions"), json=body)
        try:
            response = self._http.send(request, stream=True)
        except httpx.HTTPError as exc:
            if self._cancelled:
                return
            raise self._transport_error(exc) from None
        with self._lock:
            self._response = response
            cancelled = self._cancelled
        try:
            if cancelled:
                return
            if response.status_code != 200:
                raise AiError(
                    describe_status(
                        response.status_code, _error_detail(response.read(), self._key)
                    ),
                    status=response.status_code,
                )
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" not in content_type and "json" in content_type:
                # 少数网关忽略 stream=true，直接返回完整 JSON
                yield from self._parse_whole(response.read())
                return
            yield from self._parse_sse(response)
        except AiError:
            raise
        except httpx.HTTPError as exc:
            if self._cancelled:
                return
            raise self._transport_error(exc, streaming=True) from None
        except Exception:
            if self._cancelled:  # 另一线程关闭了响应流
                return
            raise
        finally:
            with self._lock:
                self._response = None
            with contextlib.suppress(Exception):
                response.close()

    # ------------------------------------------------------------------ 内部
    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    def _parse_sse(self, response: httpx.Response) -> Iterator[StreamEvent]:
        splitter = ThinkSplitter()
        for raw in response.iter_lines():
            if self._cancelled:
                return
            line = raw.strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(chunk, dict):
                continue
            if "error" in chunk:  # 流中途报错（部分厂商以 data 行返回）
                raise AiError("模型返回错误：" + _error_detail(payload.encode("utf-8"), self._key))
            yield from self._chunk_events(chunk, splitter)
        for kind, text in splitter.flush():
            yield StreamEvent(kind, text)  # type: ignore[arg-type]

    def _chunk_events(
        self, chunk: dict[str, Any], splitter: ThinkSplitter
    ) -> Iterator[StreamEvent]:
        choices = chunk.get("choices")
        for choice in choices if isinstance(choices, list) else []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or choice.get("message") or {}
            if not isinstance(delta, dict):
                continue
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                yield StreamEvent("reasoning", reasoning)
            content = delta.get("content")
            if isinstance(content, str) and content:
                for kind, text in splitter.feed(content):
                    yield StreamEvent(kind, text)  # type: ignore[arg-type]
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            yield StreamEvent(
                "usage",
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
            )

    def _parse_whole(self, body: bytes) -> Iterator[StreamEvent]:
        try:
            data = json.loads(body)
        except ValueError:
            raise AiError("响应格式无法识别（既不是流式也不是 JSON）") from None
        if not isinstance(data, dict):
            raise AiError("响应格式无法识别")
        splitter = ThinkSplitter()
        yield from self._chunk_events(data, splitter)
        for kind, text in splitter.flush():
            yield StreamEvent(kind, text)  # type: ignore[arg-type]

    def _transport_error(self, exc: httpx.HTTPError, *, streaming: bool = False) -> AiError:
        host = httpx.URL(self._base).host if self._base.startswith("http") else self._base
        if isinstance(exc, httpx.ConnectTimeout):
            return AiError(f"连接 {host} 超时：请检查网络、代理设置或 Base URL")
        if isinstance(exc, httpx.TimeoutException):
            return AiError("等待模型响应超时，请稍后重试或换用更快的模型")
        if isinstance(exc, httpx.ProxyError):
            return AiError("代理连接失败：请检查系统代理设置")
        if isinstance(exc, (httpx.UnsupportedProtocol, httpx.InvalidURL)):
            return AiError("Base URL 格式不正确（应以 http:// 或 https:// 开头）")
        if isinstance(exc, httpx.ConnectError):
            text = str(exc).lower()
            if "certificate" in text or "ssl" in text:
                return AiError(f"与 {host} 的安全连接失败（证书校验未通过）：请检查代理或网关证书")
            return AiError(f"无法连接到 {host}：请检查网络、代理设置或 Base URL")
        if streaming:
            return AiError("连接中断，回答不完整")
        return AiError(f"网络请求失败：{type(exc).__name__}")
