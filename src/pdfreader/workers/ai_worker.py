"""AI 请求后台线程。

主线程先取好文本（PyMuPDF 非线程安全）、拼好消息，这里只做网络请求：
流式对话逐块发 ``delta`` / ``reasoning``，结束发 ``finished``；也可只列模型（设置对话框的「获取模型」）。
``shutdown()`` 会关闭响应流，阻塞中的读取立即返回，线程随即结束。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal

from pdfreader.services.ai.client import AiError, ChatClient

__all__ = ["AiWorker"]


class AiWorker(QThread):
    """一次 AI 请求。

    Signals:
        delta(str): 正文增量
        reasoning(str): 思考内容增量
        finished_text(str): 完成，携带完整正文
        modelsListed(list): 模型列表（``list_models`` 任务）
        failed(str): 失败（中文原因）
        cancelled(): 被取消
    """

    delta = Signal(str)
    reasoning = Signal(str)
    finished_text = Signal(str)
    modelsListed = Signal(list)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._client: ChatClient | None = None
        self._task = "chat"
        self._messages: list[dict[str, Any]] = []
        self._model = ""
        self._temperature: float | None = None
        self._max_tokens: int | None = None
        self._stopped = False

    def setup_chat(
        self,
        client: ChatClient,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self._client = client
        self._task = "chat"
        self._messages = messages
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    def setup_models(self, client: ChatClient) -> None:
        self._client = client
        self._task = "models"

    def shutdown(self) -> None:
        """取消请求（可在主线程调用）。"""
        self._stopped = True
        if self._client is not None:
            self._client.cancel()

    @property
    def stopped(self) -> bool:
        return self._stopped

    def run(self) -> None:
        client = self._client
        if client is None:
            self.failed.emit("未配置 AI 服务")
            return
        try:
            if self._task == "models":
                models = client.list_models()
                if not self._stopped:
                    self.modelsListed.emit(models)
                return
            parts: list[str] = []
            for event in client.stream_chat(
                self._messages,
                model=self._model,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            ):
                if self._stopped:
                    break
                if event.kind == "content":
                    parts.append(event.text)
                    self.delta.emit(event.text)
                elif event.kind == "reasoning":
                    self.reasoning.emit(event.text)
            if self._stopped:
                self.cancelled.emit()
            else:
                self.finished_text.emit("".join(parts))
        except AiError as exc:
            if self._stopped:
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))
        except Exception as exc:  # 防御：任何意外都不能让线程静默死掉
            if self._stopped:
                self.cancelled.emit()
            else:
                self.failed.emit(f"AI 请求失败：{type(exc).__name__}")
        finally:
            client.close()
