"""检查点存储（checkpointer）：LangGraph 用来保存图执行断点、支撑 interrupt/恢复执行。

BoundedMemorySaver 是 MemorySaver（内存版检查点）的薄包装：
- thread（一次图执行的会话线程，以 thread_id 标识）数量超上限时淘汰最旧的，
  防止长驻服务内存无限增长；
- 所有异常只记日志不抛出，绝不阻断生成主流程。
"""

import logging
from collections import OrderedDict
from typing import Any, Optional

try:  # langgraph >= 0.2.x 同时提供两个名字，按可用性兼容导入
    from langgraph.checkpoint.memory import InMemorySaver as _MemorySaver
except ImportError:  # pragma: no cover - 老版本兜底
    from langgraph.checkpoint.memory import MemorySaver as _MemorySaver

logger = logging.getLogger(__name__)

# 内存中最多保留的 thread 数，超过后淘汰最旧的
MAX_THREADS = 200


class BoundedMemorySaver:
    """包装 MemorySaver，thread 数超上限时淘汰最旧（LRU 顺序）。"""

    def __init__(self, max_threads: int = MAX_THREADS) -> None:
        self._inner = _MemorySaver()
        self._max_threads = max_threads
        # thread_id → None，仅记录存活顺序（OrderedDict 的键序即淘汰顺序）
        self._threads: "OrderedDict[str, None]" = OrderedDict()

    # ── 内部工具 ────────────────────────────────────────────────────────────
    def _touch(self, thread_id: Optional[str]) -> None:
        """记录/刷新 thread 存活顺序，超上限淘汰最旧。异常只记日志。"""
        try:
            if not thread_id:
                return
            self._threads[thread_id] = None
            self._threads.move_to_end(thread_id)
            while len(self._threads) > self._max_threads:
                oldest, _ = self._threads.popitem(last=False)
                try:
                    self._inner.delete_thread(oldest)
                except Exception:  # 清理失败不影响新请求
                    logger.debug("淘汰旧 thread 失败：%s", oldest, exc_info=True)
        except Exception:
            logger.debug("thread 记账失败", exc_info=True)

    # ── checkpointer 协议（LangGraph 调用面）────────────────────────────────
    def get_tuple(self, config: Any) -> Optional[Any]:
        try:
            return self._inner.get_tuple(config)
        except Exception:
            logger.debug("检查点读取失败", exc_info=True)
            return None

    def list(self, config: Any, **kwargs: Any) -> Any:
        try:
            return self._inner.list(config, **kwargs)
        except Exception:
            logger.debug("检查点列举失败", exc_info=True)
            return iter(())

    def put(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        try:
            thread_id = None
            if isinstance(config, dict):
                thread_id = (config.get("configurable") or {}).get("thread_id")
            self._touch(thread_id)
            return self._inner.put(config, checkpoint, metadata, new_versions)
        except Exception:
            logger.debug("检查点写入失败", exc_info=True)
            return None

    def put_writes(self, config: Any, writes: Any, task_id: str) -> Any:
        try:
            return self._inner.put_writes(config, writes, task_id)
        except Exception:
            logger.debug("检查点写入（writes）失败", exc_info=True)
            return None

    def delete_thread(self, thread_id: str) -> None:
        try:
            self._threads.pop(thread_id, None)
            self._inner.delete_thread(thread_id)
        except Exception:
            logger.debug("thread 删除失败：%s", thread_id, exc_info=True)

    # LangGraph 新版本可能用到的属性透传
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
