"""引擎适配层。

封装 PyMuPDF 的访问，是**整个项目唯一 ``import pymupdf`` 的地方**。
上层（core/services/controllers）只依赖这里定义的稳定接口，
将来更换或升级引擎时只需修改本目录。

阶段 1 实现具体封装。
"""
