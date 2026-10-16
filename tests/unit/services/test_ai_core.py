"""AI 服务的纯逻辑：预设 / 规范化 / 设置读写、凭据、上下文检索、引用解析、提示词。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdfreader.services.ai import context, prompts
from pdfreader.services.ai.keystore import SERVICE, KeyStore, KeyStoreError, default_keystore
from pdfreader.services.ai.provider import (
    PRESETS,
    AiSettings,
    AiSettingsStore,
    ProviderConfig,
    default_ai_settings_path,
    is_local_host,
    new_provider,
    normalize_base_url,
    preset_by_id,
)


# --------------------------------------------------------------------------- provider
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1"),
        ("https://api.deepseek.com/v1/", "https://api.deepseek.com/v1"),
        (" https://x.com/v1/chat/completions ", "https://x.com/v1"),
        ("https://x.com/v1/chat/completions/", "https://x.com/v1"),
        ("https://x.com/v1/models", "https://x.com/v1"),
        ("https://open.bigmodel.cn/api/paas/v4", "https://open.bigmodel.cn/api/paas/v4"),
        # 不自动补 /v1
        ("https://api.deepseek.com", "https://api.deepseek.com"),
        ("", ""),
    ],
)
def test_normalize_base_url(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


def test_presets_are_normalized_and_unique() -> None:
    ids = [p.id for p in PRESETS]
    assert len(ids) == len(set(ids))
    assert ids[-1] == "custom"
    for preset in PRESETS:
        assert normalize_base_url(preset.base_url) == preset.base_url
    assert preset_by_id("nope").id == "custom"
    assert preset_by_id("zhipu").base_url.endswith("/v4")


def test_local_host_detection() -> None:
    assert is_local_host("http://localhost:11434/v1")
    assert is_local_host("http://127.0.0.1:8000/v1")
    assert is_local_host("http://foo.localhost/v1")
    assert not is_local_host("https://api.openai.com/v1")


def test_new_provider_from_preset() -> None:
    p = new_provider("deepseek")
    assert p.name == "DeepSeek" and p.model == "deepseek-chat" and p.needs_key
    assert p.host == "api.deepseek.com"
    assert p.is_usable()
    ollama = new_provider("ollama")
    assert ollama.is_local and not ollama.needs_key and not ollama.is_usable()
    custom = new_provider("custom")
    assert custom.name == "自定义服务" and custom.base_url == ""
    assert new_provider("deepseek").id != p.id


def test_settings_roundtrip(tmp_path: Path) -> None:
    store = AiSettingsStore(tmp_path / "ai.json")
    p = new_provider("qwen")
    p.vision, p.temperature = True, 0.2
    s = AiSettings(enabled=True, active_id=p.id, providers=[p], consented=[p.id])
    store.save(s)
    raw = json.loads((tmp_path / "ai.json").read_text(encoding="utf-8"))
    assert "key" not in json.dumps(raw).lower().replace("needs_key", "")
    loaded = store.load()
    assert loaded == s
    assert loaded.active() == p
    assert loaded.provider(p.id) == p
    assert loaded.provider("x") is None


def test_settings_load_tolerates_bad_data(tmp_path: Path) -> None:
    path = tmp_path / "ai.json"
    store = AiSettingsStore(path)
    assert store.load() == AiSettings()  # 文件不存在
    path.write_text("{bad", encoding="utf-8")
    assert store.load() == AiSettings()
    path.write_text("[]", encoding="utf-8")
    assert store.load() == AiSettings()
    path.write_text(
        json.dumps(
            {
                "enabled": "yes",
                "providers": [
                    {"id": "a", "name": "A", "base_url": "https://a/v1/", "models": ["m", 3]},
                    {"id": "", "name": "bad"},
                    "junk",
                    {"id": "b", "name": "B", "temperature": True, "vision": "1"},
                ],
                "consented": ["a", 1],
                "target_language": None,
            }
        ),
        encoding="utf-8",
    )
    s = store.load()
    assert s.enabled is False
    assert [p.id for p in s.providers] == ["a", "b"]
    assert s.providers[0].base_url == "https://a/v1"
    assert s.providers[0].models == ["m"]
    assert s.providers[1].temperature is None and s.providers[1].vision is False
    assert s.consented == ["a"]
    assert s.target_language == "简体中文"
    assert s.active() == s.providers[0]  # active_id 为空时取第一个
    assert AiSettings().active() is None


def test_default_path_follows_settings_dir() -> None:
    from pdfreader.config import settings as settings_module

    assert default_ai_settings_path().parent == settings_module.default_settings_path().parent
    assert AiSettingsStore().path.name == "ai.json"


def test_provider_host_fallback() -> None:
    assert ProviderConfig("i", "n", base_url="weird").host == "weird"


# --------------------------------------------------------------------------- keystore
def test_keystore_memory_backend(memory_keyring) -> None:  # type: ignore[no-untyped-def]
    ks = default_keystore()
    assert ks.get("p1") == ""
    ks.set("p1", "sk-1")
    assert memory_keyring.data[(SERVICE, "p1")] == "sk-1"
    assert ks.get("p1") == "sk-1"
    ks.set("p1", "")  # 空串 = 删除
    assert ks.get("p1") == ""
    ks.delete("missing")  # 不存在也不抛


def test_keystore_wraps_backend_errors() -> None:
    class Broken:
        def get_password(self, service: str, username: str) -> str | None:
            raise RuntimeError("locked")

        def set_password(self, service: str, username: str, password: str) -> None:
            raise RuntimeError("locked")

        def delete_password(self, service: str, username: str) -> None:
            raise RuntimeError("locked")

    ks = KeyStore(Broken())
    with pytest.raises(KeyStoreError, match="读取"):
        ks.get("p")
    with pytest.raises(KeyStoreError, match="保存"):
        ks.set("p", "k")
    ks.delete("p")  # 删除失败静默

    class Passthrough(Broken):
        def get_password(self, service: str, username: str) -> str | None:
            raise KeyStoreError("unavailable")

        def set_password(self, service: str, username: str, password: str) -> None:
            raise KeyStoreError("unavailable")

    with pytest.raises(KeyStoreError, match="unavailable"):
        KeyStore(Passthrough()).get("p")
    with pytest.raises(KeyStoreError, match="unavailable"):
        KeyStore(Passthrough()).set("p", "k")


# --------------------------------------------------------------------------- context
def test_tokenize_mixed() -> None:
    tokens = context.tokenize("The Transformer 模型使用注意力 a")
    assert "transformer" in tokens
    assert "the" not in tokens  # 停用词
    assert "模型" in tokens and "注意" in tokens and "意力" in tokens
    assert context.tokenize("中") == ["中"]


def test_chunk_pages_never_cross_pages() -> None:
    pages = ["A" * 2000, "", "short"]
    chunks = context.chunk_pages(pages, size=900, overlap=100)
    assert {c.page for c in chunks} == {0, 2}
    assert all(len(c.text) <= 900 for c in chunks)
    assert sum(1 for c in chunks if c.page == 0) == 3
    assert chunks[-1] == context.Chunk(2, "short")


def test_bm25_ranks_relevant_chunk_first() -> None:
    chunks = [
        context.Chunk(0, "We study image classification with convolutional networks."),
        context.Chunk(1, "The attention mechanism weighs tokens; attention is all you need."),
        context.Chunk(2, "本文提出一种新的注意力机制用于机器翻译。"),
    ]
    index = context.Bm25Index(chunks)
    assert index.search("attention mechanism")[0].page == 1
    assert index.search("注意力机制")[0].page == 2
    assert index.search("quantum chromodynamics") == []
    assert context.Bm25Index([]).search("x") == []


def test_overview_context_short_and_long() -> None:
    text, truncated = context.overview_context(["第一页内容", "第二页内容"])
    assert not truncated
    assert text == "[p.1] 第一页内容\n\n[p.2] 第二页内容"
    pages = [f"page{i} " + "x" * 3000 for i in range(30)]
    text, truncated = context.overview_context(pages, budget=9000)
    assert truncated
    assert "[p.1]" in text and "[p.30]" in text and "省略" in text
    assert len(text) < 9000 + 600


def test_question_context_uses_retrieval_for_long_docs() -> None:
    pages = ["Title and abstract of the paper."] + [
        ("filler text " * 200)
        if i != 17
        else ("The learning rate was 3e-4 with cosine decay. " * 5)
        for i in range(1, 40)
    ]
    text, retrieved = context.question_context(pages, "what learning rate decay", budget=4000)
    assert retrieved
    assert text.startswith("[p.1] Title")
    assert "[p.18]" in text
    short, retrieved = context.question_context(["a", "b"], "q")
    assert not retrieved and short == "[p.1] a\n\n[p.2] b"


def test_cited_pages_and_links() -> None:
    answer = "方法见 [p.3]，结果 [p. 5, p.7] 与 [P12]，区间 [p.4–6]；参考文献 [1] 不算；[p.99] 越界"
    assert context.cited_pages(answer) == [3, 5, 7, 12, 4, 99]
    linked = context.link_citations(answer, page_count=20)
    assert r"[\[p.3\]](cite:3)" in linked
    assert r"[\[p.5\]](cite:5) [\[p.7\]](cite:7)" in linked
    assert r"[\[p.12\]](cite:12)" in linked
    assert r"[\[p.4\]](cite:4)" in linked
    assert "[1]" in linked and "(cite:1)" not in linked
    assert "[p.99]" in linked and "cite:99" not in linked
    assert context.link_citations("[p.3,4]", 10) == r"[\[p.3\]](cite:3) [\[p.4\]](cite:4)"


# --------------------------------------------------------------------------- prompts
def test_prompt_builders() -> None:
    t = prompts.translate_messages("Hello", "简体中文")
    assert t[0]["role"] == "system" and "简体中文" in t[0]["content"]
    assert t[-1] == {"role": "user", "content": "Hello"}

    e = prompts.explain_messages("E=mc^2", "周围文字", "English")
    assert "English" in e[0]["content"] and "周围文字" in e[-1]["content"]
    assert "上下文" not in prompts.explain_messages("x", "  ", "中文")[-1]["content"]

    s = prompts.summary_messages("[p.1] abc", "中文", truncated=True)
    assert "[p.N]" in s[-1]["content"] and "开头与结尾" in s[-1]["content"]

    history = [
        {"role": "user", "content": f"q{i}"}
        if i % 2 == 0
        else {"role": "assistant", "content": f"a{i}"}
        for i in range(10)
    ] + [{"role": "tool", "content": "x"}, {"role": "user", "content": ["not str"]}]
    a = prompts.ask_messages("为什么？", "[p.2] ctx", "中文", history)
    assert a[0]["role"] == "system"
    assert len(a) == 1 + prompts.MAX_HISTORY_MESSAGES + 1 - 2  # 末尾两条无效历史被丢弃
    assert "问题：为什么？" in a[-1]["content"]
    assert "用户引用的原文" not in a[-1]["content"]
    quoted = prompts.ask_messages("为什么？", "ctx", "中文", quote="  原文一段  ")
    assert "用户引用的原文（问题针对这段）：\n原文一段\n\n问题：为什么？" in quoted[-1]["content"]

    q = prompts.suggest_messages("ctx", "中文")
    assert "5 个" in q[-1]["content"]

    img = prompts.explain_image_messages("QUJD", "附近文字", "中文")
    parts = img[-1]["content"]
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,QUJD"
    assert "附近文字" in parts[0]["text"]


def test_parse_questions() -> None:
    text = "1. 作者的方法是什么？\n- 有哪些局限？\n\n2）实验数据集？\n短\n1. 作者的方法是什么？\n• 第四个问题？\n五？？？\n第六个问题会被截断？"
    qs = prompts.parse_questions(text)
    assert qs == ["作者的方法是什么？", "有哪些局限？", "实验数据集？", "第四个问题？", "五？？？"]


def test_preset_actions() -> None:
    keys = [a.key for a in prompts.PRESET_ACTIONS]
    assert len(keys) == len(set(keys))
    assert prompts.preset_by_key("method") is not None
    assert prompts.preset_by_key("nope") is None
