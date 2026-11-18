"""资源文本化与切分：把各类文件转成纯文本，再递归切分成适合向量化入库的块。

对外可用函数：
    resource_to_text()  把资源文件转换为纯文本（纯文本直接读，其余交给 MinerU 解析）
    split_text()        按「段落 > 换行符 > 句号」递归切分文本，并给每块附加 source / split_time 元数据
    ingest_file()       一条龙：解析文件 → 切分 → 向量化 → 写入 Milvus 集合（默认 threeKill）

MinerU 走「精准解析 API」：申请上传链接 → PUT 上传 → 轮询批量结果 → 下载 zip 取 full.md。
环境变量（server/.env）：
    MINERU_API_KEY        必填，MinerU API Token
    MINERU_API_BASE       可选，默认 https://mineru.net/api/v4
    MINERU_MODEL_VERSION  可选，pipeline / vlm / MinerU-HTML，默认 vlm（推荐）
"""

import io
import os
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

# 显式指定 server/.env，否则以脚本方式运行时读不到配置
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

try:  # 以模块形式运行（python -m app.tools.textToVector，或被 app 内其他模块导入）
    from app.tools.milvus import create_collection, get_client, insert_texts
except ImportError:  # 直接运行 python app/tools/textToVector.py 时，同目录模块直接导入
    from milvus import create_collection, get_client, insert_texts

# 递归切分的分隔符优先级：段落(空行) > 换行符 > 句号；
# "" 是兜底项，处理超长且没有任何标点的段落，按字符硬切以保证不超过 chunk_size
DEFAULT_SEPARATORS = ["\n\n", "\n", "。", ""]

# 入库目标集合（Milvus collection）名，对应 milvus.create_collection / insert_texts 的 collection_name
COLLECTION_NAME = "threeKill"

# 可直接读取的纯文本类后缀，没必要上传解析
PLAIN_TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
    ".json", ".jsonl", ".xml", ".yaml", ".yml", ".ini", ".conf",
    ".toml", ".sql", ".py", ".js", ".ts",
}

# MinerU 支持解析的类型：PDF、图片、Office 文档、HTML
MINERU_SUFFIXES = {
    ".pdf",
    ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp",
    ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
    ".html", ".htm",
}

MINERU_API_BASE = os.getenv("MINERU_API_BASE", "https://mineru.net/api/v4")
MINERU_MODEL_VERSION = os.getenv("MINERU_MODEL_VERSION", "vlm")
# 轮询间隔与最长等待时间（秒）；大文档解析通常几分钟内完成
MINERU_POLL_INTERVAL = 3.0
MINERU_MAX_WAIT = 600.0


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """清洗提取结果：去掉 BOM 与零宽字符、行尾空白，并把连续空行压成一个空行。

    只处理行尾空白、不碰行首缩进，避免破坏 MinerU 输出的 Markdown 列表与代码块。
    """
    text = text.replace("\ufeff", "").replace("\u200b", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _read_plain_text(path: Path, encoding: str | None = None) -> str:
    """读取纯文本文件；未指定编码时依次尝试 utf-8-sig、gbk，最后带容错读取。"""
    if encoding:
        return path.read_text(encoding=encoding)
    for enc in ("utf-8-sig", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _mineru_headers() -> dict[str, str]:
    """构造 MinerU 请求头，Token 缺失时直接报错，避免发出必然失败的请求。"""
    api_key = (os.getenv("MINERU_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("未配置 MINERU_API_KEY，请在 server/.env 中填写 MinerU API Token")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "*/*",
    }


def _mineru_data(response: httpx.Response) -> dict:
    """校验 MinerU 的响应并取出 data 部分，HTTP 错误或业务错误码统一转成异常。"""
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"MinerU 返回非 JSON 响应（HTTP {response.status_code}）") from exc
    if response.status_code >= 400 or payload.get("code") != 0:
        raise RuntimeError(f"MinerU 接口错误：{payload.get('msg') or response.status_code}")
    return payload.get("data") or {}


def _mineru_wait_result(client: httpx.Client, batch_id: str) -> str:
    """轮询批量解析结果，返回结果压缩包的下载地址。"""
    endpoint = f"{MINERU_API_BASE}/extract-results/batch/{batch_id}"
    deadline = time.monotonic() + MINERU_MAX_WAIT
    while time.monotonic() < deadline:
        data = _mineru_data(client.get(endpoint, headers=_mineru_headers()))
        for item in data.get("extract_result") or []:
            state = item.get("state")
            if state == "done":
                zip_url = item.get("full_zip_url")
                if not zip_url:
                    raise RuntimeError("MinerU 解析完成但没有返回结果下载地址")
                return zip_url
            if state == "failed":
                raise RuntimeError(f"MinerU 解析失败：{item.get('err_msg') or '未返回失败原因'}")
        time.sleep(MINERU_POLL_INTERVAL)
    raise TimeoutError(f"MinerU 解析超时（超过 {MINERU_MAX_WAIT:.0f} 秒）")


def _read_markdown_from_zip(content: bytes, file_name: str) -> str:
    """从 MinerU 的结果压缩包里取出 Markdown 正文。"""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        target = next((n for n in names if n == "full.md" or n.endswith("/full.md")), None)
        if target is None:  # 兜底：任意 Markdown 文件
            target = next((n for n in names if n.lower().endswith(".md")), None)
        if target is None:
            raise RuntimeError(f"MinerU 结果压缩包中没有 Markdown 文件：{file_name}")
        return archive.read(target).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# MinerU 解析（内部实现，外部统一走 resource_to_text）
# ---------------------------------------------------------------------------

def _mineru_to_markdown(
    path: Path,
    model_version: str | None = None,
) -> str:
    """调用 MinerU 云端解析把文档转成 Markdown。

    流程为「申请上传链接 → 上传文件 → 轮询结果 → 下载 zip 并取 full.md」，
    上传完成后 MinerU 会自动提交解析任务，无需再调提交接口。
    """
    default_version = "MinerU-HTML" if path.suffix.lower() in {".html", ".htm"} else MINERU_MODEL_VERSION
    version = model_version or default_version

    with httpx.Client(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        # 1. 申请上传链接，同时得到批次号
        data = _mineru_data(
            client.post(
                f"{MINERU_API_BASE}/file-urls/batch",
                headers=_mineru_headers(),
                json={"files": [{"name": path.name}], "model_version": version},
            )
        )
        batch_id = data.get("batch_id")
        upload_urls = data.get("file_urls") or []
        if not batch_id or not upload_urls:
            raise RuntimeError(f"MinerU 未返回上传地址：{data}")

        # 2. 上传文件（此处不带 JSON 请求头，PUT 不需要 Content-Type）
        upload = client.put(upload_urls[0], content=path.read_bytes())
        if upload.status_code >= 400:
            raise RuntimeError(f"上传文件到 MinerU 失败（HTTP {upload.status_code}）")

        # 3. 轮询直到解析完成，拿到结果包地址
        zip_url = _mineru_wait_result(client, batch_id)

        # 4. 下载结果包并读取 Markdown
        archive = client.get(zip_url)
        if archive.status_code >= 400:
            raise RuntimeError(f"下载 MinerU 解析结果失败（HTTP {archive.status_code}）")
        return _normalize(_read_markdown_from_zip(archive.content, path.name))


# ---------------------------------------------------------------------------
# 对外函数
# ---------------------------------------------------------------------------

def resource_to_text(
    source: str | Path,
    encoding: str | None = None,
    model_version: str | None = None,
) -> str:
    """把各种资源文件转换为纯文本，供后续切分、向量化入库使用。

    纯文本类（.txt/.md/.csv/.json/.py 等）本地直接读取；
    PDF、图片、Word、PPT、Excel、HTML 交给 MinerU 云端解析并转成 Markdown。

    Args:
        source: 资源文件路径。
        encoding: 纯文本类文件的编码；不传则依次尝试 utf-8-sig、gbk。
        model_version: 传给 MinerU 的解析模型 pipeline / vlm / MinerU-HTML，
            不传则用环境变量 MINERU_MODEL_VERSION（默认 vlm，HTML 自动改用 MinerU-HTML）。

    Returns:
        str: 提取到的纯文本；走 MinerU 时保留 Markdown 结构（标题、表格、列表）。

    Raises:
        FileNotFoundError: 文件不存在，或路径指向目录。
        ValueError: 后缀不在支持范围内。
        RuntimeError: MinerU 未配置或解析失败。
        TimeoutError: MinerU 解析超时。
    """
    path = Path(source).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"资源文件不存在：{path}")

    suffix = path.suffix.lower()
    if suffix in PLAIN_TEXT_SUFFIXES:
        print(f"当前为纯文本：{path}")
        return _normalize(_read_plain_text(path, encoding))
    if suffix in MINERU_SUFFIXES:
        print(f"当前为文件：{path}")
        return _mineru_to_markdown(path, model_version=model_version)

    supported = ", ".join(sorted(PLAIN_TEXT_SUFFIXES | MINERU_SUFFIXES))
    raise ValueError(f"不支持的文件类型：{suffix or '(无后缀)'}；已支持：{supported}")


def split_text(
    text: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    separators: list[str] | None = None,
    source: str | Path | None = None,
) -> list[Document]:
    """把长文本递归切分成块，并附加元数据，供向量化并写入向量库。

    切分优先级为「段落（空行）> 换行符 > 句号」：先用空行切段落，
    某段仍超过 chunk_size 时才降级用换行切，再不行就用句号切，
    最后对没有任何分隔符的超长片段按字符兜底切分，保证不超过 chunk_size。

    Args:
        text: 待切分的纯文本，通常来自 resource_to_text()。
        chunk_size: 单块最大字符数。
        chunk_overlap: 相邻块的重叠字符数，用于避免分块边界处丢失上下文，必须小于 chunk_size。
        separators: 自定义分隔符优先级列表；不传则使用默认的「段落 > 换行 > 句号」。
        source: 文本出处，一般传资源文件路径，写入每个块的 metadata["source"]；
            相对路径会转成绝对路径，不传则记为 "unknown"。

    Returns:
        list[Document]: 切分结果，page_content 为块文本（已去掉首尾空白、过滤空块），
            metadata 目前包含 source（文本出处）与 split_time（本次切分时间，ISO 8601 带时区）；
            输入为空时返回 []。

    Raises:
        ValueError: chunk_size 不是正数，或 chunk_overlap 大于等于 chunk_size。
    """
    text = (text or "").strip()
    if not text:
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须是正整数")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap 必须小于 chunk_size，否则切分无法推进")

    # 末尾补 "" 兜底，自定义分隔符时同样避免超长片段
    separator_list = list(separators or DEFAULT_SEPARATORS)
    if separator_list[-1] != "":
        separator_list.append("")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=separator_list,
        keep_separator=True,  # 保留段落换行与句号，块内容更完整
        length_function=len,
    )
    # 同一次切分共用一个时间戳，便于按批次归组与后续增量更新
    split_time = datetime.now().astimezone().isoformat(timespec="seconds")
    if source is None:
        source_label = "unknown"
    else:
        source_path = Path(source).expanduser()
        source_label = str(source_path if source_path.is_absolute() else source_path.resolve())

    metadata = {"source": source_label, "split_time": split_time}
    return [
        Document(page_content=chunk.strip(), metadata=dict(metadata))
        for chunk in splitter.split_text(text)
        if chunk.strip()
    ]


def ingest_file(
    source: str | Path,
    collection_name: str = COLLECTION_NAME,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    recreate: bool = False,
) -> dict:
    """一条龙入库：资源文件 → 纯文本 → 切分块 → 向量化 → 写入 Milvus 集合。

    集合不存在时自动按 milvus._build_schema 创建（dense 向量索引 + BM25 稀疏索引），
    每块以 {"page_content": 正文, "source": 出处, "split_time": 切分时间} 的形式写入，
    后两者走集合的 dynamic field，可直接用于过滤与溯源。

    Args:
        source: 资源文件路径，支持 textToVector 里声明的全部后缀。
        collection_name: 目标集合名，默认 threeKill。
        chunk_size: 单块最大字符数，默认 500。
        chunk_overlap: 相邻块重叠字符数，默认 50。
        recreate: 是否先删集合再重建；改了字段结构（如 text → page_content）或想全量重灌时置 True，
            注意会丢弃集合里的旧数据。

    Returns:
        dict: {"source": 出处, "collection": 集合名, "chars": 文本字符数,
            "chunks": 切分块数, "inserted": 实际写入条数}。

    Raises:
        FileNotFoundError: 资源文件不存在。
        ValueError: 后缀不支持，或向量维度与集合的 dim 不一致。
        RuntimeError: MinerU 未配置 / 解析失败，或 VECTOR_API_KEY 未配置。
    """
    path = Path(source).expanduser()
    text = resource_to_text(path)
    chunks = split_text(
        text,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        source=path,
    )
    result = {
        "source": str(path),
        "collection": collection_name,
        "chars": len(text),
        "chunks": len(chunks),
        "inserted": 0,
    }
    if not chunks:
        return result

    client = get_client()
    if recreate:
        create_collection(client, drop_existing=True, collection_name=collection_name)
    elif not client.has_collection(collection_name):
        create_collection(client, collection_name=collection_name)

    # page_content 为正文；source / split_time 作为动态字段一并写入，便于按出处过滤与溯源
    data = [{**chunk.metadata, "page_content": chunk.page_content} for chunk in chunks]
    result["inserted"] = insert_texts(client, data, collection_name=collection_name)
    return result


if __name__ == "__main__":
    result = ingest_file(
        r'D:\renfu-mycode\product\MAIC\server\resource\三国杀玩法.txt',
        recreate=True,  # 首次入库或改过集合字段结构时置 True（会清空重建）；日常增量置 False
    )
    print(
        f"来源：{result['source']}\n"
        f"集合：{result['collection']}\n"
        f"字符数：{result['chars']}，切分块数：{result['chunks']}，写入条数：{result['inserted']}"
    )
