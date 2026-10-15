"""厂商预设、Provider 配置与 AI 设置的持久化。

* 预设只是「填好默认值的模板」，用户可改任意字段；厂商地址以官方文档为准。
* API Key **不在这里**——存系统凭据管理器（见 :mod:`.keystore`），``ai.json`` 里只有地址与模型。
* Base URL 保存时规范化：去掉末尾 ``/`` 与误粘贴的 ``/chat/completions``；**不**自动补 ``/v1``
  （智谱 ``/v4``、方舟 ``/v3``、Gemini ``/v1beta/openai`` 都会被补错）。
"""

from __future__ import annotations

import contextlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from pdfreader.config import settings as _settings

__all__ = [
    "PRESETS",
    "AiSettings",
    "AiSettingsStore",
    "ProviderConfig",
    "ProviderPreset",
    "default_ai_settings_path",
    "is_local_host",
    "new_provider",
    "normalize_base_url",
    "preset_by_id",
]


@dataclass(frozen=True)
class ProviderPreset:
    """厂商预设。"""

    id: str
    name: str
    base_url: str
    models: tuple[str, ...] = ()  # 常用模型（仅作下拉建议，可「获取模型列表」刷新）
    note: str = ""
    needs_key: bool = True


PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        "deepseek",
        "DeepSeek",
        "https://api.deepseek.com/v1",
        ("deepseek-chat", "deepseek-reasoner"),
    ),
    ProviderPreset(
        "qwen",
        "通义千问（阿里云百炼）",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ("qwen-plus", "qwen-turbo", "qwen-max", "qwen-vl-plus"),
        "国际站为 dashscope-intl.aliyuncs.com",
    ),
    ProviderPreset(
        "kimi",
        "Kimi（月之暗面）",
        "https://api.moonshot.cn/v1",
        ("moonshot-v1-32k", "moonshot-v1-128k"),
        "国际站为 api.moonshot.ai",
    ),
    ProviderPreset(
        "zhipu",
        "智谱 GLM",
        "https://open.bigmodel.cn/api/paas/v4",
        ("glm-4-flash", "glm-4-plus", "glm-4v-flash"),
        "路径是 /api/paas/v4，不是 /v1",
    ),
    ProviderPreset(
        "doubao",
        "豆包（火山方舟）",
        "https://ark.cn-beijing.volces.com/api/v3",
        (),
        "模型名一般填推理接入点 ID（ep-…）",
    ),
    ProviderPreset(
        "siliconflow",
        "硅基流动",
        "https://api.siliconflow.cn/v1",
        ("deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct"),
        ".cn 与 .com 站的 Key 不通用",
    ),
    ProviderPreset(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        ("gpt-4o-mini", "gpt-4o"),
    ),
    ProviderPreset(
        "gemini",
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        ("gemini-2.0-flash",),
        "Gemini 官方 OpenAI 兼容层",
    ),
    ProviderPreset(
        "openrouter",
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        (),
        "聚合多家模型，模型名形如 vendor/model",
    ),
    ProviderPreset(
        "anthropic",
        "Anthropic Claude",
        "https://api.anthropic.com/v1",
        (),
        "官方定位为测试 / 对比用的兼容层",
    ),
    ProviderPreset(
        "ollama",
        "Ollama（本机）",
        "http://localhost:11434/v1",
        (),
        "数据不出本机；Key 可留空",
        needs_key=False,
    ),
    ProviderPreset("custom", "自定义（OpenAI 兼容）", "", (), "vLLM、LM Studio、公司网关等"),
)


def preset_by_id(preset_id: str) -> ProviderPreset:
    """按 id 取预设，未知 id 回退「自定义」。"""
    for preset in PRESETS:
        if preset.id == preset_id:
            return preset
    return PRESETS[-1]


def normalize_base_url(url: str) -> str:
    """规范化 Base URL：去空白、末尾 ``/`` 与误粘贴的 ``/chat/completions``。"""
    text = url.strip()
    while text.endswith("/"):
        text = text[:-1]
    for suffix in ("/chat/completions", "/completions", "/models"):
        if text.lower().endswith(suffix):
            text = text[: -len(suffix)]
            break
    while text.endswith("/"):
        text = text[:-1]
    return text


def is_local_host(base_url: str) -> bool:
    """地址是否指向本机（本机模型数据不出电脑，界面上显示「本机」）。"""
    host = (urlparse(base_url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")


@dataclass
class ProviderConfig:
    """一个 OpenAI 兼容服务（不含 API Key）。"""

    id: str
    name: str
    preset: str = "custom"
    base_url: str = ""
    model: str = ""
    models: list[str] = field(default_factory=list)  # 已知模型（下拉候选）
    vision: bool = False  # 当前模型能看图（无法从 /models 可靠判断，由用户勾选）
    temperature: float | None = None  # None = 不发送（推理模型可能拒绝采样参数）

    @property
    def host(self) -> str:
        return urlparse(self.base_url).hostname or self.base_url

    @property
    def is_local(self) -> bool:
        return is_local_host(self.base_url)

    @property
    def needs_key(self) -> bool:
        return preset_by_id(self.preset).needs_key and not self.is_local

    def is_usable(self) -> bool:
        return bool(self.base_url and self.model)


def new_provider(preset_id: str) -> ProviderConfig:
    """按预设新建一个 Provider。"""
    preset = preset_by_id(preset_id)
    return ProviderConfig(
        id=uuid.uuid4().hex[:12],
        name=preset.name if preset.id != "custom" else "自定义服务",
        preset=preset.id,
        base_url=preset.base_url,
        model=preset.models[0] if preset.models else "",
        models=list(preset.models),
    )


@dataclass
class AiSettings:
    """AI 助读设置（``~/.pdfreader/ai.json``）。"""

    enabled: bool = False  # 默认关闭：AI 是可选的联网功能
    active_id: str = ""
    providers: list[ProviderConfig] = field(default_factory=list)
    target_language: str = "简体中文"  # 翻译 / 回答使用的语言
    consented: list[str] = field(default_factory=list)  # 已同意外发的 provider id

    def active(self) -> ProviderConfig | None:
        for provider in self.providers:
            if provider.id == self.active_id:
                return provider
        return self.providers[0] if self.providers else None

    def provider(self, provider_id: str) -> ProviderConfig | None:
        return next((p for p in self.providers if p.id == provider_id), None)


def default_ai_settings_path() -> Path:
    """与用户设置同目录的 ``ai.json``。"""
    return _settings.default_settings_path().with_name("ai.json")


def _provider_from_dict(raw: object) -> ProviderConfig | None:
    if not isinstance(raw, dict):
        return None
    pid, name = raw.get("id"), raw.get("name")
    if not isinstance(pid, str) or not pid or not isinstance(name, str):
        return None
    temperature = raw.get("temperature")
    models = raw.get("models")
    return ProviderConfig(
        id=pid,
        name=name,
        preset=str(raw.get("preset") or "custom"),
        base_url=normalize_base_url(str(raw.get("base_url") or "")),
        model=str(raw.get("model") or ""),
        models=[m for m in models if isinstance(m, str)] if isinstance(models, list) else [],
        vision=raw.get("vision") is True,
        temperature=(
            float(temperature)
            if isinstance(temperature, (int, float)) and not isinstance(temperature, bool)
            else None
        ),
    )


class AiSettingsStore:
    """AI 设置读写：逐字段校验，坏字段回退默认；先写临时文件再原子替换。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_ai_settings_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> AiSettings:
        defaults = AiSettings()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return defaults
        if not isinstance(data, dict):
            return defaults
        raw_providers = data.get("providers")
        providers = [
            p
            for p in (
                _provider_from_dict(r)
                for r in (raw_providers if isinstance(raw_providers, list) else [])
            )
            if p is not None
        ]
        consented = data.get("consented")
        return AiSettings(
            enabled=data.get("enabled") is True,
            active_id=str(data.get("active_id") or ""),
            providers=providers,
            target_language=str(data.get("target_language") or defaults.target_language),
            consented=(
                [c for c in consented if isinstance(c, str)] if isinstance(consented, list) else []
            ),
        )

    def save(self, settings: AiSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            tmp.replace(self._path)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
