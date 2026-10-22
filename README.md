# A-Share Insights

本地 A 股中文年报目录与文字 RAG 服务。FastAPI Swagger 支持公司搜索、公告版本、PDF 下载、索引、原文检索和带引用的问答。巨潮公开网页接口没有服务可用性保证。

## 1.1 启动

需要 Python 3.12、uv、Docker Desktop，以及已下载的本地 `BAAI/bge-large-zh-v1.5`。

1. 在项目根目录复制 `.env.example` 为 `.env`，设置随机数据库密码。执行：

   ```sh
   docker compose up -d db
   ```

2. 复制 `backend/.env.example` 为 `backend/.env`，将数据库URL里的密码改成同一个值。配置会自动读取 `backend/.env`，已有进程环境变量优先。

3. 安装项目依赖并迁移：

   ```sh
   cd backend
   uv sync --extra dev --extra eval
   uv run alembic upgrade head
   ```

4. 从1.0升级时，暂停旧服务，**仅导入一次**：

   ```sh
   uv run python -m ashare_insights.db.import_sqlite --source ../data/insights.db
   ```

   导入只读SQLite，逐表校验全部字段、保留ID和关联并重置序列；目标业务或RAG表非空则拒绝，失败事务回滚。原SQLite保留备份。正式运行和数据库测试仅使用PostgreSQL，不维护SQLite兼容模式。

5. 启动一个服务进程：

   ```sh
   uv run python -m ashare_insights.main
   ```

打开 <http://127.0.0.1:8000/docs>。FastAPI和数据库默认仅监听本机；不要使用多个uvicorn worker。Docker只运行数据库，模型与PDF在宿主机。`docker compose down`保留数据库卷，不要用`down -v`删除需要的数据。

## 本地模型与问答配置

- 模型默认位于 `data/models/bge-large-zh-v1.5`。可通过 `ASHARE_EMBEDDING_MODEL_PATH` 设置明确的本地路径，不会自动下载或执行远程模型代码。
- BGE Large生成1024维归一化向量，使用CPU、默认batch size 4、4个PyTorch线程。文档不加查询前缀，问题加中文检索前缀。超长输入报错，不静默截断。
- 正式依赖由本项目的 `backend/.venv` 和 `uv.lock` 管理，不依赖原学习项目或Docling实验环境。
- 问答设置 `ASHARE_LLM_BASE_URL`、`ASHARE_LLM_MODEL`、`ASHARE_LLM_API_KEY`；支持MiniMax的OpenAI兼容接口，模型名不写死。
- Ragas可单独设置 `ASHARE_EVAL_BASE_URL`、`ASHARE_EVAL_MODEL`、`ASHARE_EVAL_API_KEY`，留空时继承问答配置。
- 未配置LLM时仍可索引、检索、运行确定性评测。问答及Ragas会明确报告缺失配置，不会自动回退到OpenAI或远程Embedding。
- 问答和Ragas会发送问题、检索片段及参考答案给配置的远程模型；PDF解析与Embedding本地完成。

## Swagger 操作顺序

所有接口以 `/api/v1` 开头：

1. `GET /companies?q=600519` 搜索代码/名称，读取公司 `id`。
2. `GET /reports?company_id=<id>&years=2024` 查看报告所属年份的全部公告版本，读取所选报告 `id`。
3. `POST /reports/<id>/download` 创建/复用下载任务，通过 `GET /downloads/<task_id>` 查询。
4. 下载完成后，`GET /reports/<id>/file` 获取PDF。
5. `POST /reports/<id>/index` 提交 `{ "force": false }`，通过 `GET /index-tasks/<task_id>` 查看进度。
6. 索引完成后，`POST /reports/<id>/retrieve` 提交 `{ "question": "公司的主要业务是什么？", "top_k": 5 }`。
7. `POST /reports/<id>/ask` 提交 `{ "question": "公司的主要业务是什么？" }`，查看回答、原文摘录与引用。

`POST /companies/refresh` 和报告查询的 `refresh=true` 可刷新目录。公司目录缓存24小时，公告缓存15分钟；全部分页成功后才提交。失败有旧缓存则返回警告，没有则返回502。六位股票代码始终是字符串，公告按“来源+公告ID”去重，PDF按SHA-256去重，保留全部版本，不推断旧版失效。

## 索引与引用规则

- 使用pypdf提取文字；不做OCR、图片或表格结构识别。表格中的文字可能被提取，但行列关系没有保证。
- 按本地Large tokenizer切分，最多384 tokens、重叠64 tokens，优先句子/段落边界，允许跨页。对外页码只记录该块起始文字的PDF物理页，从1开始。
- 文件SHA、解析/切块版本、模型文件指纹、查询前缀、归一化配置共同决定索引身份。模型或配置改变需要重新索引，即使向量维度相同也不混用。
- 同一文件可被多个公告复用。检索使用指定文件、配置对应的有效代次做预过滤，再执行pgvector精确余弦排序，分数不是概率。
- 重复请求复用任务/有效索引；`force=true`建立新代次。全部块写入后才原子激活；失败保留旧索引。新请求可重试失败任务，历史失败保留。
- 索引为单进程单并发持久化队列，重启后清理中断任务的部分向量并重新排队。下载队列仍保持1.0行为：中断下载标记失败，可重新请求下载。
- 未下载、索引未就绪或配置不匹配返回409；公告不存在返回404；输入错误422；LLM/配置/引用校验失败503。
- 证据不足不等于年报不存在信息。回答只依据本次检索片段，引用ID与逐字摘录均需校验；该校验本身不能保证每个生成结论语义正确。

## 测试

先启动并迁移本地PostgreSQL，然后：

```sh
cd backend
uv run --extra dev pytest -q
```

数据库测试使用随机、显式隔离的PostgreSQL schema，退出后删除；不修改public业务表。测试使用固定响应和替身Embedding/LLM，不下载模型、不请求巨潮或MiniMax。SQLite只在旧库导入测试中作为只读来源。

真实Large索引、检索、问答和Ragas验收单独执行。1.0真实巨潮流程仍可在迁移后运行 `uv run python scripts/live_smoke.py`；它会查询三家公司并只下载前两份PDF。

## 20题召回评测与报告

本机2026-10-21验证：23项固定测试通过；真实茅台20题Hit@5由10/20提升至15/20，**尚未通过20/20召回验收**。两轮Ragas均完成20题。配置、失败项和局限见 [验证记录](docs/v1.1-validation.md)。

评测集为 `backend/evaluations/maotai-2024.json`：固定茅台2024年报SHA和解析版本、20道文字正例、原文位置、页码及参考答案；包含多证据组与等价证据，不绑定chunk ID。问题在真实检索调优前固定。

先通过API索引对应报告，再执行（报告ID从当前API读取，不硬编码数据库ID）：

```sh
cd backend
uv run python scripts/evaluate_rag.py --report-id <id>
# 服务已运行时复用其Large实例，避免评测再加载一份模型：
uv run python scripts/evaluate_rag.py --report-id <id> --api-url http://127.0.0.1:8000
# 增加真实Ragas辅助评分，需要配置评测模型与安装eval依赖：
uv run --extra eval python scripts/evaluate_rag.py --report-id <id> --ragas
```

每次在 `data/evaluations/<run_id>/` 新建目录，输出 `report.html`、`results.csv`、`results.json`。可用 `--output` 指定不存在的输出目录，保留初始和调整后的独立记录，不覆盖旧运行。

- Hit@1/5：至少一个返回块完整覆盖一处有效证据；MRR@5：首个这样的块的倒数排名。
- 证据Recall@5：前5块联合完整覆盖的必要证据组比例。同页/关键词命中不能替代证据覆盖；重复块不重复计分。
- 目标Hit@5为20/20且每题必要证据完整覆盖；未达到时保留失败项，不删题凑分。固定回归集成绩不代表未知问题泛化。
- Ragas使用 `LLMContextRecall`、`LLMContextPrecisionWithReference`，辅助判断语义；失败/缺失不记零，汇总给出有效数、缺失数与均值。Ragas结果不是人工确认。
- 评测入口关闭Ragas使用遥测；评分只调用显式配置的模型服务。
- 报告默认将人工复核标记为待完成，并保留实际排名、原文、页码、模型指纹、评测集版本、耗时和依赖版本。模型用量无法取得时为null，不虚构成本。

## 保留的表格解析实验

以下实验不进入1.1正式RAG链路：

```sh
uv run --script backend/scripts/table_experiment.py
```

六页、68项人工标注比较pypdf与pdfplumber，输出在 `data/table-experiment/REPORT.md`。Docling整页自动解析对照在 `data/docling-experiment/REPORT.md`；重跑步骤见 [实验说明](backend/scripts/README-table-experiment.md)。人工配置后的效果不代表通用自动解析能力。

PyMuPDF 2.0原生Markdown/JSON转换实验使用独立环境，覆盖同六页与两份完整年报；重跑与评分边界见 [PyMuPDF实验说明](backend/scripts/README-pymupdf-experiment.md)。运行产物在 `data/pymupdf-experiment/`，不替换现有RAG解析入口。

数据库、模型、PDF、日志、评测运行报告和本地配置不提交Git；源码、迁移、锁文件与固定评测标注可提交。`docs/v1.1-rag-plan.md`按用户要求仅保留本地。
