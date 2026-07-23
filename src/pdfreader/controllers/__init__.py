"""控制器层：协调 GUI 与业务逻辑。

接收用户动作，调用引擎/服务完成工作，刷新视图。
不含具体渲染/处理实现，避免 GUI 层与引擎层直接耦合。
"""

from __future__ import annotations

from pdfreader.controllers.annotation_controller import (
    AnnotationController,
    AnnotationMode,
)
from pdfreader.controllers.document_controller import DocumentController, OpenResult

__all__ = ["AnnotationController", "AnnotationMode", "DocumentController", "OpenResult"]
