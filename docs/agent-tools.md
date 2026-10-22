# Agent 年报与财务工具

## 接口和工具

`POST /api/v1/reports/{report_id}/agent/ask`，请求只有 `question`（1至2000字符）。Swagger 展示返回结构。

- `query_annual_report(question)`：调用现有纯RAG问答，返回 `answered/insufficient_evidence/error`、原文引用和检索上下文。证据不足不等于整份年报没有披露。
- `get_financial_data(year, metrics)`：公司由年报绑定，年份未指定时由提示词引导采用年报年份。若旧北交所代码在目录中存在相同非空巨潮orgId、唯一的920代码，则使用该代码查询，并在结果保留bound_company_code及代码变更提示；不按名称或数字猜测映射。只提供年度合并数据，不支持跨公司、季度、母公司、预测和业务明细。

指标：`revenue`（营业收入）、`net_profit`（净利润）、`net_profit_parent`（归母净利润）、`total_assets`、`total_liabilities`、`total_equity`、`operating_cash_flow`、`investing_cash_flow`、`financing_cash_flow`。营业总收入不替代营业收入；净利润不替代归母净利润。

财务Provider协议为 `fetch(source, code, statement) -> list[dict]`。AKShare 1.19.1工作进程获取原始响应；服务层按公司、年度和字段规范化，东方财富优先，缺失或错误时尝试新浪。源码不修改全局代理/requests，不设置无限网络等待。单次AKShare调用在子进程中执行，超时终止；一次查询最多涉及三张报表及两个来源。

财务工具状态：`ok/partial/no_data/unsupported/error`。缺失值不是零；返回元值字符串、预先计算的display_amount亿元约数、币种CNY、合并口径、来源字段、来源地址和抓取时间。来源公告日期/更新日期原样保留，不等同于PDF发布日期。不同来源或PDF版本可能有重述差异，回答应明确来源，不静默视为同版年报。

Agent回答状态：`answered/partial/insufficient_evidence/error`。`sources`区分PDF和API；`tool_calls`记录名称、实参、状态、起止时间、耗时与工具返回，不暴露模型内部思考或凭据。PDF引用先由原RAG校验，再按source_id引用；不存在的来源或没有证据的成功答案会被拒绝。API来源回答逐一检查所有明确金额及单位，允许四舍五入与“净流出”表述；不允许只出现一个正确金额却夹带错误换算。引用存在不证明其他结论语义正确，仍须评测。

## 提示词与边界

唯一提示词在 `services/agent.py` 的 `PROMPT`。建议先RAG，再判断是否补查财务API；实际工具选择由模型作出。程序不自动触发API、不强制调用顺序，实际模型可能违反提示词，报告必须保留这种失败。

`ReActAgent`不依赖原生function calling；复用`ASHARE_LLM_*`，本轮不新增提供商凭据。每次请求独立上下文；以下环境变量见 `backend/.env.example`：

- `ASHARE_FINANCIAL_TIMEOUT=30`：每个上游整次调用秒数，包括内部分页，子进程超时终止。
- `ASHARE_FINANCIAL_CACHE_TTL=86400`：成功历史响应缓存秒数；目录`data/financial-cache/`。空结果和错误不缓存，不用过期缓存掩盖错误。
- `ASHARE_AGENT_TIMEOUT=240`：问答总时限。
- `ASHARE_AGENT_MAX_ITERATIONS=6`、`ASHARE_AGENT_MAX_TOOL_CALLS=4`：模型迭代和工具执行上限。

缓存包含历史年度，所以查其他年度可能命中同一报表缓存；返回的`cached`、`fetched_at`反映事实。取消问答后已经启动的同步线程可能在其有限超时内结束，不再用于生成答案。

## 重跑

从项目根目录执行（先启动本机PostgreSQL并在本地配置模型）。`ASHARE_DATA_DIR`使用项目data的绝对路径，避免示例中的`../data`随工作目录改变：

```sh
export ASHARE_DATA_DIR="$PWD/data"
uv sync --project backend --extra dev --extra eval
PYTHONPATH=backend/src backend/.venv/bin/python -m uvicorn ashare_insights.main:app --host 127.0.0.1 --port 8001
```

另一个终端：

```sh
export ASHARE_DATA_DIR="$PWD/data"
PYTHONPATH=backend/src backend/.venv/bin/python -m pytest backend/tests -q
PYTHONPATH=backend/src backend/.venv/bin/python backend/scripts/verify_financial_tools.py --output data/financial-validation/new-run
PYTHONPATH=backend/src backend/.venv/bin/python backend/scripts/evaluate_agent.py --report-id 1 --output data/agent-evaluations/new-run
```

输出目录必须不存在，避免覆盖历史报告。真实接口验证查询沪深北各5家公司2024年度九项指标；不下载新PDF。

## 评测定义

独立数据集`backend/evaluations/agent-maotai-2024.json`：8个RAG正例、8个2018年度财务补查题、4个无数据/不支持问题。问题在真实Agent运行之前冻结；财务参考值由独立财务调用冻结，不从Agent答案反推。原RAG20题不改。

固定测试使用真实裁剪的15家公司响应、虚拟故障以及脚本化模型响应，验证代码和ReAct协议；它不证明提示词可靠。真实评测仅提交问题给Agent接口，不替Agent调工具、不隐藏RAG内容。

报告保留实际顺序、RAG状态、API实参、来源、金额单位、答案及耗时。金额检查按显示精度允许四舍五入，不接受亿元/万元混淆。RAG正例同时检查既有固定原文跨度是否被召回。无数据题中服务错误不能记为成功拒答。自动通过表示流程与证据检查通过，最终语义人工审阅另记，不将Agent成功率当作RAG召回率。

## 本机验证

版本、三市场覆盖、调试失败记录和真实20题结果见 [验证记录](agent-validation.md)。
