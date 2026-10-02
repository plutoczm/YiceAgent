# 模型微调子系统

针对大数据领域通用大模型专业能力弱、SQL 幻觉高的痛点，构建数据工程 SFT 指令数据集，
基于 Unsloth 对 Qwen3-8B-Instruct 开展 4-bit QLoRA 参数高效微调，产出行业专属 LoRA
适配器，合并导出 GGUF 后经 Ollama 私有化部署，并通过多 LLM 智能路由接入平台。

## 目录

```text
training/
  dataset_builder/   数据集构建（Spider Text2SQL + SQL 审查 + 数仓设计 + 知识问答）
  config/            train.yaml / eval.yaml 配置
  train/             Unsloth QLoRA 训练脚本
  eval/              基座 vs 微调对比评测框架
  export/            LoRA 合并、GGUF 导出、Modelfile、Ollama 导入
  scripts/           WSL2 一次性环境准备脚本
  data/              产物目录（gitignore：raw / processed / models / eval）
```

## 环境

- 训练环境复用现有 conda **`agent`** 虚拟环境（Windows 下训练在 **WSL2** 中执行，
  原生 Windows 的 Unsloth/bitsandbytes 支持有限）。
- 硬件：RTX 5060 Ti（**8G 显存**，Blackwell sm_120，需 CUDA 12.8+ / 驱动 570+）单卡。
  8G 显存建议用 **Qwen3-4B** 训练（8B 会因显存占满而病态变慢）；需要 8B 时请改用更大显存机器。

```bash
# WSL2 (Ubuntu 22.04/24.04) 一次性准备
bash training/scripts/prepare_wsl2.sh
```

## 1. 构建数据集

```bash
python -m training.dataset_builder.build --min-total 3200
```

- 主数据源为公开数据集 **Spider**（HuggingFace `xlang/spider`，train/dev 库分离，
  训练与评测天然无泄漏）；SQL 审查由项目内确定性规则引擎自动标注；数仓设计由模板设计
  确定性生成 gold；知识问答取自 `knowledge_base/` 42 篇文档。
- 产物：`training/data/processed/{train,eval}.jsonl`（ShareGPT 格式）与 `stats.json`。
- 总量不足 `--min-total` 时构建失败，避免产出不完整数据集。

## 2. QLoRA 微调

```bash
python -m training.train.train_unsloth --config training/config/train.yaml
```

- 仅训练 LoRA 低秩参数（r=32, alpha=64），4-bit nf4 量化 + bf16。
- 梯度检查点 + 梯度累积（batch 2 × 累积 4）控制 8G 显存。
- Qwen3 思考模式关闭（`enable_thinking=False`），与线上推理行为一致。
- 产物：`training/data/models/lora-datacopilot/`。

## 3. 对比评测

```bash
python -m training.eval.evaluate --runner base   # transformers 离线推理评测基座
python -m training.eval.evaluate --runner ft    # Ollama OpenAI 兼容接口评测微调模型
```

- Text2SQL：执行无关、schema 感知打分 —— SQL 合法率 + 组件级 P/R/F1（SELECT 列、
  表引用、WHERE 列、GROUP BY、HAVING、ORDER BY、LIMIT、聚合、子查询）。
- 数仓设计：JSON 合法率 + 必需键齐全 + 分层覆盖 + 表名模式 + DDL/指标存在；
  长输出被 `max_new_tokens` 截断时按原文 key 存在性宽容评分。
- 产出 `report_base.json` / `report_ft.json` 与 `summary.md` 对比表，**如实记录数值，
  不预设目标**。

**本次实测结果**（Qwen3-4B，各 30 条 Text2SQL + 30 条数仓用例）：

| 指标 | 基座 | 微调 | Δ |
| --- | --- | --- | --- |
| Text2SQL 组件 F1 | 0.622 | 0.764 | +0.143（+22.9%） |
| 数仓结构评分 | 0.0 | 0.763 | +0.763 |

> 数仓结构评分使用截断宽容指标：微调模型输出的设计 JSON 结构正确，但超出
> 3072 token 上限被截断，故按 key 存在性评分；调高 `max_new_tokens_warehouse`
> 可获得完整 JSON 与更高分。

## 4. 导出与私有化部署

```bash
bash training/export/export.sh   # 合并 LoRA → GGUF(q4_k_m) → ollama create
```

- **两步导出**（新版 llama.cpp 将转换与量化分离）：先 `convert_hf_to_gguf.py --outtype f16`，
  再用编译好的 `llama-quantize` 产出 `q4_k_m`。合并建议用 CPU 加载（`device_map="cpu"`，
  约需 11GB 内存），避免 8G 显存放不下 bf16 模型。
- `training/export/Modelfile`：Qwen3 ChatML 模板（无思考包装）+ `PARAMETER stop` + temperature 0
  （新版 Ollama 已移除 `STOP` 指令）。
- 验证本地 OpenAI 兼容接口（Windows 已装 Ollama 可直接用）：

```bash
curl http://127.0.0.1:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"datacopilot-qwen3-4b","messages":[{"role":"user","content":"统计活跃用户"}]}'
```

## 5. 接入平台智能路由

```text
DATACOPILOT_LLM__ROUTING_ENABLED=true
DATACOPILOT_LLM__CLOUD_PROVIDER=deepseek
DATACOPILOT_LOCAL__ENABLED=true
DATACOPILOT_LOCAL__BASE_URL=http://127.0.0.1:11434/v1
DATACOPILOT_LOCAL__CHAT_MODEL=datacopilot-qwen3-8b
```

启动后，Text2SQL、SQL 审核、数仓设计等专业数据工程任务路由到本地微调模型，
通用对话继续走云端；本地模型不可用时自动降级云端。

## 数据与模型许可

- Spider 数据集为研究用途（CC BY-SA），构建脚本保留 `source` 元数据。企业落地时请
  使用自有数据或确认公开数据集许可。
- 基座 Qwen3-8B-Instruct 遵循 Qwen 开源许可，商用前请核对最新条款。
