# A-Share Insights

本地 A 股中文年报目录与 PDF 服务。通过 FastAPI 的 Swagger 页面搜索公司、查看年报及其各个公告版本，并按需下载 PDF。数据来自巨潮资讯公开网页接口；该接口并非有服务保障的开发者 API。

## 启动

需要 Python 3.12 和 uv。在项目根目录执行：

```bash
cd backend
uv sync --extra dev
uv run alembic upgrade head
uv run python -m ashare_insights.main
```

打开 <http://127.0.0.1:8000/docs>。默认只监听本机。可参考 `backend/.env.example` 设置 `ASHARE_DATA_DIR`、`ASHARE_HOST`、`ASHARE_PORT` 和 `ASHARE_REQUEST_TIMEOUT`；环境变量需在启动命令前导出，服务不会自动读取 `.env` 文件。

## Swagger 操作顺序

1. `GET /api/v1/companies?q=600519` 搜索股票代码或公司名称，从结果读取 `id`。首次搜索会读取巨潮 A 股公司目录。
2. `GET /api/v1/reports?company_id=<id>&years=2024` 查询报告年份为 2024 的全部中文年报全文公告，从结果选择 `id`。不传 `years` 时查询最近三个已结束年度。
3. `POST /api/v1/reports/<id>/download` 获取任务 `id`，再用 `GET /api/v1/downloads/<id>` 查询状态。
4. 状态为 `completed` 后，通过 `GET /api/v1/reports/<id>/file` 获取 PDF。

`POST /api/v1/companies/refresh` 和年报查询的 `refresh=true` 可强制刷新。公司目录缓存 24 小时，按公司与报告年份缓存公告列表 15 分钟。刷新失败时如有旧缓存，响应会附带警告和缓存时间；无缓存时返回 502。公告和 PDF 文件分别按来源公告 ID 与 SHA-256 去重。各版本均保留，服务不自动判断哪个版本取代另一个。

`cached_at` 和任务更新时间以 UTC 表示；年报公告日期按北京时间计算。

数据库与 PDF 存放于根目录 `data/`，不会提交 Git。首版只支持一个服务进程；下载队列在进程内，任务状态写入 SQLite。重启后排队任务会继续处理，运行中断的任务会标记失败，重新请求下载即可重试。

## 测试

```bash
cd backend
uv run pytest -q
```

自动测试使用从巨潮响应精简的固定样本，不访问网络。真实接口验收需在可连接巨潮的本机单独运行；固定样本通过不代表实时接口仍可用。

需要运行真实接口验收时，在迁移后执行 `uv run python scripts/live_smoke.py`。该脚本会通过 API 查询贵州茅台、美的集团、苏轴股份的 2024 年报，并仅下载前两家公司的 PDF 到本地 `data/`。
