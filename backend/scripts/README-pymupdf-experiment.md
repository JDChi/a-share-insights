# PyMuPDF 2.0 原生 Markdown 实验

仅实验，不修改现有 pypdf RAG 解析入口、索引、数据库或后端依赖。

## 环境与执行

在项目根目录运行：

```sh
uv venv --python backend/.venv/bin/python data/.venv-pymupdf
uv pip install --python data/.venv-pymupdf/bin/python -r backend/scripts/pymupdf-experiment-requirements.txt
data/.venv-pymupdf/bin/python -m unittest discover -s backend/scripts -p test_pymupdf_checks.py
data/.venv-pymupdf/bin/python backend/scripts/pymupdf_experiment.py --scope all
```

依赖已固定。本机是 Python 3.12/macOS ARM64；安装阶段需要网络。PyMuPDF 2.0 已包含原4LLM代码和本地ONNX版面资源；它依赖的 `pymupdf4llm==2.0` 是兼容空包，不是再安装另一套解析器。不依赖PyTorch或学习项目虚拟环境。

转换前阻止Python socket连接并关闭ONNX遥测；OCR和图片导出关闭，截图由原PDF单独渲染。不调用远程模型。资源文件的路径、大小和SHA保留在每组results.json中；这些是安装包内资源，不新增下载其它模型。

支持 `--scope samples|full|all`、`--mode layout|lines_strict|text|all`、`--pdf-dir`、`--output`。默认all：六页运行三种模式，完整年报运行layout和lines_strict；text不做全文运行。使用独立进程串行执行各组。输出目录必须不存在，避免覆盖旧结果。

## 输入与输出

输入为 `data/pdfs/` 中现有茅台、美的2024年报，按固定SHA校验，不下载PDF。

六页、七个区域、68项单元格检查复用原 `table_samples.json` 和 `table_checks.py`，不改标注。整页转换，不传入人工区域、列边界或表头；标注bbox只在转换后按IoU≥0.2匹配，匹配歧义单独记录。

默认产物位于 `data/pymupdf-experiment/<run_id>/`：

- `REPORT.md`、`summary.json`：对照结果、耗时和局限。
- 各组 `results.json`、`errors.json`：逐页状态、全部检查、版本和资源指纹。
- 每个来源的 `document.md`、`page-map.json`：按物理页顺序拼接的全文和Markdown字符位置。
- 逐页 `document.md`、`markdown-export.json`、`document.json`、`tables-from-markdown.json`、`native-tables.json`。
- 六页和各文档10个固定抽查页的原页截图；原页复核结论记录在 `REVIEW.md`。

只导出选定页面的原生JSON，文档级目录可能包含其它页的章节标题，不将它当作额外页面正文。规则模式的原生 `to_json` 不受支持，因此明确标记不可用，仍保留它实际返回的Markdown与元数据JSON；不会换用layout生成假对照。

## 评分与页码

主分数从实际Markdown解析出的行列核验；layout原生JSON单元格另行检查。绝不拿独立 `Page.find_tables()` 的结果证明Markdown正确。表格bbox优先来自导出page_boxes的原文位置；旧规则输出没有位置区间时，仅在表格数量一致时按页面阅读顺序对应，并记录对应方式。无法对应不猜测。

金额、负号、括号、逗号、横线、零和列位置按原标准检查；只忽略排版空白。Markdown没有表达合并跨度时，空白不自动还原成null。拆行、空列消失、合并覆盖与格式变化均保留，不修正输出来凑分。原生JSON中的单元格坐标与原始矩阵直接保存。

页码是原PDF物理页，从1开始。全文page-map的位置指向实际逐页Markdown；元素pos相对该页Markdown，不伪称为pypdf旧文本字符位置。全文拼接后逐页核对切片完全一致。API不作变更。

全文诊断统计页数、页序、空输出、替换字符、重复页面、标题、表格数及与pypdf的字符保留差异。字符多重集诊断只用于筛查，不证明内容正确、顺序正确或数值归属正确。不做跨页表格合并，不继承缺失表头或单位。

## 对照和重建报告

存在既有 `data/table-experiment/` 和 `data/docling-experiment/` 时，报告读取其结果，先核对标注SHA。缺失基线显示未运行，不将缺失记零。

```sh
data/.venv-pymupdf/bin/python backend/scripts/report_pymupdf_experiment.py data/pymupdf-experiment/<run_id>
```

pdfplumber基线含人工区域和列线，自动化程度不同。68项通过数只覆盖固定小样；完整转换不等于全部表格正确，也不代表RAG召回改善。脚本与依赖可以提交，PDF、环境、MD、截图和运行报告都被Git忽略；本轮不自动提交。

## 本机2026-10-21结果

- 自动版面：实际Markdown与原生JSON均58/68；规则lines_strict为21/68，text为27/68。七个目标表格的MD与JSON逐格比较仅忽略空白后无差异，但两者仍有同样的结构错误。
- 两份PDF的layout及lines_strict全文转换均完成，分别覆盖143页、295页，无失败、空输出或替换字符页；页码与Markdown切片一致。
- layout茅台墙钟42.29秒、峰值592.9 MiB；美的64.73秒、577.1 MiB。墙钟包含两次独立MD/JSON转换及核验，不能当作单次MD转换耗时。
- 20个固定物理页已做开发抽查；失败包括茅台表尾空附注列消失、美的资产总计行脱离表格、长行名及附注拆行，还有标题误判。Markdown不能自动修复这些问题。
- 本次报告：`data/pymupdf-experiment/native-2.0/REPORT.md`；逐页开发复核：同目录`REVIEW.md`。实验可作为后续候选，不直接替换现有RAG解析器。
