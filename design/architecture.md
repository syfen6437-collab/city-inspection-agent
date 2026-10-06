# 技术方案

## 数据流

1. `document_parser` 读取 `.docx`；`.doc` 通过本机 Microsoft Word COM 转成临时 `.docx`，临时文件在解析后删除。
2. 每个段落和表格行获得稳定的 `source_id`，供证据链引用。
3. `index` 对中文字符建立 2/3/4-gram 倒排索引，并以稀有度加权排序。
4. `extractor` 把报告片段交给本地 Qwen3，要求严格 JSON；对报告中明确标注的病害表做 schema-aware 行解析以保留完整记录，模型负责概要、跨段语义和前次状态补充；所有值仍须来自报告原文。
5. `qa` 先检索证据，再把证据交给本地模型生成回答；回答只保留模型引用的有效证据。
6. `render` 生成与官方标签相同的概要字段表、建议明细表和完整病害明细表；证据 ID 和原文摘录保留在 JSON 审计结果中。
7. `adapter` 同时输出内部 `predictions.v1.json` 和不含审计元数据的 `standard_predictions.json`，便于接入平台字段。

## 训练与测试隔离

训练标签只由 `evaluation.py` 在离线评估命令中读取。`predict.py --split test` 的调用链不会遍历标签目录，也不会访问标准答案。确定性代码只做解析、校验、脱敏和格式化，不填入答案。

## 模型运行

默认配置为 `Qwen/Qwen3-8B`、4-bit NF4、FP16 计算、`device_map=auto`。模型路径、最大上下文和最大生成长度通过环境变量或 CLI 配置，不写死密钥。推理失败会记录状态和错误，不会降级为规则答案。
