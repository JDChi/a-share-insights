# 年报表格小样实验

仅对现有茅台、美的 2024 年报的六个物理页做离线实验，不下载文件，不调用模型，不写数据库。

## 重跑

从项目根目录运行：

```sh
uv run --script backend/scripts/table_experiment.py
```

脚本使用 PEP 723 独立依赖声明；首次由 uv 安装依赖时需要网络。PDF 解析过程本身不访问网络，不修改后端运行依赖。已有相同依赖的 Python 环境也可直接执行该脚本，实际版本会记录在结果中。

默认读取 `data/pdfs/` 中两份以 SHA-256 命名的 PDF；同时验证内容校验值，文件缺失或不匹配就停止。参数 `--pdf-dir`、`--output` 可覆盖输入/输出目录，`--fixture` 可指定另一份相同结构的样本配置。

## 输入与核验标准

- `table_samples.json`：六页、七个表格区域、68 项人工对照原页图像标注的单元格检查。列号从 0 开始，PDF 物理页码从 1 开始；重复行名支持 `occurrence` 指定出现序号。
- 固定裁剪区域对三种 pdfplumber 模式一致；`grid` 使用默认线框模式，`text` 使用文本行列模式，`tuned` 对无完整网格样本配置显式列边界和容差。
- 茅台63页拆为上方旧表结尾、下方新利润表两个区域；64页不补造表名、单位和年度。美的表头区分合并/公司口径、年度，以及经重列标记。
- 裁剪区域、列边界、完整列名、表名和单位均由人工对照这六页配置，**不是自动区域识别或自动表头理解的结果**。不把人工配置当作通用解析能力。
- 数值检查只忽略排版空白，保留括号、负号和逗号。`null` 表示无独立单元格（例如被合并覆盖），`""` 是提取为空的单元格，`"-"` 保留横线；三者不等价，也不改成零。
- 多行项目名和跨行附注不猜测拼接；此类失败会留在错误清单。缺少行或列时，即使期望值为 `null`，也不会误判通过。

## 输出

所有产物默认位于被 Git 忽略的 `data/table-experiment/`：

- `REPORT.md`：结论、各区域检查结果、全部失败项与输出链接。
- `results.json`：版本、输入校验值、配置校验值、区域参数、各模式的原始单元格与全部核验结果。
- `errors.json`：失败项、预期值、实际值与缺失/不一致原因。
- 每页目录：整页 PNG、pypdf 普通文字与保留布局文字、每个表格区域的三套 JSON/Markdown。

Markdown保留原始表头行供检查，并以人工完整列名显示目标形式。输出错误项是本实验的有效发现，不因此返回非零退出码；输入/程序异常仍会报错。此结果只覆盖固定小样，既不是完整表格正确率，也不是 RAG 检索评测。

## Docling 对照实验

Docling 使用独立环境 `data/.venv-docling/`。本机通过该环境的 `existing-ml-runtime.pth` 引用已有学习环境的 site-packages 来复用 PyTorch，其余新增依赖只安装到实验环境；原学习环境与后端环境未修改。移动/删除学习环境后需要在实验环境重新安装对应依赖。

从头准备独立环境可执行下列命令（会安装所需依赖；本机已有环境不必重复创建）：

```sh
python3.12 -m venv data/.venv-docling
data/.venv-docling/bin/python -m pip install -r backend/scripts/docling-experiment-requirements.txt
python3.12 backend/scripts/download_docling_models.py
```

模型下载器仅获取 Heron 和 TableFormer accurate 的必需文件，固定来源 revision，核对权重 SHA-256，并在 `data/models/docling/manifest.json` 记录全部文件校验值。首次安装和模型下载需要网络；不下载 OCR 或 VLM 权重。模型与虚拟环境均被 Git 忽略。

依次执行两种配置，避免同时加载两份模型：

```sh
data/.venv-docling/bin/python backend/scripts/docling_experiment.py --cell-matching true
data/.venv-docling/bin/python backend/scripts/docling_experiment.py --cell-matching false
python3.12 backend/scripts/report_docling_experiment.py
```

测试关闭 OCR、图片分类/描述、代码与公式增强，使用 CPU 四线程；加载本地模型后通过 socket 拦截禁止网络连接。两次运行均解析完整单页，**不传入人工表格区域、列边界或表头模板**。原标注 bbox 只在转换后，通过 IoU 匹配待核验的表格，不参与解析。使用与 pdfplumber 相同的 `table_checks.py` 严格核验68项标注。

原始跨度单元格只在起始行列写入文本，覆盖位置为 null。不自动修复漏字、拼接多行、继承跨页表头，也不将自动 Markdown 的完整程度等同于数字与口径正确。

结果位于 `data/docling-experiment/REPORT.md`，包括两种配置的原始 JSON/Markdown、全部失败项、逐页耗时、进程峰值RSS、实际包版本和模型来源。峰值内存为进程测量，不是模型文件大小，也不是系统总内存占用。
