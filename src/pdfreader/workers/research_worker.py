"""长文研究任务的纯网络阶段；文档文本已在主线程提取完毕。"""

from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from pdfreader.services.ai.client import AiError, ChatClient
from pdfreader.services.ai.research import ResearchChunk, batch_context, run_research
from pdfreader.services.ai.semantic import embedding_requests, semantic_retrieve


class ResearchWorker(QThread):
    result = Signal(str)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(
        self,
        client: ChatClient,
        *,
        model: str,
        batches: list[str],
        task: str,
        max_requests: int,
        chunks: list[ResearchChunk] | None = None,
        embedding_model: str = "",
        query: str = "",
    ) -> None:
        super().__init__()
        self._client = client
        self._model = model
        self._batches = batches
        self._task = task
        self._max_requests = max_requests
        self._stopped = False
        self._chunks = chunks or []
        self._embedding_model = embedding_model
        self._query = query

    def shutdown(self) -> None:
        self._stopped = True
        self._client.cancel()

    def run(self) -> None:
        try:
            budget = self._max_requests
            batches = self._batches
            if self._embedding_model:
                selected = semantic_retrieve(
                    self._client,
                    self._chunks,
                    self._query,
                    model=self._embedding_model,
                    cancelled=lambda: self._stopped,
                    progress=self.progress.emit,
                )
                batches = batch_context([chunk.render() for chunk in selected])
                budget -= embedding_requests(len(self._chunks))
            answer = run_research(
                self._client,
                model=self._model,
                batches=batches,
                task=self._task,
                max_requests=budget,
                cancelled=lambda: self._stopped,
                progress=self.progress.emit,
            )
            if not self._stopped:
                self.result.emit(answer)
        except AiError as exc:
            if not self._stopped:
                self.failed.emit(str(exc))
        except Exception:
            logging.getLogger("pdfreader").exception("长文研究任务失败")
            if not self._stopped:
                self.failed.emit("研究任务失败，请检查服务或导出诊断信息。")
        finally:
            self._client.close()
