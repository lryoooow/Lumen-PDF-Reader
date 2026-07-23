"""LRU 缓存单元测试。"""

from __future__ import annotations

import threading

import pytest

from pdfreader.utils.cache import LRUCache


class TestBasicOperations:
    def test_put_and_get(self) -> None:
        cache = LRUCache(capacity=3)
        cache.put("a", 1)
        assert cache.get("a") == 1

    def test_get_missing_returns_default(self) -> None:
        cache = LRUCache(capacity=3)
        assert cache.get("x", default=-1) == -1

    def test_get_missing_default_none(self) -> None:
        cache = LRUCache(capacity=3)
        assert cache.get("x") is None

    def test_contains(self) -> None:
        cache = LRUCache(capacity=3)
        cache.put("a", 1)
        assert cache.contains("a") is True
        assert cache.contains("b") is False

    def test_in_operator(self) -> None:
        cache = LRUCache(capacity=3)
        cache.put("a", 1)
        assert "a" in cache
        assert "b" not in cache

    def test_len(self) -> None:
        cache = LRUCache(capacity=3)
        cache.put("a", 1)
        cache.put("b", 2)
        assert len(cache) == 2


class TestEviction:
    def test_evict_lru_when_full(self) -> None:
        cache = LRUCache(capacity=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)  # "a" 应被淘汰
        assert "a" not in cache
        assert "b" in cache
        assert "c" in cache
        assert cache.evictions == 1

    def test_get_updates_recency(self) -> None:
        cache = LRUCache(capacity=2)
        cache.put("a", 1)
        cache.put("b", 2)
        _ = cache.get("a")  # "a" 变为最近使用
        cache.put("c", 3)  # 应淘汰 "b" 而非 "a"
        assert "a" in cache
        assert "b" not in cache

    def test_put_existing_updates_recency(self) -> None:
        cache = LRUCache(capacity=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("a", 10)  # "a" 更新并提升
        cache.put("c", 3)  # 淘汰 "b"
        assert cache.get("a") == 10
        assert "b" not in cache

    def test_capacity_one(self) -> None:
        cache = LRUCache(capacity=1)
        cache.put("a", 1)
        cache.put("b", 2)
        assert "a" not in cache
        assert cache.get("b") == 2


class TestGetOrCompute:
    def test_compute_on_miss(self) -> None:
        cache = LRUCache(capacity=3)
        calls: list[str] = []
        result = cache.get_or_compute("x", lambda: (calls.append("x"), 42)[1])
        assert result == 42
        assert calls == ["x"]

    def test_no_compute_on_hit(self) -> None:
        cache = LRUCache(capacity=3)
        cache.put("x", 42)
        calls: list[str] = []
        result = cache.get_or_compute("x", lambda: (calls.append("x"), 99)[1])
        assert result == 42
        assert calls == []


class TestStats:
    def test_hits_and_misses(self) -> None:
        cache = LRUCache(capacity=3)
        cache.put("a", 1)
        _ = cache.get("a")  # hit
        _ = cache.get("a")  # hit
        _ = cache.get("b")  # miss
        assert cache.hits == 2
        assert cache.misses == 1

    def test_reset_stats(self) -> None:
        cache = LRUCache(capacity=3)
        cache.put("a", 1)
        _ = cache.get("a")
        cache.reset_stats()
        assert cache.hits == 0
        assert cache.misses == 0


class TestEdgeCases:
    def test_capacity_zero_means_no_cache(self) -> None:
        cache = LRUCache(capacity=0)
        cache.put("a", 1)
        assert cache.get("a") is None
        assert cache.size == 0

    def test_negative_capacity_raises(self) -> None:
        with pytest.raises(ValueError):
            LRUCache(capacity=-1)

    def test_remove_existing(self) -> None:
        cache = LRUCache(capacity=3)
        cache.put("a", 1)
        assert cache.remove("a") is True
        assert "a" not in cache

    def test_remove_nonexistent(self) -> None:
        cache = LRUCache(capacity=3)
        assert cache.remove("x") is False

    def test_clear(self) -> None:
        cache = LRUCache(capacity=3)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.clear()
        assert cache.size == 0

    def test_keys(self) -> None:
        cache = LRUCache(capacity=3)
        cache.put("a", 1)
        cache.put("b", 2)
        assert set(cache.keys()) == {"a", "b"}

    def test_cache_none_value(self) -> None:
        """缓存值为 None 时应能区分'缓存了 None'和'未命中'。"""
        cache = LRUCache(capacity=3)
        cache.put("a", None)
        # get 默认 None，但 contains 能区分
        assert cache.contains("a") is True
        assert cache.get("a") is None


class TestThreadSafety:
    def test_concurrent_access(self) -> None:
        """多线程并发读写不崩溃。"""
        cache = LRUCache(capacity=100)
        errors: list[Exception] = []

        def writer() -> None:
            try:
                for i in range(200):
                    cache.put(i, i * 2)
            except Exception as e:
                errors.append(e)

        def reader() -> None:
            try:
                for i in range(200):
                    cache.get(i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(4)]
        threads += [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
