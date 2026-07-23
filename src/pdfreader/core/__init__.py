"""业务核心层（框架无关的纯 Python 模型）。

定义 :class:`~pdfreader.core.document.Document`、Page、Annotation 等领域对象。
**铁律**：本层绝不 ``import PySide6`` 或 ``import pymupdf``，
通过依赖注入持有引擎接口，从而可脱离 GUI 独立单元测试。

阶段 2 实现具体模型。
"""
