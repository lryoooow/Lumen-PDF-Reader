import json

import httpx
import pytest

from pdfreader.services.ai.client import AiError, ChatClient
from pdfreader.services.ai.research import (
    ResearchDocument,
    batch_context,
    check_evidence,
    request_bound,
    research_chunks,
    retrieve_chunks,
    run_research,
    workflow_key,
)


def test_chapters_captions_retrieval_and_exact_quote_verification() -> None:
    documents = [
        ResearchDocument(
            1,
            "论文",
            ("Background text", "Attention method.\n\nFigure 2 result"),
            ((0, "背景"), (1, "方法")),
        )
    ]
    chunks = research_chunks(documents)
    assert chunks[-1].kind == "图表说明" and chunks[-1].chapter == "方法"
    assert retrieve_chunks(chunks, "Attention")[0].page == 1
    evidence = check_evidence(
        "结论 [d.1 p.2]「Attention method.」\n错误 [d.1 p.99]「Made up quote」", documents
    )
    assert evidence[0].found and not evidence[1].found
    assert workflow_key(documents, "summary", "m", "base") != workflow_key(
        documents, "compare", "m", "base"
    )


def test_hierarchical_summary_covers_all_batches_and_budget_blocks_before_network() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body["messages"][-1]["content"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "摘要 [d.1 p.1]「original quote」"}}]},
            headers={"content-type": "application/json"},
        )

    client = ChatClient("https://example.test/v1", "k", transport=httpx.MockTransport(handler))
    batches = batch_context([f"page-{i} " + "x" * 5000 for i in range(5)])
    with pytest.raises(AiError, match="预算"):
        run_research(
            client,
            model="m",
            batches=batches,
            task="总结",
            max_requests=1,
            cancelled=lambda: False,
            progress=lambda text: None,
        )
    assert sent == []
    result = run_research(
        client,
        model="m",
        batches=batches,
        task="总结",
        max_requests=30,
        cancelled=lambda: False,
        progress=lambda text: None,
    )
    assert "original quote" in result
    assert all(any(f"page-{i} " in message for message in sent) for i in range(5))
    assert len(sent) <= request_bound(len(batches))
    client.close()
