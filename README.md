# 城市基础设施定检报告智能体

这是面向“城市基础设施定检报告问答分析”赛题一的独立工程。旧版 InsightAI 工程不在本目录中，也不会被此项目修改。

## 公开仓库边界

本仓库公开可审查的源代码、脚本、测试、设计文档和运行说明。模型权重（约 15 GB）、解析缓存、原始/生成报告、临时渲染文件、本地环境和压缩提交包属于本机运行产物，按 `.gitignore` 排除，不应上传到公开 GitHub 仓库。运行时请按下文指定本地数据源和模型路径。

## 设计边界

- 核心推理模型为本地开源 `Qwen/Qwen3-8B`，默认 4-bit NF4、FP16 计算。
- 官方 ZIP 仅作为只读输入；原始文档、标签、模型权重和缓存不进入提交包。
- 测试预测流程只读取测试文档，不读取训练标签，不调用外部推理 API，也不写入原始数据。
- 模型不能确定的字段使用 `null` 或空数组；失败会保留 `blocked`/`failed` 状态，绝不伪造成功答案。
- 结果 DOCX 按输入文件 stem 原名写入 `result/`，表格顺序与官方“信息提取报告”标签一致：概要字段表、建议明细表、完整病害明细表。`predictions.v1.json` 是内部审计格式，不替代平台要求的官方 JSON；平台若要求单独上传 JSON，应使用运行生成的 `result/standard_predictions.json`。

## 安装与模型

使用 Python 3.10+ 创建环境后安装 `requirements.txt`。GPU 推理再按当前 CUDA 驱动安装匹配的 PyTorch，并安装 `requirements-model.txt`。模型目录通过 `CITY_AGENT_MODEL_PATH` 或 CLI `--model-path` 指定。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-model.txt
python scripts/download_model.py --target models/Qwen3-8B
```

在本机没有配置 `python` 命令时，可从任意 PowerShell 目录直接运行工程自带启动器。它会自动切换到工程目录，并依次使用 `.venv`、bundled Python 或 `py -3`。Windows PowerShell 5.1 使用纯 ASCII 兼容脚本，CMD 可直接调用 `.cmd`：

```powershell
& "E:\6000RMB\city_inspection_agent\run_predict.ps1" -Limit 1
& "E:\6000RMB\city_inspection_agent\run_predict.ps1" -Limit 1 -Resume
E:\6000RMB\city_inspection_agent\run_predict.cmd -Limit 1
```

确认 1 份报告成功后，再去掉 `-Limit 1` 运行完整测试集；中断后追加 `-Resume` 继续。`-Resume` 会跳过结构校验通过且已有结果 DOCX 的成功报告，只重试失败或无效记录。

默认推理预算面向 12 GB 显存设备，使用 4-bit NF4、有限输入上下文和 CPU offload。长报告仍可能耗时较久；逐份结果、预测 JSON 和审计日志都会原子写入，进程中断后可从已完成记录继续。

如果官方 ZIP 被移动但本地 `cache/reports_test.json` 仍存在，可将该解析缓存作为恢复输入传给 `--source`；它只包含已解析的原文片段，不包含训练标签或模型答案。

下载脚本只访问 ModelScope 的公开模型仓库，不接受或记录 API 密钥。

## CLI

```powershell
python scripts/prepare.py --source E:\6000RMB\赛题一城市基础设施定检报告问答分析.zip --split test
python scripts/evaluate.py --source E:\6000RMB\赛题一城市基础设施定检报告问答分析.zip --limit 3
python scripts/predict.py --source E:\6000RMB\赛题一城市基础设施定检报告问答分析.zip --split test
python scripts/package.py --output city_inspection_agent.tar.gz --result-dir result
streamlit run app.py
```

`--limit 3` 适合先做烟测；全量测试集应得到 92 条记录和 92 个结果 DOCX。没有模型权重或 CUDA 依赖时，预测文件仍会生成，但记录状态为 `blocked`，这是明确的环境状态而不是评测答案。

## 输出

`result/` 目录包含：

- 每份输入对应的结果 DOCX（与输入文件 stem 一致，不添加 `_结果` 后缀）；
- `predictions.v1.json`：结构化字段、病害、建议、证据来源和模型元数据；
- `standard_predictions.json`：平台上传用的版本化 JSON 适配层；
- `manifest.json`：输入数量、成功/阻塞/失败计数；
- `audit.jsonl`：逐条可追溯审计记录。

提交包只包含 `code/`、`design/`、`result/` 三个目录，且打包函数会拒绝超过 1 GB 的文件。打包时会排除旧版本带 `_结果` 后缀的 DOCX，避免同一输入出现重复结果。

## 测试

### 训练数据准备与离线诊断

在独立环境安装运行依赖后，可从任意目录运行以下入口。该步骤不加载模型，不上传数据，也不修改官方 ZIP：

```powershell
E:\6000RMB\city_inspection_agent\.venv\Scripts\python.exe E:\6000RMB\city_inspection_agent\scripts\prepare_training.py --source E:\6000RMB\赛题一城市基础设施定检报告问答分析.zip --parse-labels
```

输出保存在本地 `cache/training/`：原文与标签的一对一配对清单、已解析标签、按桥梁别名关联分组的训练/验证划分、标签内部一致性审计及准备摘要。只接受同年唯一文件名匹配，备用匹配还需同年唯一编号和桥名核对；歧义会明确报错。标签解析支持无标题行的概要表，并保留合并单元格、空单元格和文档顺序。成功解析的标签按源文件 CRC 和解析器版本断点复用。一致性检查只标记概要与详细结论数值或建议条数的冲突，不改写原始标签；标记不等于已经完成样本剔除或训练清洗。

离线评估只接受标记为 `split=train` 的预测，拒绝将测试结果与训练标签对比。概要严格按字段映射比较；病害按一对一行匹配，证据按原文来源 ID 和连续引用校验。`summary_character_f1`、`detailed_character_f1` 是本地字符级诊断指标，不是官方“文本一致性”或综合分数。失败与未匹配项保留在 `evaluation.json`；部分评估返回非零退出码。JSON/schema 合法不代表答案完整或正确。

桥梁分组使用规范化文件名及标签桥名的保守别名连接，同组不同年份不能跨训练/验证集。分组仍应审查：过度归组会减少独立组数，未知别名则需要后续核对。原始报告、标签、配对清单及训练产物均不进入公开仓库。

无需 pytest 也可运行核心测试：

```powershell
python -m unittest discover -s tests -v
```

测试覆盖 DOCX 段落/表格来源定位、中文 n-gram 检索、schema 转换、脱敏、JSON 校验和训练/测试分区隔离。`.doc` 解析使用 Windows Word COM；机器未安装 Word 时会返回明确错误。
