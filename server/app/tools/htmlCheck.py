"""先用不着，不检查，等流程跑通再说
HTML 页面自检：无头浏览器渲染截图 → 多模态大模型走查 → 模型返工修正，循环到合格为止。
对外可用函数：
    html_to_screenshot()  把 HTML 渲染成 PNG 截图，返回 (png 字节, 页面尺寸信息)
    check_html_page()     截图 → 模型评审 → 模型返工 的循环，返回最终 HTML

临时文件策略：截图默认只留在内存里，不产生磁盘文件；渲染引擎用的临时目录
（page.html / page.png / 浏览器 profile）在渲染结束或报错时自动删除；
即便为了调试传了 output_dir，只要 keep_screenshots=False，退出前也会清理干净。

渲染引擎：
    1. 优先 Playwright（异步 API）：能等字体加载、能读页面真实尺寸、能截全页；
       安装：pip install playwright && playwright install chromium
    2. 没有 Playwright 时自动回退调用本机 Chrome / Edge 的 --headless --screenshot，
       只要求系统装过浏览器（Windows 上基本都有 Edge），无需额外依赖。

环境变量（均可选）：
    HTML_CHECK_ENGINE      auto(默认) / playwright / cli，强制指定渲染引擎
    HTML_CHECK_BROWSER     无头浏览器可执行文件路径，回退引擎找不到浏览器时使用
    HTML_CHECK_MAX_TOKENS  评审+返工阶段单次模型输出上限，默认 8000
    HTML_CHECK_TIMEOUT     单次渲染超时秒数，默认 60

注意：评审依赖截图，模型必须支持视觉输入（多模态），否则调用会报错。
"""

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage

# 显式指定 server/.env，否则以脚本方式运行时读不到大模型配置。
# override=True：.env 是本模块显式声明的配置源，应该压过 shell/系统里的同名残留变量
#   （默认 override=False 时，终端里遗留的旧 API_KEY 会让请求拿着过期 key 去鉴权，表现为 401）；
# encoding="utf-8-sig"：兼容被记事本/PowerShell 存成「UTF-8 带 BOM」的 .env，
#   否则 BOM 会粘在第一个键名上导致该变量读不到。
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH, encoding="utf-8-sig", override=True)

try:  # 以模块形式运行（被 app 内其他模块导入、或 python -m app.tools.htmlCheck）
    from app.agent.agent import get_llm
except ImportError:  # 直接运行 python app/tools/htmlCheck.py 时，把 server 目录补进搜索路径
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.agent.agent import get_llm

logger = logging.getLogger(__name__)

# 默认视口按 16:9 的课件画布设置，截出来的图与用户实际看到的比例一致
DEFAULT_VIEWPORT = (1280, 720)
DEFAULT_MAX_ROUNDS = 3      # 最多让模型返工几次
DEFAULT_TIMEOUT = 60.0      # 单次渲染（含浏览器启动）的超时秒数
DEFAULT_MAX_TOKENS = 8000   # 返工时要输出整份 HTML，上限给足避免被截断

# 模型输出的分隔标记：前半段是 JSON 结论，后半段是修正后的完整 HTML
HTML_MARKER = "===HTML==="

# 页面尺寸采集脚本：内容尺寸大于视口就说明有溢出
_METRICS_JS = """
() => {
  const doc = document.documentElement;
  const body = document.body || {};
  return {
    viewportWidth: window.innerWidth,
    viewportHeight: window.innerHeight,
    scrollWidth: Math.max(doc.scrollWidth || 0, body.scrollWidth || 0),
    scrollHeight: Math.max(doc.scrollHeight || 0, body.scrollHeight || 0),
  };
}
"""

# 系统里常见的浏览器位置（Windows 优先，兼顾 macOS / Linux）
_BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]

REVIEW_PROMPT = """你是 MAIC 课件的视觉走查员，负责检查一页 HTML 页面的渲染截图是否达到「能直接放给学生看」的标准。

这类页面通常是 16:9 的卡片式幻灯片：暖色底、大标题、左右两栏、底部横幅。
你只报「明显影响观感」的问题，判断范围仅限下面几类：
1. 溢出与裁切：文字或元素超出卡片/页面可视范围，被截断、被压扁，或出现滚动条；
2. 遮挡重叠：文字压住文字，元素互相覆盖，层级错乱；
3. 排版失衡：某一栏内容过多把整页挤满、两栏高度严重不齐、大片空白让版面失去重心；
4. 可读性：字号过小、文字与背景颜色过近导致看不清、长段落没有行距；
5. 结构缺失：本应有标题/正文/横幅的位置是空的；
6. 对齐与间距：一眼可见的错位（元素跑出容器、间距忽大忽小到不协调）。

以下情况不算问题，不要报：字体族的细微差异、5px 以内的间距误差、配色审美偏好、文案措辞、
以及任何必须改文字内容才能解决的问题。

输出格式（严格遵守，不要输出额外解释）：
第 1 行起先给 JSON：{"hasIssue": true/false, "issues": ["问题1（说清位置和表现）", "问题2"]}
之后单独一行写 ===HTML===
最后是修正后的完整 HTML 文档（以 <html 或 <!DOCTYPE html> 开头，以 </html> 结尾）。
hasIssue 为 false 时，可以省略 ===HTML=== 与 HTML 部分。

修正要求：保持原有配色、字体、整体布局与全部文字内容不变，只针对列出的问题做最小改动；
可以调整字号、间距、flex/grid 尺寸、overflow、换行与容器高度；禁止 <script>，禁止外链资源。"""


# ---------------------------------------------------------------------------
# 内部工具：文本清洗与模型输出解析
# ---------------------------------------------------------------------------

def _parse_json(raw: str) -> dict:
    """从模型输出中提取 JSON 对象；解析失败返回空字典，由调用方兜底。"""
    text = (raw or "").strip()
    if text.startswith("```"):  # 去掉 ```json ... ``` 围栏
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("走查结论不是合法 JSON：%s", raw[:200])
        return {}
    return data if isinstance(data, dict) else {}


def _clean_html(raw: str) -> str:
    """去掉模型附带的 ```html 围栏，并截取 <html> 到 </html> 之间的内容。"""
    text = (raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"```\s*$", "", text).strip()
    lower = text.lower()
    start, end = lower.find("<html"), lower.rfind("</html>")
    if start != -1 and end > start:
        return text[start : end + len("</html>")]
    return text


def _as_str_list(value) -> list[str]:
    """把模型给的问题列表统一成字符串列表，过滤空项。"""
    if isinstance(value, str):
        return [part.strip() for part in value.split("\n") if part.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _message_text(content) -> str:
    """把模型返回的 content 统一转成字符串；分段列表只取文本部分。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                chunks.append(str(part.get("text") or ""))
        return "".join(chunks)
    return str(content)


def _is_usable_html(text: str) -> bool:
    """判断模型给出的返工结果是否可用：既要是 HTML，也不能被 max_tokens 截断。"""
    candidate = (text or "").strip()
    if not candidate or not candidate.startswith("<"):
        return False
    lower = candidate.lower()
    if "<html" in lower or "<!doctype" in lower:
        return "</html>" in lower  # 完整性校验，避免写入半截文档
    return True  # 片段形式（未来若支持片段也放行）


def _save_png(output_dir: str | Path | None, name: str, png: bytes) -> Path | None:
    """把截图落盘，方便人工复核；不传目录时直接跳过。

    Returns:
        Path | None: 实际写出的文件路径，写失败或不落盘时返回 None（由调用方登记待清理文件）。
    """
    if not output_dir:
        return None
    try:
        directory = Path(output_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        target.write_bytes(png)
        return target
    except OSError as exc:  # 落盘失败不影响主流程
        logger.warning("截图保存失败：%s", exc)
        return None


def _cleanup_screenshots(paths: list[Path]) -> None:
    """删除本次运行写出的截图，并尝试删掉因此变空的目录。

    只删自己登记过的文件、只 rmdir 空目录，绝不递归删除，
    避免误伤用户放在同一目录里的其他东西。
    """
    parents: set[Path] = set()
    for path in paths:
        try:
            path.unlink(missing_ok=True)
            parents.add(path.parent)
        except OSError as exc:  # 文件被占用等，记录后继续清下一个
            logger.warning("截图删除失败 %s：%s", path, exc)
    for parent in parents:
        try:
            parent.rmdir()  # 非空会抛 OSError，说明还有别的东西，保持原样
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 渲染：Playwright 优先，找不到时回退系统浏览器命令行
# ---------------------------------------------------------------------------

async def _shot_with_playwright(
    html: str,
    width: int,
    height: int,
    full_page: bool,
) -> tuple[bytes, dict]:
    """用 Playwright 渲染并截图，返回 (png 字节, 页面尺寸信息)。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            args=["--disable-gpu", "--no-sandbox", "--font-render-hinting=none"]
        )
        try:
            page = await browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=1,  # 1 倍图足够评审，2 倍图只会白白放大 token
            )
            await page.set_content(html, wait_until="load")
            # 等字体就绪再截图，否则中文字体首帧常出现宽度抖动、看起来像排版问题
            await page.evaluate(
                "async () => { if (document.fonts && document.fonts.ready) { await document.fonts.ready; } }"
            )
            await page.wait_for_timeout(200)
            metrics = await page.evaluate(_METRICS_JS)
            png = await page.screenshot(type="png", full_page=full_page)
        finally:
            await browser.close()

    metrics["engine"] = "playwright"
    return png, metrics


def _find_browser() -> str | None:
    """按「环境变量 → PATH → 常见安装路径」的顺序找无头浏览器可执行文件。"""
    env_path = (os.getenv("HTML_CHECK_BROWSER") or "").strip()
    if env_path and Path(env_path).is_file():
        return env_path
    for name in ("chrome", "chrome.exe", "msedge", "msedge.exe", "chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _BROWSER_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def _shot_with_cli_sync(
    html: str,
    browser_path: str,
    width: int,
    height: int,
    timeout: float,
) -> tuple[bytes, dict]:
    """同步调用浏览器命令行截图（放在线程里跑，避免阻塞事件循环）。

    注意：命令行模式只能截视口范围，也读不到页面尺寸，
    因此尺寸信息里只给视口大小，评审时模型主要看图。

    临时目录（page.html / page.png / 浏览器 profile）在退出 with 时自动删除，
    正常结束和抛异常都会清理；ignore_cleanup_errors 用于兜住 Windows 上
    浏览器子进程尚未完全退出、文件句柄短暂被占用导致删除失败的情况。
    """
    with tempfile.TemporaryDirectory(prefix="html_check_", ignore_cleanup_errors=True) as tmp:
        tmp_dir = Path(tmp)
        html_path = tmp_dir / "page.html"
        png_path = tmp_dir / "page.png"
        html_path.write_text(html, encoding="utf-8")

        command = [
            browser_path,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--force-device-scale-factor=1",
            f"--user-data-dir={tmp_dir / 'profile'}",
            f"--window-size={width},{height}",
            f"--screenshot={png_path}",
            html_path.as_uri(),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"无头浏览器截图超时（{timeout:.0f} 秒）") from exc

        if not png_path.is_file() or png_path.stat().st_size == 0:
            stderr = (result.stderr or b"").decode("utf-8", errors="ignore").strip()
            raise RuntimeError(f"无头浏览器没有产出截图：{stderr[:300] or '未知原因'}")
        return png_path.read_bytes(), {
            "viewportWidth": width,
            "viewportHeight": height,
            "engine": "cli",
        }


async def html_to_screenshot(
    html: str,
    viewport: tuple[int, int] = DEFAULT_VIEWPORT,
    full_page: bool = False,
    engine: str | None = None,
    timeout: float | None = None,
) -> tuple[bytes, dict]:
    """把一段 HTML 渲染成 PNG 截图，供多模态模型走查。

    先用 Playwright 渲染（能等字体、能读尺寸、能截全页）；没装 Playwright
    或浏览器内核缺失时，自动回退到系统 Chrome / Edge 的命令行截图。

    Args:
        html: 待渲染的完整 HTML 文本（片段也可以，浏览器会自动补全）。
        viewport: 视口尺寸 (宽, 高)，默认 1280×720，与 16:9 课件画布一致。
        full_page: 是否截整页。排查溢出时建议 True，把超出视口的部分也截进来；
            默认 False，截图尺寸稳定、看图成本可控。
        engine: "auto" / "playwright" / "cli"；不传则读环境变量 HTML_CHECK_ENGINE。
        timeout: 渲染超时秒数，不传则读环境变量 HTML_CHECK_TIMEOUT（默认 60）。

    Returns:
        tuple[bytes, dict]: PNG 字节，以及页面尺寸信息
            （viewportWidth / viewportHeight / scrollWidth / scrollHeight / engine）；
            内容尺寸大于视口即说明页面有溢出。

    Raises:
        ValueError: html 为空，或 engine 取值不合法。
        RuntimeError: 两种引擎都不可用时，消息里会拼接各自失败的原因。
    """
    if not (html or "").strip():
        raise ValueError("html 不能为空")

    width, height = viewport
    engine_name = (engine or os.getenv("HTML_CHECK_ENGINE") or "auto").strip().lower()
    if engine_name not in {"auto", "playwright", "cli"}:
        raise ValueError(f"不支持的渲染引擎：{engine_name}；可选 auto / playwright / cli")
    render_timeout = float(timeout or os.getenv("HTML_CHECK_TIMEOUT") or DEFAULT_TIMEOUT)

    errors: list[str] = []

    if engine_name in {"auto", "playwright"}:
        try:
            return await asyncio.wait_for(
                _shot_with_playwright(html, width, height, full_page),
                timeout=render_timeout,
            )
        except ImportError as exc:
            errors.append(f"Playwright 不可用（{exc}）")
        except Exception as exc:  # 内核未安装、启动失败、渲染超时等
            errors.append(f"Playwright 渲染失败（{exc}）")
        if engine_name == "playwright":
            raise RuntimeError(
                "Playwright 渲染失败：" + "；".join(errors)
                + "；可执行 pip install playwright && playwright install chromium"
            )

    if engine_name in {"auto", "cli"}:
        browser_path = _find_browser()
        if browser_path:
            try:
                return await asyncio.to_thread(
                    _shot_with_cli_sync, html, browser_path, width, height, render_timeout
                )
            except Exception as exc:
                errors.append(f"命令行浏览器渲染失败（{exc}）")
        else:
            errors.append("未找到 Chrome / Edge 可执行文件（可用 HTML_CHECK_BROWSER 指定路径）")

    raise RuntimeError("HTML 渲染失败：" + "；".join(errors))


# ---------------------------------------------------------------------------
# 模型走查
# ---------------------------------------------------------------------------

def _review_user_prompt(html: str, metrics: dict, previous_issues: list[str]) -> str:
    """拼装走查请求：页面尺寸线索 + 上轮遗留问题 + HTML 源码（截图单独走图片消息）。"""
    parts: list[str] = []
    if metrics:
        parts.append(
            "页面尺寸信息：视口 {w}×{h}，文档内容 {sw}×{sh}（引擎：{engine}）。"
            "内容尺寸超出视口就说明有溢出，请对照截图确认有没有被裁切。".format(
                w=metrics.get("viewportWidth"),
                h=metrics.get("viewportHeight"),
                sw=metrics.get("scrollWidth", "未知"),
                sh=metrics.get("scrollHeight", "未知"),
                engine=metrics.get("engine", "unknown"),
            )
        )
    if previous_issues:
        listed = "\n".join(f"{i}. {issue}" for i, issue in enumerate(previous_issues, 1))
        parts.append(f"上一轮已指出的问题（请确认本轮是否修好，并且不要引入新的同类问题）：\n{listed}")
    parts.append(f"下面是这一页的 HTML 源码：\n```html\n{html}\n```")
    parts.append("截图见后，请按约定格式给出结论。")
    return "\n\n".join(parts)


def describe_model_config(env_path: Path | None = None) -> dict[str, str]:
    """返回当前生效的大模型配置（密钥打码），用于排查 401/404 这类配置问题。

    Args:
        env_path: 只用于在返回值里标明读取的是哪个 .env，方便确认改对了文件。

    Returns:
        dict[str, str]: ENV_FILE / API_BASE_URL / MODEL_NAME / API_KEY / OPENAI_API_KEY，
            其中密钥只保留前 6 位、后 4 位与总长度，不会把完整密钥打印出去。
    """

    def _mask(value: str | None) -> str:
        text = (value or "").strip()
        if not text:
            return "（空）"
        if len(text) <= 12:
            return f"***（长度 {len(text)}）"
        return f"{text[:6]}...{text[-4:]}（长度 {len(text)}）"

    return {
        "ENV_FILE": str(env_path or _ENV_PATH),
        "API_BASE_URL": os.getenv("API_BASE_URL") or "（空）",
        "MODEL_NAME": os.getenv("MODEL_NAME") or "（空）",
        "API_KEY": _mask(os.getenv("API_KEY")),
        # langchain-openai 在 api_key 为 None 时会回退读这个变量，鉴权失败时一并打出来对账
        "OPENAI_API_KEY": _mask(os.getenv("OPENAI_API_KEY")),
    }


def _explain_model_error(exc: Exception) -> str:
    """把底层异常翻译成能照着做的排查清单，避免只抛一句「调用失败」还得靠猜。"""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    config = describe_model_config()
    lines = [
        f"页面走查调用模型失败（{type(exc).__name__}，HTTP {status}）：{exc}",
        "当前生效配置："
        f"base_url={config['API_BASE_URL']}，model={config['MODEL_NAME']}，"
        f"api_key={config['API_KEY']}，openai_api_key={config['OPENAI_API_KEY']}，"
        f"env={config['ENV_FILE']}",
    ]
    if status in (401, 403):
        lines.append(
            "→ 鉴权失败：key 无效、过期，或与 API_BASE_URL 不是同一家网关。请确认这个 key 的归属与有效期；"
            "若 api_key 显示为（空），说明 .env 没读到，检查变量名/引号/BOM；"
            "若 api_key 与 .env 不一致，说明被系统环境变量或 OPENAI_API_KEY 顶替了。"
        )
    elif status == 404:
        lines.append("→ 接口或模型不存在：确认 API_BASE_URL 是否要带 /v1，MODEL_NAME 是否拼写正确。")
    elif status == 400:
        lines.append(
            "→ 请求被拒：常见原因是该模型不支持图片输入（非多模态），或 max_tokens 超过模型上限。"
        )
    elif status == 429:
        lines.append("→ 触发限流或额度不足：确认套餐余量，或降低调用频率。")
    return "\n".join(lines)


async def _review_once(
    html: str,
    png: bytes,
    metrics: dict,
    previous_issues: list[str],
    llm=None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> tuple[dict, str]:
    """把截图连同源码发给多模态模型，返回 (结论 JSON, 修正后的 HTML)。"""
    model = (llm or get_llm()).bind(temperature=0, max_tokens=max_tokens)
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    message = HumanMessage(
        content=[
            {"type": "text", "text": _review_user_prompt(html, metrics, previous_issues)},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
    )
    try:
        result = await model.ainvoke([SystemMessage(content=REVIEW_PROMPT), message])
    except Exception as exc:
        raise RuntimeError(_explain_model_error(exc)) from exc

    raw = _message_text(result.content).strip()
    marker_at = raw.find(HTML_MARKER)
    if marker_at == -1:
        verdict_text, fixed_text = raw, ""
    else:
        verdict_text = raw[:marker_at]
        fixed_text = raw[marker_at + len(HTML_MARKER) :]
    return _parse_json(verdict_text), _clean_html(fixed_text)


# ---------------------------------------------------------------------------
# 对外主函数
# ---------------------------------------------------------------------------

async def check_html_page(
    html: str,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    viewport: tuple[int, int] = DEFAULT_VIEWPORT,
    full_page: bool = False,
    output_dir: str | Path | None = None,
    keep_screenshots: bool = False,
    llm=None,
    engine: str | None = None,
) -> str:
    """渲染 HTML 交给模型走查，有问题就让模型返工，循环到「无明显问题」或返工次数用尽。

    每一轮的流程是：无头浏览器截图 → 多模态模型看图 + 看源码 → 判定是否有
    明显样式问题 → 有问题就同时给出修正后的完整 HTML → 用新 HTML 进入下一轮。

    临时文件自清理：截图默认只放在内存里，不落盘；即便传了 output_dir，
    只要 keep_screenshots 为 False，函数退出前（包括异常退出）也会把写出的
    roundN.png 逐个删除、并在目录变空时删掉目录。

    Args:
        html: 待检查的 HTML 文本，一般是上一节点生成好的整页文档。
        max_rounds: 最多返工几次，默认 3；判定本身不计入返工次数，
            因此对模型的最多调用次数是 max_rounds + 1。
        viewport: 截图视口 (宽, 高)，默认 1280×720。
        full_page: 是否截整页；默认 False，截视口范围。
        output_dir: 仅调试用；传入后会把每轮截图写成 round0.png / round1.png …，
            是否保留由 keep_screenshots 决定。
        keep_screenshots: 是否保留 output_dir 里的截图，默认 False（用完即删）。
            排查渲染效果时置 True，此时调用方自己负责清理。
        llm: 自定义模型客户端（需支持图片输入），不传则用 app.agent.agent.get_llm()。
        engine: 渲染引擎 auto / playwright / cli，不传则读环境变量 HTML_CHECK_ENGINE。

    Returns:
        str: 最终 HTML。可能是最后一轮修正后的版本；如果模型始终没给出可用修正，
            或已经没有明显问题，则返回当时手上的版本。

    Raises:
        ValueError: 传入的 html 为空。
        RuntimeError: 无法渲染截图（两种引擎均不可用），或模型调用失败。
    """
    if not (html or "").strip():
        raise ValueError("html 不能为空")

    max_tokens = int(os.getenv("HTML_CHECK_MAX_TOKENS") or DEFAULT_MAX_TOKENS)
    current = html.strip()
    revisions = 0
    previous_issues: list[str] = []
    written_files: list[Path] = []  # 登记本次落盘的截图，退出时统一清理

    try:
        while True:
            png, metrics = await html_to_screenshot(
                current, viewport=viewport, full_page=full_page, engine=engine
            )
            saved = _save_png(output_dir, f"round{revisions}.png", png)
            if saved is not None:
                written_files.append(saved)

            verdict, fixed = await _review_once(
                current, png, metrics, previous_issues, llm=llm, max_tokens=max_tokens
            )
            issues = _as_str_list(verdict.get("issues"))
            has_issue = verdict.get("hasIssue")

            if has_issue is False or (has_issue is None and not issues):
                print(f"[htmlCheck] 第 {revisions + 1} 轮走查通过，未发现明显样式问题")
                return current

            if not issues:  # 说有毛病却不说是什么，无法验证是否修好，直接收手避免死循环
                logger.warning("模型判定页面有问题但没有列出具体问题，保留当前版本")
                return current

            if revisions >= max_rounds:
                print(f"[htmlCheck] 已返工 {revisions} 轮仍有问题，返回最后一版")
                logger.warning("未修复的问题：%s", "；".join(issues))
                return current

            if not _is_usable_html(fixed):
                logger.warning("模型没有给出可用的修正 HTML（为空或不完整），保留当前版本")
                return current
            if fixed.strip() == current.strip():
                logger.warning("模型返回的 HTML 与原版一致，判定为无法通过返工解决，保留当前版本")
                return current

            print(f"[htmlCheck] 第 {revisions + 1} 轮发现问题，返工修正：{'；'.join(issues)}")
            current = fixed
            previous_issues = issues
            revisions += 1
    finally:
        # 正常返回、判停返回、抛异常三种出口都会走到这里，保证不残留截图
        if written_files and not keep_screenshots:
            _cleanup_screenshots(written_files)


if __name__ == "__main__":
    # 直接运行本文件可做一次端到端自测：故意做一页文字溢出的幻灯片
    DEMO_HTML = """<!DOCTYPE html>
    <html lang="zh-CN">
    <head><meta charset="UTF-8"><style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { display: flex; align-items: center; justify-content: center; min-height: 100vh;
             font-family: "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; }
      .slide { width: 1040px; height: 585px; background: #FEFAF3; border-radius: 12px; padding: 44px 56px;
               display: flex; flex-direction: column; }
      h1 { color: #E4620B; font-size: 34px; margin-bottom: -50px;}
      .body { flex: 1; display: grid; grid-template-columns: 1fr 1.25fr; gap: 24px; margin-top: 24px; overflow: hidden; }
      .hero { background: #FAEFE1; border-radius: 8px; display: flex; align-items: center; justify-content: center;
              color: #E4620B; font-size: 42px; font-weight: 700; }
      .card { background: #FFF9EA; border-radius: 6px; padding: 14px 20px; margin-bottom: 14px; }
      .card h3 { color: #E4620B; font-size: 18px; margin-bottom: 8px; }
      .card p { color: #4A4A4A; font-size: 15px; line-height: 1.75; }
      .footer { background: #EC6520; color: #fff; text-align: center; font-size: 20px; padding: 12px; margin-top: 24px; }
    </style></head>
    <body><div class="slide">
      <h1>什么是三国杀</h1>
      <div class="body">
        <div class="hero">三国杀</div>
        <div>
          <div class="card"><h3>身份与胜负</h3><p>每名玩家开局隐藏一个身份，主公与忠臣要清掉反贼和内奸，反贼要击杀主公，内奸要活到最后。这段文字故意写得非常长，长到一定会把卡片撑破并溢出到页面之外，用来验证走查能不能发现文字被裁切的问题。</p></div>
          <div class="card"><h3>回合流程</h3><p>判定、摸牌、出牌、弃牌四个阶段依次进行，摸牌阶段固定摸两张牌。</p></div>
        </div>
      </div>
      <div class="footer">理解规则，才能玩得明白</div>
    </div></body></html>"""

    async def _demo() -> None:
        # 先打印生效配置：401/404 这类问题基本都是配置没对上，比看堆栈快
        print("当前模型配置：")
        for name, value in describe_model_config().items():
            print(f"  {name} = {value}")
        print()

        # 默认不落盘；想人工核对每轮截图时，传 output_dir 且把 keep_screenshots 置 True
        final = await check_html_page(DEMO_HTML)
        print(f"最终 HTML：\n{final}")

    asyncio.run(_demo())
