"""会话记忆：多轮对话历史 + 最近一次产物快照，供 API 层与图内读写。

- 会话历史：按 sessionId 存 [{role, content}]，超 20 条裁掉最旧的（多轮意图识别的输入）；
- 产物快照：最近一次生成的 topic / catalogList / pageList / audioList / courseId，
  是 /chat/retry（手动重试失败页）与 revise_page（定向改页）的数据源。
纯内存实现：服务重启即清空，属会话级缓存，不承担持久化职责（持久化在 storage/course_db）。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 每个会话最多保留的历史条数
MAX_HISTORY = 20

# sessionId → [{role: "user"|"assistant", content: str}]
_HISTORY: dict[str, list[dict]] = {}
# sessionId → 最近一次产物快照
_SNAPSHOT: dict[str, dict] = {}


def _key(session_id: Optional[str]) -> str:
    """把 sessionId 规整成字典键；空 id 归并到默认会话，避免到处判空。"""
    return (session_id or "").strip() or "_default"


def get_history(session_id: Optional[str]) -> list[dict]:
    """取会话历史（副本），供意图识别与续写使用。"""
    return [dict(item) for item in _HISTORY.get(_key(session_id), [])]


def append_message(session_id: Optional[str], role: str, content: str) -> None:
    """追加一条会话消息，超过 MAX_HISTORY 条裁掉最旧的。"""
    try:
        key = _key(session_id)
        history = _HISTORY.setdefault(key, [])
        history.append({"role": role, "content": str(content or "")})
        if len(history) > MAX_HISTORY:
            del history[: len(history) - MAX_HISTORY]
    except Exception:
        logger.debug("会话历史写入失败", exc_info=True)


def clear_history(session_id: Optional[str]) -> None:
    """清空指定会话历史。"""
    _HISTORY.pop(_key(session_id), None)


def save_snapshot(session_id: Optional[str], snapshot: dict) -> None:
    """保存最近一次产物快照（覆盖写）。异常只记日志。"""
    try:
        _SNAPSHOT[_key(session_id)] = dict(snapshot or {})
    except Exception:
        logger.debug("快照写入失败", exc_info=True)


def get_snapshot(session_id: Optional[str]) -> Optional[dict]:
    """取最近一次产物快照（副本）；没有则返回 None。"""
    snapshot = _SNAPSHOT.get(_key(session_id))
    return dict(snapshot) if snapshot else None


def update_snapshot(session_id: Optional[str], **fields) -> None:
    """增量更新快照字段（例如重试后刷新 pageList）。"""
    try:
        snapshot = _SNAPSHOT.setdefault(_key(session_id), {})
        snapshot.update(fields)
    except Exception:
        logger.debug("快照更新失败", exc_info=True)
