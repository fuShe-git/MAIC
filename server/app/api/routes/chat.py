"""对话接口（v4）：4 个接口共用一套 SSE 进度事件。

- POST /chat        一次性同步调用（既有行为不变）；
- POST /chat/stream SSE 进度流（新增 confirm 终态、partial/failedPages 终态字段）；
- POST /chat/confirm 人工确认大纲后恢复执行（SSE，interrupt 恢复）；
- POST /chat/retry  手动重试失败页（图外直调 agent + 重组装，不受 dispatch 上限约束）。

进度事件结构：{step, status, current, total, message, data}，data 是各阶段的领域字段
（topic / topic_path / courseHtml / videoUrl / partial / failedPages / courseId 等）。
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent import sessions
from app.agent.agents.assemble_agent import _append_quiz_section, _quiz_section
from app.agent.graph import is_interrupted, resume_classroom, run_classroom
from app.agent.nodes import (
    OUTPUT_DIR,
    _build_page,
    _clean_script,
    _emit,
    _module_outline,
    set_progress_emitter,
)
from app.storage import course_db
from app.tools.audioCreate import tts_save
from app.tools.courseAssemble import build_course_html, cleanup_audio_outputs
from app.tools.videoExport import estimate_duration, read_duration

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 生成任务注册表：runId → 后台 asyncio.Task ────────────────────────────────
# 前端中断/返回/刷新时先用 runId 调 /chat/cancel 主动 cancel 任务；
# 同时 SSE 生成器 finally 也会兜底 cancel，避免前端只 abort 连接而漏取消。
_running_tasks: dict[str, asyncio.Task] = {}


def _register_task(run_id: str, task: asyncio.Task) -> None:
    if run_id:
        _running_tasks[run_id] = task


def _forget_task(run_id: str, task: asyncio.Task) -> None:
    if run_id and _running_tasks.get(run_id) is task:
        _running_tasks.pop(run_id, None)


def _cancel_task(run_id: str) -> bool:
    """按 runId 取消正在执行的生成任务；返回是否命中一个仍在运行的任务。"""
    task = _running_tasks.get(run_id)
    if task and not task.done():
        task.cancel()
        return True
    return False


# ── 请求/响应模型（只追加可选字段，完全兼容）────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    sessionId: Optional[str] = None      # 多轮会话 id（可选）
    threadId: Optional[str] = None       # 图线程 id（confirm/retry 定位用，可选）
    autoConfirm: bool = True             # False 时大纲产出后等待人工确认
    history: Optional[list] = None       # 多轮历史 [{role, content}]（可选）
    runId: Optional[str] = None          # 本次生成任务 id（中断/取消定位用）


class ChatResponse(BaseModel):
    reply: str = ""
    needLecture: bool
    topic: str = ""
    courseHtml: str = ""
    videoUrl: str = ""
    partial: bool = False                # True 表示有失败页的降级交付
    failedPages: list = []               # 失败清单 [{ind, stage, error}]
    threadId: str = ""
    courseId: str = ""


class ConfirmRequest(BaseModel):
    threadId: str                        # 目标图线程 id
    catalogList: Optional[list] = None   # 用户编辑后的大纲
    confirmed: bool = True               # False 表示取消生成
    runId: Optional[str] = None          # 本次生成任务 id（中断/取消定位用）


class RetryRequest(BaseModel):
    sessionId: Optional[str] = None      # 会话 id（取产物快照）
    threadId: Optional[str] = None       # 兜底定位用
    inds: Optional[list] = None          # 要重试的模块 ind；不传=重试全部失败页
    runId: Optional[str] = None          # 本次生成任务 id（中断/取消定位用）


class CancelRequest(BaseModel):
    runId: str                           # 要取消的生成任务 id


# ── 通用工具 ────────────────────────────────────────────────────────────────
def _sse(event: dict) -> str:
    """把事件编码成 SSE（Server-Sent Events，服务端向浏览器单向推流）帧文本。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _snapshot_course_id(session_id: str) -> str:
    """从会话快照里取最近一次课件 id（供前端跳转课件库）。"""
    snapshot = sessions.get_snapshot(session_id)
    return str((snapshot or {}).get("courseId") or "")


def _terminal_payload(state: dict) -> dict:
    """把最终状态折成响应体（/chat 与终态事件共用）。"""
    failed = [
        row for row in (state.get("failedPages") or []) if isinstance(row, dict)
    ]
    return {
        "reply": state.get("reply") or "",
        "needLecture": bool(state.get("needLecture", True)),
        "topic": state.get("topic") or "",
        "courseHtml": state.get("courseHtml") or "",
        "videoUrl": state.get("videoUrl") or "",
        "partial": bool(failed),
        "failedPages": failed,
        "threadId": state.get("threadId") or "",
    }


def _push_terminal(state: dict, session_id: str, payload: dict) -> None:
    """终态推送：confirm / reply / error（主管已推）之外统一补 done 事件。

    - 人工确认中断（__interrupt__）→ confirm 事件（supervisor 已推过，这里只兜底）；
    - 闲聊 → reply 事件；
    - 正常生成 → done 事件，data 带 partial / failedPages / courseId。
    """
    if is_interrupted(state):
        if not payload.get("courseHtml"):
            _emit(
                "confirm",
                "start",
                catalogList=state.get("catalogList") or [],
                threadId=state.get("threadId") or "",
                message="请确认大纲",
            )
        return

    if not payload["needLecture"]:
        _emit("reply", "done", message=payload["reply"] or "")
        return

    if state.get("failMessage"):
        # 主管 fail() 已推过 error 事件，不再重复
        return

    _emit(
        "done",
        "done",
        courseHtml=payload["courseHtml"],
        videoUrl=payload["videoUrl"],
        partial=payload["partial"],
        failedPages=payload["failedPages"],
        courseId=_snapshot_course_id(session_id),
        threadId=payload["threadId"],
        message="课件生成完成",
    )


# ── POST /chat：一次性调用（既有接口，行为不变）────────────────────────────
@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest):
    """同步调用图，返回最终结果；不再推送进度事件。"""
    session_id = (payload.sessionId or "").strip()
    history = sessions.get_history(session_id) if session_id else []
    state = await run_classroom(
        payload.message,
        thread_id=payload.threadId,
        auto_confirm=payload.autoConfirm,
        history=history,
        session_id=session_id,
    )
    result = _terminal_payload(state)
    if session_id and result["needLecture"]:
        sessions.append_message(session_id, "user", payload.message)
        sessions.append_message(
            session_id, "assistant", result["reply"] or f"已生成课件：{result['topic']}"
        )
    return ChatResponse(**result, courseId=_snapshot_course_id(session_id))


# ── POST /chat/stream：SSE 进度流 ──────────────────────────────────────────
@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest):
    """SSE 进度流：图跑在后台任务，进度事件经队列推给前端。

    终态三种：done（生成成功，data 含 partial/failedPages）、reply（闲聊）、
    confirm（大纲等待人工确认，data 含 catalogList/threadId）。
    """
    session_id = (payload.sessionId or "").strip()
    thread_id = (payload.threadId or "").strip() or uuid4().hex
    run_id = (payload.runId or "").strip() or uuid4().hex
    events: asyncio.Queue = asyncio.Queue()

    # 先取历史（不含本轮消息）供意图识别，再记录本轮用户消息
    history = sessions.get_history(session_id) if session_id else []
    if session_id:
        sessions.append_message(session_id, "user", payload.message)

    async def _runner():
        try:
            state = await run_classroom(
                payload.message,
                thread_id=thread_id,
                auto_confirm=payload.autoConfirm,
                history=history,
                session_id=session_id,
            )
            result = _terminal_payload(state)
            if session_id and result["needLecture"]:
                sessions.append_message(
                    session_id,
                    "assistant",
                    result["reply"] or f"已生成课件：{result['topic']}",
                )
            _push_terminal(state, session_id, result)
        except Exception as exc:
            logger.exception("生成失败")
            _emit("error", "done", message=str(exc))
        finally:
            events.put_nowait(None)  # 结束标记
            set_progress_emitter(None)

    async def _event_stream():
        set_progress_emitter(events.put_nowait)
        task = asyncio.create_task(_runner())
        _register_task(run_id, task)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(events.get(), timeout=300)
                except asyncio.TimeoutError:
                    yield _sse({"step": "error", "status": "done", "message": "请求超时"})
                    break
                if event is None:
                    break
                yield _sse(event)
        finally:
            _forget_task(run_id, task)
            set_progress_emitter(None)
            # 客户端断开/主动取消时，不能让后台任务继续跑完：显式 cancel 并等待其收敛。
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("生成任务收尾异常")

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── POST /chat/confirm：人工确认大纲后恢复执行 ──────────────────────────────
@router.post("/chat/confirm")
async def chat_confirm(payload: ConfirmRequest):
    """SSE：恢复被 interrupt 的图执行。

    catalogList 可被用户编辑（标题/讲稿增删改），恢复后只重走 page/audio；
    confirmed=False 表示取消，图内收尾返回闲聊式回复。
    """
    thread_id = (payload.threadId or "").strip()
    if not thread_id:
        async def _bad():
            yield _sse({"step": "error", "status": "done", "message": "缺少 threadId"})

        return StreamingResponse(_bad(), media_type="text/event-stream")

    run_id = (payload.runId or "").strip() or thread_id
    events: asyncio.Queue = asyncio.Queue()
    answer = {
        "confirmed": bool(payload.confirmed),
        "catalogList": payload.catalogList or [],
    }

    async def _runner():
        try:
            state = await resume_classroom(thread_id, answer)
            result = _terminal_payload(state)
            _push_terminal(state, (state.get("sessionId") or ""), result)
        except Exception as exc:
            logger.exception("确认后恢复执行失败")
            _emit("error", "done", message=str(exc))
        finally:
            events.put_nowait(None)
            set_progress_emitter(None)

    async def _event_stream():
        set_progress_emitter(events.put_nowait)
        task = asyncio.create_task(_runner())
        _register_task(run_id, task)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(events.get(), timeout=300)
                except asyncio.TimeoutError:
                    yield _sse({"step": "error", "status": "done", "message": "请求超时"})
                    break
                if event is None:
                    break
                yield _sse(event)
        finally:
            _forget_task(run_id, task)
            set_progress_emitter(None)
            # 客户端断开/主动取消时，不能让后台任务继续跑完：显式 cancel 并等待其收敛。
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("生成任务收尾异常")

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── POST /chat/retry：手动重试失败页（图外直调，不受派发上限约束）──────────
@router.post("/chat/retry")
async def chat_retry(payload: RetryRequest):
    """SSE：重试失败页并重新组装课件。

    数据源是会话快照（sessions.get_snapshot）；失败清单与重试进度以事件推送。
    """
    session_id = (payload.sessionId or "").strip()
    run_id = (payload.runId or "").strip() or uuid4().hex
    events: asyncio.Queue = asyncio.Queue()

    async def _runner():
        try:
            await _retry_failed_pages(session_id, payload.inds)
        except Exception as exc:
            logger.exception("重试失败页失败")
            _emit("error", "done", message=str(exc))
        finally:
            events.put_nowait(None)
            set_progress_emitter(None)

    async def _event_stream():
        set_progress_emitter(events.put_nowait)
        task = asyncio.create_task(_runner())
        _register_task(run_id, task)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(events.get(), timeout=300)
                except asyncio.TimeoutError:
                    yield _sse({"step": "error", "status": "done", "message": "请求超时"})
                    break
                if event is None:
                    break
                yield _sse(event)
        finally:
            _forget_task(run_id, task)
            set_progress_emitter(None)
            # 客户端断开/主动取消时，不能让后台任务继续跑完：显式 cancel 并等待其收敛。
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("生成任务收尾异常")

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _retry_failed_pages(session_id: str, inds: Optional[list]) -> None:
    """重试失败页本体：重生成指定页/音频 → 重组装 → 刷新快照与资产库。"""
    snapshot = sessions.get_snapshot(session_id)
    if not snapshot:
        _emit("error", "done", message="没有可重试的课件快照，请先生成课件")
        return

    catalog = list(snapshot.get("catalogList") or [])
    page_list = list(snapshot.get("pageList") or [])
    audio_list = list(snapshot.get("audioList") or [])
    quiz_list = list(snapshot.get("quizList") or [])
    topic = str(snapshot.get("topic") or "").strip()
    course_id = str(snapshot.get("courseId") or "")
    course_html_path = str(snapshot.get("courseHtml") or "")

    # 目标 ind：请求指定 > 失败清单
    failed_rows = [
        row for row in (snapshot.get("failedPages") or []) if isinstance(row, dict)
    ]
    if inds:
        targets = sorted({int(ind) for ind in inds if str(ind).strip().lstrip("-").isdigit()})
    else:
        targets = sorted({int(row.get("ind")) for row in failed_rows if row.get("ind") is not None})
    if not targets:
        _emit("retry", "done", message="没有需要重试的页面")
        return

    total = len(targets)
    _emit("retry", "start", total=total, message=f"正在重试 {total} 个页面…")

    pages_by_ind = {int(page.get("ind") or 0): page for page in page_list if isinstance(page, dict)}
    audios_by_ind = {int(item.get("ind") or 0): item for item in audio_list if isinstance(item, dict)}
    still_failed: list[dict] = []
    done_count = 0

    for ind in targets:
        module = next(
            (m for m in catalog if isinstance(m, dict) and int(m.get("ind") or 0) == ind),
            None,
        )
        if module is None:
            still_failed.append({"ind": ind, "stage": "pages", "error": "大纲里没有这个模块"})
            continue

        # 重生成页面
        page_errors: list[Exception] = []

        def _on_error(exc: Exception) -> None:
            page_errors.append(exc)

        html = await _build_page(topic, module, len(catalog), _on_error)
        pages_by_ind[ind] = {"ind": ind, "context": _module_outline(module), "htmlpage": html}
        if page_errors:
            still_failed.append(
                {"ind": ind, "stage": "pages", "error": str(page_errors[0])[:200]}
            )

        # 重生成音频
        script = _clean_script("\n".join(module.get("context") or []))
        audio_path = ""
        duration = estimate_duration(script)
        if script:
            target_file = OUTPUT_DIR / "audio" / f"{uuid4().hex}.mp3"
            OUTPUT_DIR.joinpath("audio").mkdir(parents=True, exist_ok=True)
            try:
                await tts_save(script, target_file)
                audio_path = str(target_file)
                try:
                    duration = read_duration(target_file)
                except Exception as exc:
                    logger.warning("第 %s 页音频时长读取失败，按字数估算：%s", ind, exc)
            except Exception as exc:
                still_failed.append({"ind": ind, "stage": "audio", "error": str(exc)[:200]})
        audios_by_ind[ind] = {
            "ind": ind,
            "context": script,
            "audio_path": audio_path,
            "duration": duration,
        }

        done_count += 1
        _emit("retry", "progress", current=done_count, total=total, ind=ind)

    # 按大纲顺序汇总产物
    new_pages = [
        pages_by_ind.get(int(m.get("ind") or 0))
        for m in catalog
        if isinstance(m, dict) and pages_by_ind.get(int(m.get("ind") or 0))
    ]
    new_audios = [
        audios_by_ind.get(int(m.get("ind") or 0))
        for m in catalog
        if isinstance(m, dict) and audios_by_ind.get(int(m.get("ind") or 0))
    ]

    # 音频在课件组装后已清理；非目标页文件若已丢失，按快照讲稿补合成，避免重试后丢旁白
    for item in new_audios:
        if not isinstance(item, dict) or Path(str(item.get("audio_path") or "")).is_file():
            continue
        ind = int(item.get("ind") or 0)
        script = _clean_script(str(item.get("context") or ""))
        if not script:
            module = next(
                (m for m in catalog if isinstance(m, dict) and int(m.get("ind") or 0) == ind),
                None,
            )
            if module:
                script = _clean_script("\n".join(module.get("context") or []))
        if not script:
            continue
        target_file = OUTPUT_DIR / "audio" / f"{uuid4().hex}.mp3"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            await tts_save(script, target_file)
            item["audio_path"] = str(target_file)
            try:
                item["duration"] = read_duration(target_file)
            except Exception as exc:
                logger.warning("第 %s 页音频时长读取失败，按字数估算：%s", ind, exc)
        except Exception as exc:
            logger.warning("第 %s 页音频补合成失败，该页将无旁白：%s", ind, exc)

    # 重组装：写回原课件文件（URL 稳定），保留练习片段
    course_html = build_course_html(topic, new_pages, new_audios)
    course_html = _append_quiz_section(course_html, _quiz_section(quiz_list))
    if course_html_path:
        Path(course_html_path).write_text(course_html, encoding="utf-8")
    else:
        course_html_path = str(OUTPUT_DIR / "videos" / f"{uuid4().hex}.html")
        Path(course_html_path).parent.mkdir(parents=True, exist_ok=True)
        Path(course_html_path).write_text(course_html, encoding="utf-8")
    cleanup_audio_outputs()

    # 刷新快照与资产库（to_thread 包裹同步 SQLite）
    remaining = [
        row for row in failed_rows if int(row.get("ind") or -1) not in set(targets)
    ] + [row for row in still_failed]
    sessions.update_snapshot(
        session_id,
        pageList=new_pages,
        audioList=new_audios,
        failedPages=remaining,
        courseHtml=course_html_path,
    )
    # 保留原记录的创建时间与 id，状态随失败清单变化
    record = await asyncio.to_thread(course_db.get_course, course_id) if course_id else None
    try:
        await asyncio.to_thread(
            lambda: course_db.save_course(
                course_id=course_id or uuid4().hex,
                topic=topic,
                created_at=(record or {}).get("createdAt") or "",
                status="partial" if remaining else "done",
                page_count=len(new_pages),
                html_path=course_html_path,
                catalog_list=catalog,
                page_list=new_pages,
                audio_list=new_audios,
                quiz_list=quiz_list,
            )
        )
    except Exception:
        logger.warning("重试后资产库刷新失败", exc_info=True)

    _emit(
        "retry",
        "done",
        courseHtml=course_html_path,
        failedPages=remaining,
        partial=bool(remaining),
        message="重试完成" if not remaining else "重试完成，仍有页面失败",
    )


# ── POST /chat/cancel：主动取消正在进行的生成任务 ───────────────────────────
@router.post("/chat/cancel")
async def chat_cancel(payload: CancelRequest):
    """按 runId 取消后台生成任务。

    前端在「中断生成 / 返回首页 / 刷新页面」时调用，让服务端停止跑图（不只是断开 SSE 连接）。
    返回 cancelled 表示是否命中了一个仍在运行的任务。
    """
    run_id = (payload.runId or "").strip()
    cancelled = _cancel_task(run_id) if run_id else False
    logger.info("取消生成任务 runId=%s，命中=%s", run_id or "(空)", cancelled)
    return {"cancelled": cancelled}
