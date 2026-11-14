"""
Milvus 向量数据库工具集。
本模块只提供函数，导入时不产生任何副作用（不建集合、不写数据）。

对外可用函数：
    get_client()          创建 Milvus 客户端（按 MILVUS_URI / MILVUS_TOKEN 配置）
    create_collection()   创建集合并建立 dense 向量索引与 sparse BM25 索引
    insert_docs()         写入已算好向量的数据
    insert_texts()        只给原文即可入库，内部先做向量化再写入
    query_docs()          按主键或过滤条件查询数据，也可用于统计条数
    delete_docs()         按主键或过滤条件删除数据
    search_dense()        稠密向量检索，按语义相似度召回
    search_keyword()      关键词检索（BM25 全文检索），擅长专有名词与精确词
    hybrid_search()       混合检索，融合向量路与关键词路的结果
    get_embeddings()      获取 embedding 客户端（首次调用创建并缓存）
    embed_text()          单条文本向量化，可把用户问题转成查询向量
    embed_texts()         批量文本向量化

集合 rag_demo 的字段：
    id      主键（INT64，自增）
    text    原文（VARCHAR，中文分词）
    dense   稠密向量（由外部 embedding 模型生成）
    sparse  稀疏向量（BM25 函数在写入时自动生成）
环境变量（server/.env）：
    MILVUS_URI       Milvus 地址，默认 http://127.0.0.1:19530
    MILVUS_TOKEN     Zilliz Cloud 等托管服务需要的令牌，本地部署可不填
    VECTOR_API_KEY   阿里云百炼 API Key，向量化必需
    VECTOR_BASE_URL  向量化服务地址（可选）
    VECTOR_MODEL     向量化模型名（可选，默认 text-embedding-v4）
"""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from pymilvus import (
    AnnSearchRequest,
    DataType,
    Function,
    FunctionType,
    MilvusClient,
    RRFRanker,
    WeightedRanker,
)

# 显式指定 server/.env，否则以脚本方式运行时读不到配置
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

COLLECTION_NAME = "rag_demo"
# 稠密向量维度：必须与 embedding 模型的输出维度一致。百炼 text-embedding-v4 默认 1024 维
DIM = 1024

# 阿里云百炼的 OpenAI 兼容地址（国际站为 https://dashscope-intl.aliyuncs.com/compatible-mode/v1）
VECTOR_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# text-embedding-v4：默认 1024 维，也可选 2048/1536/768/512/256/128/64
VECTOR_MODEL = "text-embedding-v4"

# 稠密向量的相似度度量。用 COSINE 而不是 IP：
# 百炼输出的向量不是单位向量，IP 分数没有固定上下界，没法用 0.5 这种阈值卡；
# COSINE 是归一化后的夹角余弦，取值 -1~1，文本一般在 0~1，阈值才有可解释性
DENSE_METRIC = "COSINE"

# ---------------------------------------------------------------------------
# 基础：向量化（把问题 / 文本转成 dense 向量）
# ---------------------------------------------------------------------------

@lru_cache
def get_embeddings() -> OpenAIEmbeddings:
    """获取 embedding 客户端，首次调用时创建并缓存。

    百炼走 OpenAI 兼容协议，因此直接复用 langchain 的 OpenAIEmbeddings。

    Returns:
        OpenAIEmbeddings: 按 DIM、VECTOR_MODEL、VECTOR_BASE_URL 配置好的客户端。

    Raises:
        RuntimeError: 环境变量 VECTOR_API_KEY 未配置。
    """
    api_key = (os.getenv("VECTOR_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("未配置 VECTOR_API_KEY，请在 server/.env 中填写阿里云百炼 API Key")
    return OpenAIEmbeddings(
        model=os.getenv("VECTOR_MODEL", VECTOR_MODEL),
        api_key=api_key,
        base_url=os.getenv("VECTOR_BASE_URL", VECTOR_BASE_URL),
        # 维度必须与建集合时的 dim 一致，否则写入/检索都会报维度不匹配
        dimensions=DIM,
        # 非 OpenAI 官方模型，关掉 tiktoken 本地分词，把原文直接发给服务端
        check_embedding_ctx_length=False,
    )


def embed_text(text: str) -> list[float]:
    """把单条文本向量化，常用于把用户问题转成检索用的查询向量。

    Args:
        text: 待向量化的文本，前后空白会在内部去掉。

    Returns:
        list[float]: 长度为 DIM 的稠密向量，可直接传给 search_dense / hybrid_search。

    Raises:
        ValueError: text 为空或纯空白。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("待向量化的文本不能为空")
    return get_embeddings().embed_query(text)


def embed_texts(texts: list[str], batch_size: int = 10) -> list[list[float]]:
    """批量向量化文本。

    Args:
        texts: 待向量化的文本列表，空串与纯空白项会被丢弃。
        batch_size: 单次请求的文本条数上限，百炼限制为 10，超出会自动分批。

    Returns:
        list[list[float]]: 与清理后的输入一一对应的向量列表；输入全为空时返回 []。

    Note:
        因为空文本被过滤，返回列表长度可能小于传入长度，调用方需自行对齐下标。
    """
    cleaned = [t.strip() for t in (texts or []) if t and t.strip()]
    if not cleaned:
        return []
    return get_embeddings().embed_documents(cleaned, chunk_size=batch_size)


# ---------------------------------------------------------------------------
# 基础：连接与建集合
# ---------------------------------------------------------------------------

def get_client() -> MilvusClient:
    """创建 Milvus 客户端。

    客户端本身很轻，真正的连接建立发生在第一次请求时。

    Returns:
        MilvusClient: 按 MILVUS_URI / MILVUS_TOKEN 配置的客户端。
    """
    uri = os.getenv("MILVUS_URI", "http://127.0.0.1:19530")
    token = os.getenv("MILVUS_TOKEN") or None
    if token:
        return MilvusClient(uri=uri, token=token)
    return MilvusClient(uri=uri)


def _build_schema(dim: int = DIM):
    """构造集合结构：原文 + 稠密向量 + 由 BM25 函数自动生成的稀疏向量。"""
    schema = MilvusClient.create_schema(auto_id=True, enable_dynamic_field=True)
    schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
    schema.add_field(
        field_name="text",
        datatype=DataType.VARCHAR,
        max_length=65535,
        enable_analyzer=True,  # 必须开分词，BM25 依赖它
        analyzer_params={"type": "chinese"},
    )
    schema.add_field(field_name="dense", datatype=DataType.FLOAT_VECTOR, dim=dim)
    schema.add_field(field_name="sparse", datatype=DataType.SPARSE_FLOAT_VECTOR)

    schema.add_function(
        Function(
            name="bm25",
            function_type=FunctionType.BM25,
            input_field_names=["text"],
            output_field_names=["sparse"],
        )
    )
    return schema

def create_collection(
    client: MilvusClient,
    dim: int = DIM,
    drop_existing: bool = False,
    collection_name: str = COLLECTION_NAME,
) -> bool:
    """创建集合并建立索引（dense 向量索引 + sparse BM25 索引）。

    Args:
        client: Milvus 客户端，通常来自 get_client()。
        dim: 稠密向量维度，必须与 embedding 模型的输出维度一致。
        drop_existing: 集合已存在时是否先删除再重建；True 会丢弃旧数据，慎用。
        collection_name: 集合名，默认 rag_demo。

    Returns:
        bool: True 表示新建成功；False 表示集合已存在且未被改动。
    """
    if client.has_collection(collection_name):
        if not drop_existing:
            return False
        client.drop_collection(collection_name)

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="dense",
        index_type="IVF_FLAT",
        metric_type=DENSE_METRIC,
        params={"nlist": 128},
    )
    # BM25 函数产出的稀疏字段，metric_type 必须写 BM25（Milvus 2.5+ 的硬性要求）
    index_params.add_index(
        field_name="sparse",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="BM25",
    )

    client.create_collection(
        collection_name=collection_name,
        schema=_build_schema(dim),
        index_params=index_params,
    )
    return True

# ---------------------------------------------------------------------------
# 业务 1：写入
# ---------------------------------------------------------------------------

def insert_docs(
    client: MilvusClient,
    data: list[dict],
    collection_name: str = COLLECTION_NAME,
) -> int:
    """写入已算好向量的数据。

    Args:
        client: Milvus 客户端。
        data: 记录列表，每条形如 {"text": 原文, "dense": 向量}；
            sparse 由 BM25 函数自动生成，无需手写；dense 维度须等于建集合时的 dim。
        collection_name: 集合名，默认 rag_demo。

    Returns:
        int: 实际写入条数；data 为空时返回 0。
    """
    if not data:
        return 0
    res = client.insert(collection_name=collection_name, data=data)
    return res["insert_count"]

def insert_texts(
    client: MilvusClient,
    data: list[dict],
    batch_size: int = 10,
    collection_name: str = COLLECTION_NAME,
) -> int:
    """只提供原文即可入库：内部先用 embedding 模型把 text 算成 dense 向量，再写入。

    Args:
        client: Milvus 客户端。
        data: 记录列表，每条需含 "text" 字段；其他字段（如 metadata）原样保留。
        batch_size: 向量化时单次请求的条数上限，百炼限制为 10。
        collection_name: 集合名，默认 rag_demo。

    Returns:
        int: 实际写入条数；text 为空或纯空白的记录会被跳过，可能小于传入条数。

    Raises:
        ValueError: 模型输出的向量维度与集合的 dim 不一致。
    """
    docs = []
    for item in data or []:
        text = (item.get("text") or "").strip()
        if text:
            docs.append({**item, "text": text})
    if not docs:
        return 0

    vectors = embed_texts([doc["text"] for doc in docs], batch_size=batch_size)

    for doc, vector in zip(docs, vectors):
        if len(vector) != DIM:
            raise ValueError(
                f"向量维度不一致：模型输出 {len(vector)} 维，集合要求 {DIM} 维，"
                "请用 create_collection(client, drop_existing=True) 按 DIM 重建集合"
            )
        doc["dense"] = vector

    return insert_docs(client, docs, collection_name=collection_name)


# ---------------------------------------------------------------------------
# 业务 2：查询
# ---------------------------------------------------------------------------

def query_docs(
    client: MilvusClient,
    ids: list[int] | None = None,
    filter_expr: str = "",
    limit: int | None = 10,
    output_fields: tuple[str, ...] = ("id", "text"),
    collection_name: str = COLLECTION_NAME,
) -> list[dict]:
    """按主键或过滤条件查询数据。

    Args:
        client: Milvus 客户端。
        ids: 主键列表；传了就按主键批量取（等价于 get by pk），此时忽略 filter_expr 与 limit。
        filter_expr: 布尔过滤表达式，如 'id >= 0'、'text like "%向量%"'、'id in [1,2]'。
        limit: 返回条数上限；output_fields 为聚合表达式（如 count(*)）时忽略。
        output_fields: 需要返回的字段；传 ("count(*)",) 时直接返回统计条数。
        collection_name: 集合名，默认 rag_demo。

    Returns:
        list[dict]: 命中的记录列表，字段由 output_fields 决定。
    """
    if ids:
        return client.get(
            collection_name=collection_name,
            ids=list(ids),
            output_fields=[f for f in output_fields if not f.startswith("count(")],
        )

    kwargs: dict = {
        "collection_name": collection_name,
        "filter": filter_expr,
        "output_fields": list(output_fields),
    }
    # 聚合查询不需要 limit，带上反而可能报错
    if limit is not None and not any(f.startswith("count(") for f in output_fields):
        kwargs["limit"] = limit
    return client.query(**kwargs)

# ---------------------------------------------------------------------------
# 业务 3：删除
# ---------------------------------------------------------------------------

def delete_docs(
    client: MilvusClient,
    ids: list[int] | None = None,
    filter_expr: str | None = None,
    collection_name: str = COLLECTION_NAME,
) -> dict:
    """按主键或过滤条件删除数据，ids 与 filter_expr 二选一。

    Args:
        client: Milvus 客户端。
        ids: 主键列表，优先级高于 filter_expr。
        filter_expr: 布尔过滤表达式；与 ids 都不传时抛错，避免误删整个集合。
        collection_name: 集合名，默认 rag_demo。

    Returns:
        dict: Milvus 的结果字典，含删除条数 delete_count。

    Raises:
        ValueError: ids 与 filter_expr 都没有传。
    """
    if ids:
        return client.delete(collection_name=collection_name, ids=list(ids))

    if not (filter_expr or "").strip():
        raise ValueError("ids 和 filter_expr 至少传一个，否则可能删掉整个集合")
    return client.delete(collection_name=collection_name, filter=filter_expr)

# ---------------------------------------------------------------------------
# 业务 4~6：三种检索
# ---------------------------------------------------------------------------

def _format_hits(results) -> list[dict]:
    """把 search 的嵌套返回压平：[[{id, distance, entity}, ...]] → [{id, score, text}]。"""
    if not results:
        return []
    hits = []
    for hit in results[0]:
        entity = hit.get("entity") or {}
        hits.append(
            {
                "id": hit.get("id"),
                "score": hit.get("distance"),  # 余弦相似度 / BM25 / RRF 分数，越大越相关
                "text": entity.get("text"),
            }
        )
    return hits

def search_dense(
    client: MilvusClient,
    query_vector: list[float],
    limit: int = 5,
    min_score: float | None = None,
    collection_name: str = COLLECTION_NAME,
    output_fields: tuple[str, ...] = ("text",),
) -> list[dict]:
    """稠密向量检索（语义相似），走 dense 索引。

    Args:
        client: Milvus 客户端。
        query_vector: 查询向量，一般由 embed_text(问题) 得到。
        limit: 返回条数上限。
        min_score: 相似度下限（COSINE 取值 -1~1），只返回得分 >= 该值的结果，
            用来滤掉语义无关的「最近的 N 条」；不传则不做过滤。
        collection_name: 集合名，默认 rag_demo。
        output_fields: 除 id、score 外需要返回的字段，默认只取 text。

    Returns:
        list[dict]: 形如 [{"id": ..., "score": ..., "text": ...}]，score 越大越相关。
    """
    # params 里带 radius 就是 Milvus 的「范围检索」：小于 radius 的结果不进入候选集
    params: dict = {}
    if min_score is not None:
        params["radius"] = float(min_score)

    results = client.search(
        collection_name=collection_name,
        data=[list(query_vector)],
        anns_field="dense",
        search_params={"metric_type": DENSE_METRIC, "params": params},
        limit=limit,
        output_fields=list(output_fields),
    )
    return _format_hits(results)

def search_keyword(
    client: MilvusClient,
    query_text: str,
    limit: int = 5,
    collection_name: str = COLLECTION_NAME,
    output_fields: tuple[str, ...] = ("text",),
) -> list[dict]:
    """关键词检索（BM25 全文检索），走 sparse 索引。

    Milvus 会用集合里的同一个中文分词器把查询词转成稀疏向量，再与库中的 sparse 字段比对，
    本质是「带分词和 IDF 加权的词频匹配」，专有名词、精确词的命中比纯向量更靠谱。

    Args:
        client: Milvus 客户端。
        query_text: 查询原文，不需要自己算稀疏向量。
        limit: 返回条数上限。
        collection_name: 集合名，默认 rag_demo。
        output_fields: 除 id、score 外需要返回的字段，默认只取 text。

    Returns:
        list[dict]: 形如 [{"id": ..., "score": ..., "text": ...}]，score 为 BM25 分数，越大越相关。
    """
    # 没命中任何查询词的文档分数为 0，本身就不会返回，因此这里不做相似度门槛
    results = client.search(
        collection_name=collection_name,
        data=[query_text],
        anns_field="sparse",
        search_params={"metric_type": "BM25", "params": {}},
        limit=limit,
        output_fields=list(output_fields),
    )
    return _format_hits(results)

def hybrid_search(
    client: MilvusClient,
    query_vector: list[float],
    query_text: str,
    limit: int = 5,
    weights: tuple[float, float] | None = None,
    collection_name: str = COLLECTION_NAME,
    output_fields: tuple[str, ...] = ("text",),
) -> list[dict]:
    """混合检索：向量路与关键词路各取 limit 条候选，再由重排器融合出最终 TopN。

    Args:
        client: Milvus 客户端。
        query_vector: 查询向量，一般由 embed_text(问题) 得到，用于 dense 路。
        query_text: 查询原文，用于 sparse（BM25）路。
        limit: 每路候选条数，同时也是最终返回条数。
        weights: 加权融合的两路权重 (dense, sparse)，如 (0.7, 0.3)；
            不传则使用 RRF（倒数排名融合），只看名次不看原始分数，
            可避免余弦分与 BM25 分量纲不一致。
        collection_name: 集合名，默认 rag_demo。
        output_fields: 除 id、score 外需要返回的字段，默认只取 text。

    Returns:
        list[dict]: 融合后的 TopN，score 为 RRF 名次倒数或加权分，
            不等于相似度，只能用来排序；需要按相似度过滤时请改用 search_dense。
    """
    requests = [
        AnnSearchRequest(
            data=[list(query_vector)],
            anns_field="dense",
            param={"metric_type": DENSE_METRIC, "params": {}},
            limit=limit,
        ),
        AnnSearchRequest(
            data=[query_text],
            anns_field="sparse",
            param={"metric_type": "BM25", "params": {}},
            limit=limit,
        ),
    ]
    ranker = WeightedRanker(*weights) if weights else RRFRanker(60)

    results = client.hybrid_search(
        collection_name=collection_name,
        reqs=requests,
        ranker=ranker,
        limit=limit,
        output_fields=list(output_fields),
    )
    return _format_hits(results)

if __name__ == "__main__":
    milvus = get_client()
    print(query_docs(milvus))
