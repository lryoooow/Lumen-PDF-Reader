"""LRU 缓存。

用于缓存已渲染的页面 QPixmap，避免重复渲染。
纯 Python 实现，不依赖 Qt，可独立单元测试。

线程安全说明：本实现使用可重入锁，可在多线程下安全使用。
但缓存中存放的对象（如 QPixmap）本身可能有线程亲和性要求，
跨线程访问需调用方自行确保（见 workers/ 的设计）。
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from threading import RLock

__all__ = ["LRUCache"]


class LRUCache:
    """线程安全的 LRU（最近最少使用）缓存。

    Args:
        capacity: 最大容量。超出时按 LRU 策略淘汰最近最少使用的项。
            capacity <= 0 表示无缓存（所有操作返回缺失）。
    """

    def __init__(self, capacity: int = 50) -> None:
        if capacity < 0:
            raise ValueError(f"容量必须非负，得到：{capacity}")
        self._capacity = capacity
        self._data: OrderedDict[object, object] = OrderedDict()
        self._lock = RLock()
        # 统计信息（便于调优/测试）
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._data)

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def evictions(self) -> int:
        return self._evictions

    def get(self, key: object, default: object = None) -> object:
        """获取缓存项。命中则提升为最近使用；未命中返回 default。"""
        with self._lock:
            if self._capacity == 0:
                self._misses += 1
                return default
            if key in self._data:
                # 提升为最近使用（移到末尾）
                self._data.move_to_end(key)
                self._hits += 1
                return self._data[key]
            self._misses += 1
            return default

    def put(self, key: object, value: object) -> None:
        """存入缓存项。若已存在则更新并提升；超出容量则淘汰 LRU 项。"""
        with self._lock:
            if self._capacity == 0:
                return
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = value
                return
            self._data[key] = value
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)  # 淘汰最旧（头部）
                self._evictions += 1

    def get_or_compute(self, key: object, factory: Callable[[], object]) -> object:
        """获取缓存项，未命中则用 factory 计算并缓存。"""
        with self._lock:
            value = self.get(key, _MISSING)
            if value is not _MISSING:
                return value
            value = factory()
            self.put(key, value)
            return value

    def contains(self, key: object) -> bool:
        with self._lock:
            return key in self._data

    def remove(self, key: object) -> bool:
        """移除指定项，返回是否成功移除。"""
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def reset_stats(self) -> None:
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._evictions = 0

    def keys(self) -> list[object]:
        with self._lock:
            return list(self._data.keys())

    def items(self) -> list[tuple[object, object]]:
        """按「最旧 → 最新」返回全部条目（不改变使用顺序）。"""
        with self._lock:
            return list(self._data.items())

    def pop_oldest(self) -> tuple[object, object] | None:
        """淘汰并返回最久未使用的条目；缓存为空时返回 None。"""
        with self._lock:
            if not self._data:
                return None
            self._evictions += 1
            return self._data.popitem(last=False)

    def __len__(self) -> int:
        return self.size

    def __contains__(self, key: object) -> bool:
        return self.contains(key)


class _Missing:
    """内部哨兵，区分"未命中"与"缓存值为 None"。"""

    __slots__ = ()

    _instance: _Missing | None = None

    def __new__(cls) -> _Missing:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


_MISSING = _Missing()
