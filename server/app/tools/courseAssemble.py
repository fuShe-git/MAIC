"""把多页幻灯片 HTML 与逐页旁白音频组装成一份可独立播放的课件 HTML。

组装策略：
    - 每页都是完整 HTML 文档，这里原样塞进 <iframe srcdoc>，天然隔离各页样式与脚本，
      互不干扰（下期接 <video> 素材也无需改动组装逻辑）；
    - 旁白 mp3 以 base64 data URI 内嵌，课件单文件自包含，双击即可播放；
    - 播放控制是固定的原生 JS 模板（不经大模型）：播放/暂停切换，音频播完自动翻页，可手动上下页。

对外可用函数：
    build_course_html()  组装课件 HTML，返回完整文档字符串
"""

import base64
import logging
import shutil
from html import escape
from pathlib import Path

from app.agent.nodes import OUTPUT_DIR

logger = logging.getLogger(__name__)

# 缺旁白的页在播放时的兜底停留毫秒数
EMPTY_PAGE_MS = 8000

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{{TITLE}}</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { min-height: 100%; }
  body { display: flex; flex-direction: column; align-items: center;
         min-height: 100vh; background: #242424; font-family: "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; }
  .stage { position: relative; width: min(94vw, 1100px); aspect-ratio: 16 / 9; flex-shrink: 0; background: #000;
           border-radius: 12px; overflow: hidden; box-shadow: 0 24px 70px rgba(0, 0, 0, .45); }
  .page { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; display: none; }
  .page.active { display: block; }
  .controls { display: flex; flex-shrink: 0; align-items: center; gap: 14px; margin-top: 18px; color: #eee; }
  .controls button { background: #EC6520; color: #fff; border: 0; border-radius: 8px;
                     padding: 10px 24px; font-size: 15px; cursor: pointer; }
  .controls button:disabled { opacity: .4; cursor: not-allowed; }
  #pageNum { font-size: 14px; letter-spacing: 1px; }
</style>
</head>
<body>
  <div class="stage">
{{PAGES}}
  </div>
  <div class="controls">
    <button id="prev">上一页</button>
    <button id="start">播放</button>
    <button id="next">下一页</button>
    <span id="pageNum">1 / 1</span>
  </div>
  <audio id="player" preload="auto"></audio>
  <script>
(function () {
  var pages = Array.prototype.slice.call(document.querySelectorAll('.page'));
  var player = document.getElementById('player');
  var pageNum = document.getElementById('pageNum');
  var prevBtn = document.getElementById('prev');
  var nextBtn = document.getElementById('next');
  var startBtn = document.getElementById('start');
  var idx = 0;
  var loadedInd = -1;   // player 当前加载的是第几页音频（-1 表示还没加载过）
  var timer = null;     // 无音频页的自动翻页计时器
  var timerEnd = 0;     // 计时器计划触发的时间戳，暂停时用它算剩余停留
  var remaining = 0;    // 无音频页暂停后剩余的停留毫秒
  var playing = false;
  var autoPlay = true;  // 关闭后本页讲完即停，不再翻页
  var rate = 1;
  var volume = 1;

  function clamp(value, min, max) {
    var n = parseFloat(value);
    return isFinite(n) ? Math.min(max, Math.max(min, n)) : min;
  }

  function applyAudioSettings() {
    player.playbackRate = rate;
    player.volume = volume;
  }

  // 自动翻页入口：音频播完与无音频页定时结束都走这里
  function advance() {
    if (!autoPlay) { stop(); return; }
    play(idx + 1);
  }

  function render() {
    pages.forEach(function (p, i) { p.classList.toggle('active', i === idx); });
    pageNum.textContent = (idx + 1) + ' / ' + pages.length;
    prevBtn.disabled = (idx === 0);
    nextBtn.disabled = (idx === pages.length - 1);
  }

  function syncBtn() { startBtn.textContent = playing ? '暂停' : '播放'; }

  function pageMs(i) {
    return parseFloat(pages[i].getAttribute('data-duration')) * 1000 || {{EMPTY_MS}};
  }

  function schedule(ms) {
    timerEnd = Date.now() + ms;
    timer = setTimeout(advance, ms);
  }

  // 从第 i 页开头完整播放（切页、自动翻页、重新播放都走这里）
  function play(i) {
    if (i < 0 || i >= pages.length) {
      stop();
      // 自然播完最后一页：通知外部（课后练习据此弹出进入确认）
      if (i >= pages.length && pages.length > 0) {
        document.dispatchEvent(new CustomEvent('course:finished'));
      }
      return;
    }
    idx = i;
    render();
    player.pause();
    clearTimeout(timer); timer = null;
    remaining = 0;
    var audio = pages[i].getAttribute('data-audio');
    if (audio) {
      player.src = audio;
      applyAudioSettings();
      loadedInd = i;
      player.play().catch(function () {});
    } else {
      loadedInd = -1;
      schedule(pageMs(i));
    }
    playing = true;
    syncBtn();
  }

  // 暂停：音频挂起；无音频页记下剩余停留时间，恢复时续上
  function stop() {
    player.pause();
    if (timer !== null) {
      clearTimeout(timer);
      remaining = Math.max(0, timerEnd - Date.now());
      timer = null;
    }
    playing = false;
    syncBtn();
  }

  // 播放/暂停切换：暂停后继续则从断点续播，不重头开始
  function toggle() {
    if (playing) { stop(); return; }
    if (!pages.length) return;
    var audio = pages[idx].getAttribute('data-audio');
    if (audio) {
      if (loadedInd !== idx || player.ended) { play(idx); return; }
      player.play().catch(function () {});
    } else {
      schedule(remaining || pageMs(idx));
    }
    playing = true;
    syncBtn();
  }

  player.addEventListener('ended', advance);
  startBtn.addEventListener('click', function () { toggle(); });
  prevBtn.addEventListener('click', function () { play(idx - 1); });
  nextBtn.addEventListener('click', function () { play(idx + 1); });

  render();
  syncBtn();

  // 父页面通过同源 iframe 调用，统一控制自动播放 / 倍速 / 音量
  window.__coursePlayer = {
    setAutoPlay: function (value) { autoPlay = !!value; },
    setRate: function (value) { rate = clamp(value || 1, 0.25, 4); applyAudioSettings(); },
    setVolume: function (value) { volume = clamp(value || 0, 0, 1); applyAudioSettings(); }
  };

  // 重新观看：从第一页重播（前端底部「重新观看」按钮派发该事件）
  document.addEventListener('course:rewatch', function () { play(0); });
})();
  </script>
</body>
</html>
"""


def _audio_data_uri(path: str | Path) -> str:
    """把 mp3 文件读成 base64 data URI；文件不存在或读失败返回空串。"""
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        encoded = base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError as exc:
        logger.warning("音频读取失败，该页将无旁白：%s（%s）", p, exc)
        return ""
    return "data:audio/mpeg;base64," + encoded


def build_course_html(topic: str, pages: list[dict], audios: list[dict]) -> str:
    """把页面与音频组装成一份可独立播放的课件 HTML。

    Args:
        topic: 课件主题，写入 <title>。
        pages: 页面列表，每项是 PageModule（含 ind / htmlpage）。
        audios: 音频列表，每项是 AudioModule（含 ind / audio_path / duration）。

    Returns:
        str: 完整课件 HTML 文档。缺音频的页由播放 JS 按 data-duration 兜底自动翻页。
    """
    audio_by_ind = {int(a.get("ind") or 0): a for a in audios}

    frames: list[str] = []
    for index, page in enumerate(pages):
        ind = int(page.get("ind") or index + 1)
        audio = audio_by_ind.get(ind, {})
        uri = _audio_data_uri(audio.get("audio_path") or "")
        duration = float(audio.get("duration") or 0)
        srcdoc = escape(str(page.get("htmlpage") or ""), quote=True)
        frames.append(
            '<iframe class="page" data-ind="{}" data-duration="{:.2f}" data-audio="{}" '
            'srcdoc="{}"></iframe>'.format(ind, duration, uri, srcdoc)
        )

    return (
        _PAGE_TEMPLATE
        .replace("{{TITLE}}", escape(str(topic)))
        .replace("{{PAGES}}", "\n".join(frames))
        .replace("{{EMPTY_MS}}", str(EMPTY_PAGE_MS))
    )


def cleanup_audio_outputs() -> None:
    """删除 outputs/audio 目录及其下所有中间音频（课件已内嵌 base64，不再需要）。

    清理失败只记日志，不影响已生成的课件。
    """
    audio_dir = OUTPUT_DIR / "audio"
    if not audio_dir.exists():
        return
    try:
        shutil.rmtree(audio_dir)
    except OSError as exc:
        logger.warning("音频目录清理失败：%s（%s）", audio_dir, exc)
