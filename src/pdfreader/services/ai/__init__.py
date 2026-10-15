"""AI 助读服务（纯 Python，不依赖 PySide6 / PyMuPDF）。

只对接 **OpenAI Chat Completions 兼容接口**：各家厂商的请求体、Bearer 鉴权与 SSE 流式格式一致，
切换厂商只需改 Base URL、API Key 与模型名。

* :mod:`.provider` —— 厂商预设、Provider 配置、Base URL 规范化、AI 设置的读写
* :mod:`.client` —— httpx 流式对话 / 列模型，错误翻译成中文原因
* :mod:`.keystore` —— API Key 存系统凭据管理器（keyring），从不写入设置文件
* :mod:`.prompts` —— 预设动作的提示词
* :mod:`.context` —— 文档分块、BM25 检索、``[p.N]`` 页码引用的生成与解析

文本提取在主线程完成（PyMuPDF 非线程安全），这里只接收字符串。
"""

from __future__ import annotations
