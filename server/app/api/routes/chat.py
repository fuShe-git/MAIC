"""对话接口（v4）：多个接口共用一套 SSE 进度事件。

- POST /chat        一次性同步调用（既有行为不变）；
- POST /chat/stream SSE 进度流（新增 confirm 终态、partial/failedPages 终态字段）；
- POST /chat/confirm 人工确认大纲后恢复执行（SSE，interrupt 恢复）；
- POST /chat/generate 用已确认的大纲/样式新开线程生成课件（SSE）；
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
from app.agent.agent import reset_llm_config, resolve_llm_config, set_llm_config
from app.agent.agents.assemble_agent import _append_quiz_section, _quiz_section
from app.agent.agents.theme_agent import generate_theme_templates
from app.agent.graph import is_interrupted, resume_classroom, run_classroom, start_classroom
from app.agent.nodes import (
    OUTPUT_DIR,
    _build_page,
    _clean_script,
    _emit,
    _is_page_topic_relevant,
    _module_outline,
    set_progress_emitter,
)
from app.core.paths import artifact_url
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
    outlineOnly: bool = False            # True 时只做安全校验 + 大纲生成，跳过改写/检索
    topic: Optional[str] = None          # 重生成大纲时的真实讲解主题；避免把操作指令当成主题
    outlineHint: Optional[str] = None    # 重生成大纲时用户对大纲的补充要求（如新增页面）
    history: Optional[list] = None       # 多轮历史 [{role, content}]（可选）
    runId: Optional[str] = None          # 本次生成任务 id（中断/取消定位用）
    modelId: Optional[str] = None        # 所选模型「配置id:模型id」；空 / platform-default 用系统默认


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


class ThemePreviewColors(BaseModel):
    background: str = "#FFFFFF"
    primary: str = "#2F5BEA"
    accent: str = "#6C8CFF"
    text: str = "#1E1F29"


class ThemeTemplatePayload(BaseModel):
    id: str
    name: str
    description: str = ""
    previewColors: ThemePreviewColors = ThemePreviewColors()
    stylePrompt: str


class ThemeRequest(BaseModel):
    message: str                          # 用户最初输入的课程主题
    feedback: Optional[str] = None        # 重新生成时的修改意见
    currentThemes: Optional[list] = None  # 当前候选，便于定向调整
    modelId: Optional[str] = None


class ThemeListResponse(BaseModel):
    themes: list[ThemeTemplatePayload]
    degraded: bool = False                # True 表示服务端降级为内置样式


class ConfirmRequest(BaseModel):
    threadId: str                        # 目标图线程 id
    catalogList: Optional[list] = None   # 用户编辑后的大纲
    confirmed: bool = True               # False 表示取消生成
    runId: Optional[str] = None          # 本次生成任务 id（中断/取消定位用）
    modelId: Optional[str] = None        # 所选模型（确认后继续生成仍沿用同一模型）
    theme: Optional[dict] = None         # 用户选择的 PPT 样式，恢复后注入图状态


class GenerateRequest(BaseModel):
    topic: str                           # 用户最终确认的讲解主题
    catalogList: Optional[list] = None   # 用户最终确认的大纲
    theme: Optional[dict] = None         # 用户选择的 PPT 样式
    sessionId: Optional[str] = None      # 产物快照的会话 id
    runId: Optional[str] = None          # 本次生成任务 id（中断/取消定位用）
    modelId: Optional[str] = None        # 所选模型


class RetryRequest(BaseModel):
    sessionId: Optional[str] = None      # 会话 id（取产物快照）
    threadId: Optional[str] = None       # 兜底定位用
    inds: Optional[list] = None          # 要重试的模块 ind；不传=重试全部失败页
    runId: Optional[str] = None          # 本次生成任务 id（中断/取消定位用）
    modelId: Optional[str] = None        # 所选模型（重试沿用同一模型）
    theme: Optional[dict] = None         # 失败页重试沿用最初选择的 PPT 样式


class CancelRequest(BaseModel):
    runId: str                           # 要取消的生成任务 id


# ── POST /chat/themes：生成或重新生成 PPT 样式模板（普通 JSON，不走 SSE）────
@router.post("/chat/themes")
async def chat_themes(payload: ThemeRequest) -> ThemeListResponse:
    """一次生成 3 个视觉样式；失败时返回内置模板并标记 degraded。"""
    model_config = await resolve_llm_config(payload.modelId)
    llm_token = set_llm_config(model_config)
    try:
        themes, degraded = await generate_theme_templates(
            message=payload.message.strip(),
            feedback=(payload.feedback or "").strip(),
            current_themes=payload.currentThemes,
        )
        return ThemeListResponse(themes=themes, degraded=degraded)
    finally:
        reset_llm_config(llm_token)


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
        # 内部 state/快照仍保存绝对路径（重试要写回文件），仅对外响应转成同源 URL
        "courseHtml": artifact_url(state.get("courseHtml"), "html"),
        "videoUrl": artifact_url(state.get("videoUrl"), "media"),
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
    # 解析所选模型并注入当前上下文；get_llm 会在本次请求内优先使用它
    token = set_llm_config(await resolve_llm_config(payload.modelId))
    try:
        state = await run_classroom(
            payload.message,
            thread_id=payload.threadId,
            auto_confirm=payload.autoConfirm,
            outline_only=payload.outlineOnly,
            topic=payload.topic,
            outline_hint=payload.outlineHint,
            history=history,
            session_id=session_id,
        )
    finally:
        reset_llm_config(token)
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
    # 先按所选模型解析运行参数；create_task 会自动继承这份上下文
    model_config = await resolve_llm_config(payload.modelId)

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
                outline_only=payload.outlineOnly,
                topic=payload.topic,
                outline_hint=payload.outlineHint,
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
        # create_task 会复制当前上下文：后台任务里的 get_llm 能读到本次所选模型
        llm_token = set_llm_config(model_config)
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
            finally:
                reset_llm_config(llm_token)

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
    # 确认后继续生成仍沿用用户当前选中的模型
    model_config = await resolve_llm_config(payload.modelId)
    answer = {
        "confirmed": bool(payload.confirmed),
        "catalogList": payload.catalogList or [],
        "theme": payload.theme or None,
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
        # create_task 会复制当前上下文：后台任务里的 get_llm 能读到本次所选模型
        llm_token = set_llm_config(model_config)
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
            finally:
                reset_llm_config(llm_token)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── POST /chat/generate：用确认后的大纲与样式新开线程生成课件 ───────────────
@router.post("/chat/generate")
async def chat_generate(payload: GenerateRequest):
    """SSE：跳过意图/检索/大纲，直接用前端确认的大纲与 PPT 样式生成课件。

    每次调用都新建图线程：停止生成后再次生成时，旧线程的检查点里可能残留
    上一次的课件产物，恢复旧线程会把旧结果当成新结果返回。
    """
    session_id = (payload.sessionId or "").strip()
    run_id = (payload.runId or "").strip() or uuid4().hex
    topic = (payload.topic or "").strip()
    catalog_list = payload.catalogList or []
    events: asyncio.Queue = asyncio.Queue()
    model_config = await resolve_llm_config(payload.modelId)

    async def _runner():
        try:
            if not topic or not catalog_list:
                _emit("error", "done", message="缺少主题或大纲，请先完成大纲确认")
                return
            state = await start_classroom(
                topic,
                catalog_list,
                payload.theme,
                session_id=session_id,
            )
            result = _terminal_payload(state)
            _push_terminal(state, session_id, result)
        except Exception as exc:
            logger.exception("生成课件失败")
            _emit("error", "done", message=str(exc))
        finally:
            events.put_nowait(None)
            set_progress_emitter(None)

    async def _event_stream():
        set_progress_emitter(events.put_nowait)
        # create_task 会复制当前上下文：后台任务里的 get_llm 能读到本次所选模型
        llm_token = set_llm_config(model_config)
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
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("生成任务收尾异常")
            finally:
                reset_llm_config(llm_token)

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
    # 重试失败页同样使用用户当前选中的模型
    model_config = await resolve_llm_config(payload.modelId)

    async def _runner():
        try:
            await _retry_failed_pages(session_id, payload.inds, theme=payload.theme)
        except Exception as exc:
            logger.exception("重试失败页失败")
            _emit("error", "done", message=str(exc))
        finally:
            events.put_nowait(None)
            set_progress_emitter(None)

    async def _event_stream():
        set_progress_emitter(events.put_nowait)
        # create_task 会复制当前上下文：后台任务里的 get_llm 能读到本次所选模型
        llm_token = set_llm_config(model_config)
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
            finally:
                reset_llm_config(llm_token)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _retry_failed_pages(
    session_id: str,
    inds: Optional[list],
    theme: Optional[dict] = None,
) -> None:
    """重试失败页本体：重生成指定页/音频 → 重组装 → 刷新快照与资产库。"""
    snapshot = sessions.get_snapshot(session_id)
    if not snapshot:
        _emit("error", "done", message="没有可重试的课件快照，请先生成课件")
        return

    selected_theme = theme or snapshot.get("selectedTheme") or {}
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

        # 与正常生成同一口径：主题不相关/内容为空时不重生成该页，也不补旁白
        relevant = module.get("topicRelevant")
        if not isinstance(relevant, bool):
            relevant = await _is_page_topic_relevant(topic, module)
        if not relevant:
            logger.info("重试第 %s 页主题校验未通过，跳过生成", ind)
            pages_by_ind.pop(ind, None)
            audios_by_ind.pop(ind, None)
            done_count += 1
            _emit("retry", "skipped", ind=ind, message=f"第 {ind} 页 · 主题不相关或内容为空，已跳过")
            _emit("retry", "progress", current=done_count, total=total, ind=ind)
            continue

        # 重生成页面
        page_errors: list[Exception] = []

        def _on_error(exc: Exception) -> None:
            page_errors.append(exc)

        html = await _build_page(topic, module, len(catalog), _on_error, selected_theme)
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
        selectedTheme=selected_theme,
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
        courseHtml=artifact_url(course_html_path, "html"),
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
