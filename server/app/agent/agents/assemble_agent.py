"""课件组装子 agent（迁自旧 html_audio_assemble）+ quizList 追加 + SQLite 快照落库。

- 常规：页面 + 音频组装成自包含课件 HTML，课件末尾附加课后练习，落盘并写资产库；
  练习默认隐藏，讲解播完后弹出「是否进入课后练习」确认，确认后才显示交互表单；
- quiz_only 意图（新增能力 3）：把测验模块追加到历史课件 HTML 并更新同一条 DB 记录；
  无历史课件则生成独立测验页（单模块无音频）新建记录，此时练习直接展示；
- 落库走 asyncio.to_thread（SQLite 是同步 IO），异常只记日志不阻断生成；
- MP4 导出注释块原样保留（暂不导出视频）。
"""

import asyncio
import json
import logging
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Optional
from uuid import uuid4

from app.agent.nodes import OUTPUT_DIR, _emit, localized
from app.agent.sessions import get_snapshot, save_snapshot, update_snapshot
from app.storage import course_db
from app.tools.courseAssemble import build_course_html, cleanup_audio_outputs

logger = logging.getLogger(__name__)


_QUIZ_START = "<!-- QUIZ_SECTION_START -->"
_QUIZ_END = "<!-- QUIZ_SECTION_END -->"

_QUIZ_STYLE = """
<style>
.quiz-prompt{position:fixed;left:50%;bottom:32px;transform:translateX(-50%);z-index:50;display:none;align-items:center;gap:18px;background:rgba(36,36,36,.96);color:#fff;padding:16px 24px;border-radius:999px;box-shadow:0 12px 40px rgba(0,0,0,.5);font-size:16px;white-space:nowrap}
.quiz-prompt button{background:#EC6520;color:#fff;border:0;border-radius:999px;padding:10px 26px;font-size:15px;cursor:pointer}
.quiz-block{flex-shrink:0;font-family:'PingFang SC','Microsoft YaHei',system-ui,sans-serif}
.quiz-item{margin-bottom:22px;color:#4A4A4A;font-size:16px;line-height:1.7}
.quiz-q{font-size:17px;font-weight:600;color:#333}
.quiz-tag{display:inline-block;margin-left:8px;padding:1px 8px;border-radius:6px;background:#FFE2C4;color:#B45309;font-size:12px;font-weight:500}
.quiz-options{display:flex;flex-direction:column;gap:8px;margin-top:10px}
.quiz-opt{display:flex;align-items:center;gap:10px;padding:9px 14px;border:1px solid #EBD9BC;border-radius:8px;background:#fff;cursor:pointer}
.quiz-opt:hover{border-color:#EC6520}
.quiz-opt input{width:16px;height:16px;accent-color:#EC6520;cursor:pointer}
.quiz-feedback{display:none;margin-top:10px;padding:8px 12px;border-radius:6px;background:#FFF3E0;font-size:14px}
.quiz-ok{color:#2E8B57}.quiz-no{color:#C0392B}
.quiz-submit-wrap{text-align:center;margin-top:28px}
#quizSubmit{background:#EC6520;color:#fff;border:0;border-radius:999px;padding:13px 46px;font-size:17px;cursor:pointer;box-shadow:0 6px 18px rgba(236,101,32,.3)}
.quiz-result{display:none;margin-top:22px;text-align:center;font-size:20px;color:#E4620B}
.quiz-result strong{font-size:26px}
</style>
"""

_QUIZ_SCRIPT = r"""
<script>
(function () {
  var answers = __ANSWERS__;
  var analyses = __ANALYSES__;
  var ui = __QUIZ_UI__;
  var total = answers.length;
  var form = document.getElementById('quizForm');
  var prompt = document.getElementById('quizPrompt');
  var block = document.getElementById('quizBlock');
  var enterBtn = document.getElementById('quizEnter');
  var submitBtn = document.getElementById('quizSubmit');
  var result = document.getElementById('quizResult');
  var lecture = document.querySelector('.stage');
  var controls = document.querySelector('.controls');

  function enterQuiz() {
    if (prompt) prompt.style.display = 'none';
    if (lecture) lecture.style.display = 'none';
    if (controls) controls.style.display = 'none';
    if (block) { block.style.display = 'block'; window.scrollTo(0, 0); }
    document.body.classList.add('quiz-mode');
  }

  // 重新观看：恢复课件区、清空答题状态，播放脚本收到同一事件后回到第一页
  document.addEventListener('course:rewatch', function () {
    if (block) block.style.display = 'none';
    if (prompt) prompt.style.display = 'none';
    if (lecture) lecture.style.display = 'block';
    document.body.classList.remove('quiz-mode');
    if (form) form.reset();
    if (submitBtn) submitBtn.style.display = 'none';
    if (result) { result.style.display = 'none'; result.innerHTML = ''; }
    for (var i = 0; i < total; i++) {
      var fb = document.getElementById('fb' + i);
      if (fb) { fb.style.display = 'none'; fb.innerHTML = ''; }
      if (form) {
        var items = form.querySelectorAll('input[name="q' + i + '"]');
        for (var k = 0; k < items.length; k++) items[k].disabled = false;
      }
    }
  });

  if (enterBtn) enterBtn.addEventListener('click', enterQuiz);
  if (prompt) {
    document.addEventListener('course:finished', function () { prompt.style.display = 'flex'; });
  }

  function checkComplete() {
    if (!submitBtn || !form) return;
    var all = total > 0;
    for (var i = 0; i < total; i++) {
      if (!form.querySelectorAll('input[name="q' + i + '"]:checked').length) { all = false; break; }
    }
    submitBtn.style.display = all ? 'inline-block' : 'none';
  }
  if (form) form.addEventListener('change', checkComplete);

  if (form) form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (!total) return;
    var score = 0;
    for (var i = 0; i < total; i++) {
      var picked = [];
      var checked = form.querySelectorAll('input[name="q' + i + '"]:checked');
      for (var j = 0; j < checked.length; j++) picked.push(parseInt(checked[j].value, 10));
      picked.sort(function (a, b) { return a - b; });
      var correct = (answers[i] || []).slice().sort(function (a, b) { return a - b; });
      var ok = picked.length === correct.length &&
        picked.every(function (v, k) { return v === correct[k]; });
      if (ok) score++;
      var fb = document.getElementById('fb' + i);
      if (fb) {
        var letters = correct.map(function (v) { return String.fromCharCode(65 + v); }).join(ui.letterJoiner);
        fb.innerHTML = ok
          ? '<span class="quiz-ok">' + ui.correct + '</span>'
          : '<span class="quiz-no">' + ui.wrong + '</span>' + ui.correctAnswer + letters +
            (analyses[i] ? ui.analysis + analyses[i] : '');
        fb.style.display = 'block';
      }
      var inputs = form.querySelectorAll('input[name="q' + i + '"]');
      for (var k = 0; k < inputs.length; k++) inputs[k].disabled = true;
    }
    if (submitBtn) submitBtn.style.display = 'none';
    if (result) {
      result.style.display = 'block';
      result.innerHTML = ui.score + '<strong>' + score + ' / ' + total + '</strong>';
    }
  });

  if (__STANDALONE__) enterQuiz();
})();
</script>
"""


def _quiz_section(quiz_list: list[dict], standalone: bool = False) -> str:
    """生成课后练习片段：确认提示 + 单选/多选表单 + 计分脚本。

    standalone=True 时无讲解环节（独立测验页），练习直接展示并跳过确认提示。
    答案以 JSON 内嵌供前端计分，页面初始不展示答案。
    """
    if not quiz_list:
        return ""

    entries: list[tuple[dict, list[int], str]] = []
    for quiz in quiz_list:
        raw = quiz.get("answer")
        if isinstance(raw, (list, tuple)):
            nums = []
            for value in raw:
                try:
                    nums.append(int(value))
                except (TypeError, ValueError):
                    continue
        else:
            try:
                nums = [int(raw)]
            except (TypeError, ValueError):
                nums = []
        if not nums or not str(quiz.get("question") or "").strip():
            continue
        entries.append((quiz, nums, escape(str(quiz.get("analysis") or ""))[:300]))

    if not entries:
        return ""

    items: list[str] = []
    answers: list[list[int]] = []
    analyses: list[str] = []
    for i, (quiz, ans, analysis) in enumerate(entries):
        answers.append(ans)
        analyses.append(analysis)
        multiple = len(ans) > 1
        qtype = "checkbox" if multiple else "radio"
        tag = (
            '<span class="quiz-tag">' + localized("多选", "Multiple answers") + '</span>'
            if multiple else ""
        )
        options = "".join(
            '<label class="quiz-opt"><input type="' + qtype + '" name="q' + str(i) +
            '" value="' + str(idx) + '"><span>' + chr(65 + idx) + '. ' +
            escape(str(opt)) + '</span></label>'
            for idx, opt in enumerate(quiz.get("options") or [])
        )
        items.append(
            '<div class="quiz-item"><div class="quiz-q">' + str(i + 1) + '. ' +
            escape(str(quiz.get("question") or "")) + tag +
            '</div><div class="quiz-options">' + options +
            '</div><div class="quiz-feedback" id="fb' + str(i) + '"></div></div>'
        )

    answers_json = json.dumps(answers, ensure_ascii=False).replace("</", "<\\/")
    analyses_json = json.dumps(analyses, ensure_ascii=False).replace("</", "<\\/")
    quiz_ui = {
        "correct": localized("✓ 回答正确", "✓ Correct"),
        "wrong": localized("✗ 回答错误", "✗ Incorrect"),
        "correctAnswer": localized("　正确答案：", " Correct answer: "),
        "analysis": localized("　解析：", " Explanation: "),
        "score": localized("得分：", "Score: "),
        "letterJoiner": localized("、", ", "),
    }
    ui_json = json.dumps(quiz_ui, ensure_ascii=False).replace("</", "<\\/")
    script = (
        _QUIZ_SCRIPT.replace("__ANSWERS__", answers_json)
        .replace("__ANALYSES__", analyses_json)
        .replace("__QUIZ_UI__", ui_json)
        .replace("__STANDALONE__", "true" if standalone else "false")
    )

    prompt = "" if standalone else (
        '<div class="quiz-prompt" id="quizPrompt"><span>' +
        localized("讲解已完成，是否进入课后练习？", "Lecture finished. Ready for the practice quiz?") +
        '</span><button type="button" id="quizEnter">' +
        localized("确认进入", "Continue") + '</button></div>'
    )
    block_display = "block" if standalone else "none"
    block = (
        '<section class="quiz-block" id="quizBlock" style="display:' + block_display +
        ';max-width:1040px;margin:24px auto;background:#FFF9EA;border-radius:12px;padding:28px 36px;">'
        '<h2 style="color:#E4620B;font-size:24px;margin-bottom:20px;">' +
        localized("课后练习", "Practice quiz") + '</h2>'
        '<form id="quizForm" novalidate>' + "".join(items) +
        '<div class="quiz-submit-wrap"><button type="submit" id="quizSubmit" style="display:none;">' +
        localized("提交", "Submit") + '</button></div>'
        '<div class="quiz-result" id="quizResult"></div></form></section>'
    )
    return _QUIZ_START + prompt + block + _QUIZ_STYLE + script + _QUIZ_END


def _append_quiz_section(course_html: str, quiz_html: str) -> str:
    """把练习片段插到课件 HTML 的 </body> 前；重复追加时整体替换旧片段。"""
    if not quiz_html:
        return course_html
    start = course_html.find(_QUIZ_START)
    end = course_html.find(_QUIZ_END)
    if start != -1 and end > start:
        return course_html[:start] + quiz_html + course_html[end + len(_QUIZ_END):]
    lower = course_html.lower()
    at = lower.rfind("</body>")
    if at == -1:
        return course_html + "\n" + quiz_html
    return course_html[:at] + quiz_html + "\n" + course_html[at:]


def _build_quiz_only_page(topic: str, quiz_list: list[dict], theme: Optional[dict] = None) -> str:
    """无历史课件时的独立测验页：单模块走既有组装函数（无音频），复用同一套播放器。"""
    from app.agent.nodes import _fallback_page

    quiz_title = localized("课后测验", "Practice quiz")
    default_topic = quiz_title
    pages = [
        {
            "ind": 1,
            "context": f"{topic}：{quiz_title}",
            "htmlpage": "",
        }
    ]
    # build_course_html 需要页面 HTML；测验内容以附加片段承载，底页给一句引导
    module = {
        "ind": 1,
        "title": [quiz_title],
        "context": [
            localized(
                "下面是本课的练习题，做完再对答案，看看掌握得怎么样。",
                "Review the practice questions below, then check your answers and see how you did.",
            )
        ],
    }
    pages[0]["htmlpage"] = _fallback_page(topic or default_topic, module, theme)
    course_html = build_course_html(topic or default_topic, pages, [])
    return _append_quiz_section(course_html, _quiz_section(quiz_list, standalone=True))


async def assemble_agent(state: dict) -> dict:
    """整理页面 + 音频 + 练习，组装课件 HTML 并写 SQLite 快照。"""
    from app.tools.videoExport import estimate_duration  # noqa: F401  MP4 恢复时需要

    topic = (state.get("topic") or state.get("userIssue") or "课件").strip()
    pages = list(state.get("pageList") or [])
    # 主题校验跳过的页不进 pageList，同步过滤其旁白，避免最终课件/资产库出现多余音频
    page_inds = {int(page.get("ind") or 0) for page in pages if isinstance(page, dict)}
    audios = [
        item for item in (state.get("audioList") or [])
        if isinstance(item, dict) and int(item.get("ind") or 0) in page_inds
    ]
    quiz_list = list(state.get("quizList") or [])
    intent = str(state.get("intent") or "new_lecture")
    session_id = state.get("sessionId") or ""
    theme = state.get("selectedTheme") or {}

    _emit("assemble", "start", total=len(pages), message="正在组装课件…")

    # quiz_only：优先追加到历史课件，其次生成独立测验页
    if intent == "quiz_only":
        result = await _assemble_quiz_only(topic, quiz_list, session_id, theme)
        _emit("assemble", "done", message="课件组装完成")
        return result

    if not pages:
        logger.warning("没有页面，跳过组装与导出")
        _emit("assemble", "done", message="没有可组装的页面")
        return {"courseHtml": "", "videoUrl": "", "stageDone": {"assemble": True}}

    video_dir = OUTPUT_DIR / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)
    uid = uuid4().hex

    # 1. 组装课件 HTML（每页 base64 音频 + 固定播放 JS），课件末尾附加课后练习
    course_html = build_course_html(topic, pages, audios)
    course_html = _append_quiz_section(course_html, _quiz_section(quiz_list))
    course_path = video_dir / f"{uid}.html"
    course_path.write_text(course_html, encoding="utf-8")
    cleanup_audio_outputs()
    _emit("assemble", "done", message="课件组装完成")

    # 2. 写资产库快照（to_thread 包裹同步 SQLite）；失败页非空标记 partial
    failed = list(state.get("failedPages") or [])
    status = "partial" if failed else "done"
    await _save_course_record(
        course_id=uid,
        topic=topic,
        status=status,
        page_count=len(pages),
        html_path=str(course_path),
        catalog_list=state.get("catalogList") or [],
        page_list=pages,
        audio_list=audios,
        quiz_list=quiz_list,
    )
    save_snapshot(
        session_id,
        {
            "topic": topic,
            "catalogList": state.get("catalogList") or [],
            "pageList": pages,
            "audioList": audios,
            "quizList": quiz_list,
            "selectedTheme": theme,
            "courseId": uid,
            "courseHtml": str(course_path),
        },
    )

    # 3. 截帧 + FFmpeg 合成 MP4；与课件 HTML 不冲突（MP4 只依赖前面产物）。
    #    暂不导出视频（等待页只需课件 HTML 即可开始学习），需要时取消下方注释即可恢复。
    # audio_by_ind = {int(a.get("ind") or 0): a for a in audios}
    # audio_paths: list[str] = []
    # durations: list[float] = []
    # for page in pages:
    #     audio = audio_by_ind.get(int(page.get("ind") or 0), {})
    #     audio_paths.append(str(audio.get("audio_path") or ""))
    #     duration = audio.get("duration")
    #     if not duration:
    #         duration = estimate_duration(str(page.get("context") or ""))
    #     durations.append(float(duration))
    #
    # video_path = ""
    # try:
    #     frames = await render_page_frames([page["htmlpage"] for page in pages])
    #     video_path = str(
    #         await asyncio.to_thread(export_mp4, frames, audio_paths, durations, video_dir)
    #     )
    # except Exception as exc:
    #     logger.warning("视频导出失败，本次仅交付课件 HTML：%s", exc)

    video_path = ""  # 暂不导出视频，恒为空串

    return {
        "courseHtml": str(course_path),
        "videoUrl": video_path,
        "stageDone": {"assemble": True},
    }


async def _assemble_quiz_only(
    topic: str, quiz_list: list[dict], session_id: str, theme: Optional[dict] = None
) -> dict:
    """quiz_only 产物（R6）：追加历史课件或生成独立测验页。"""
    snapshot = get_snapshot(session_id)
    quiz_html = _quiz_section(quiz_list)
    video_dir = OUTPUT_DIR / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)

    # 1. 有历史课件：测验片段追加进原 HTML 文件，更新同一条 DB 记录
    if snapshot and snapshot.get("courseId") and snapshot.get("courseHtml"):
        course_path = Path(str(snapshot["courseHtml"]))
        try:
            original = course_path.read_text(encoding="utf-8")
            course_path.write_text(_append_quiz_section(original, quiz_html), encoding="utf-8")
            merged_quiz = list(snapshot.get("quizList") or []) + list(quiz_list)
            await _save_course_record(
                course_id=str(snapshot["courseId"]),
                topic=str(snapshot.get("topic") or topic),
                status="done",
                page_count=len(snapshot.get("pageList") or []),
                html_path=str(course_path),
                catalog_list=snapshot.get("catalogList") or [],
                page_list=snapshot.get("pageList") or [],
                audio_list=snapshot.get("audioList") or [],
                quiz_list=merged_quiz,
            )
            update_snapshot(session_id, quizList=merged_quiz)
            return {
                "courseHtml": str(course_path),
                "videoUrl": "",
                "stageDone": {"assemble": True},
            }
        except Exception as exc:  # 读写失败落到独立测验页
            logger.warning("历史课件追加测验失败，改为独立测验页：%s", exc)

    # 2. 无历史课件：生成独立测验页并新建记录
    uid = uuid4().hex
    course_html = _build_quiz_only_page(topic, quiz_list, theme)
    course_path = video_dir / f"{uid}.html"
    course_path.write_text(course_html, encoding="utf-8")
    await _save_course_record(
        course_id=uid,
        topic=f"{topic} · {localized('课后测验', 'Practice quiz')}",
        status="done",
        page_count=1,
        html_path=str(course_path),
        catalog_list=[],
        page_list=[],
        audio_list=[],
        quiz_list=quiz_list,
    )
    save_snapshot(
        session_id,
        {
            "topic": topic,
            "catalogList": [],
            "pageList": [],
            "audioList": [],
            "quizList": quiz_list,
            "courseId": uid,
            "courseHtml": str(course_path),
        },
    )
    return {"courseHtml": str(course_path), "videoUrl": "", "stageDone": {"assemble": True}}


async def _save_course_record(**fields) -> None:
    """写课件资产库（to_thread 包裹同步 SQLite），异常只记日志。"""
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        await asyncio.to_thread(
            lambda: course_db.save_course(created_at=created_at, **fields)
        )
    except Exception:
        logger.warning("课件资产落库失败", exc_info=True)
