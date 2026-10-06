# 城市基础设施定检报告智能体

这是面向“城市基础设施定检报告问答分析”赛题一的独立工程。旧版 InsightAI 工程不在本目录中，也不会被此项目修改。

## 公开仓库边界

本仓库公开可审查的源代码、脚本、测试、设计文档和运行说明。模型权重（约 15 GB）、解析缓存、原始/生成报告、临时渲染文件、本地环境和压缩提交包属于本机运行产物，按 `.gitignore` 排除，不应上传到公开 GitHub 仓库。运行时请按下文指定本地数据源和模型路径。

## 设计边界

- 核心推理模型为本地开源 `Qwen/Qwen3-8B`，默认 4-bit NF4、FP16 计算。
- 官方 ZIP 仅作为只读输入；原始文档、标签、模型权重和缓存不进入提交包。
- 测试预测流程只读取测试文档，不读取训练标签，不调用外部推理 API，也不写入原始数据。
- 模型不能确定的字段使用 `null` 或空数组；失败会保留 `blocked`/`failed` 状态，绝不伪造成功答案。
- `predictions.v1.json` 是平台字段样例公开前的版本化适配格式，官方样例发布后只需替换 `city_agent/adapter.py`。

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
python scripts/package.py --output city_inspection_agent.tar.gz
streamlit run app.py
```

`--limit 3` 适合先做烟测；全量测试集应得到 92 条记录和 92 个结果 DOCX。没有模型权重或 CUDA 依赖时，预测文件仍会生成，但记录状态为 `blocked`，这是明确的环境状态而不是评测答案。

## 输出

`result/` 目录包含：

- 每份输入对应的结果 DOCX；
- `predictions.v1.json`：结构化字段、病害、建议、证据来源和模型元数据；
- `manifest.json`：输入数量、成功/阻塞/失败计数；
- `audit.jsonl`：逐条可追溯审计记录。

提交包只包含 `code/`、`design/`、`result/` 三个目录，且打包函数会拒绝超过 1 GB 的文件。

## 测试

无需 pytest 也可运行核心测试：

```powershell
python -m unittest discover -s tests -v
```

测试覆盖 DOCX 段落/表格来源定位、中文 n-gram 检索、schema 转换、脱敏、JSON 校验和训练/测试分区隔离。`.doc` 解析使用 Windows Word COM；机器未安装 Word 时会返回明确错误。
