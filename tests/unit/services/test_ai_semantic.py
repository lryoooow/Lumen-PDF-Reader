import json

import httpx
import pytest

from pdfreader.services.ai.client import AiError, ChatClient
from pdfreader.services.ai.research import ResearchChunk
from pdfreader.services.ai.semantic import semantic_retrieve


def test_embeddings_validate_order_and_semantic_ranking() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        values = [[0.0, 1.0]] if len(body["input"]) == 1 else [[1.0, 0.0], [0.0, 1.0]]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": vector}
                    for i, vector in reversed(list(enumerate(values)))
                ]
            },
        )

    client = ChatClient(
        "https://example.test/v1", "test-key", transport=httpx.MockTransport(handler)
    )
    chunks = [
        ResearchChunk(1, 0, "", "正文", "different words"),
        ResearchChunk(1, 1, "", "正文", "semantic match"),
    ]
    selected = semantic_retrieve(
        client,
        chunks,
        "query",
        model="embedding-model",
        cancelled=lambda: False,
        progress=lambda text: None,
    )
    assert selected[0].page == 1
    assert requests[0]["model"] == "embedding-model"
    assert requests[1]["input"] == ["query"]
    client.close()


@pytest.mark.parametrize(
    "data",
    [
        [],
        [{"index": 0, "embedding": [0, 0]}],
        [{"index": 0, "embedding": [True, 1]}],
        [{"index": 0, "embedding": [float("nan")]}],
    ],
)
def test_bad_embedding_responses_fail_visibly(data: list) -> None:
    client = ChatClient(
        "https://example.test/v1",
        "k",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                content=json.dumps({"data": data}).encode(),
                headers={"content-type": "application/json"},
            )
        ),
    )
    try:
        with pytest.raises(AiError):
            client.embeddings(["text"], model="m")
    finally:
        client.close()
