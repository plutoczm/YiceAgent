#!/usr/bin/env bash
# LoRA 合并 + GGUF 导出 + Ollama 导入一键脚本（WSL2 / Linux）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LLAMA_CPP="${ROOT}/training/.deps/llama.cpp"
MERGE_PY="${ROOT}/training/export/export_gguf.py"
MODELFILE="${ROOT}/training/export/Modelfile"
GGUF="${ROOT}/training/data/models/datacopilot-qwen3-4b-q4_k_m.gguf"
MODEL_NAME="${OLLAMA_MODEL_NAME:-datacopilot-qwen3-4b}"

# 准备 llama.cpp（github 被墙时可用镜像：https://ghfast.top/https://github.com/...）
if [ ! -f "${LLAMA_CPP}/convert_hf_to_gguf.py" ]; then
  echo "[1/4] clone llama.cpp"
  git clone --depth 1 https://ghfast.top/https://github.com/ggml-org/llama.cpp "${LLAMA_CPP}"
  # 编译 llama-quantize（新版 llama.cpp 将转换与量化分离）
  source "$(conda info --base)/etc/profile.d/conda.sh" 2>/dev/null || true
  conda install -y -n agent --override-channels -c conda-forge cmake >/dev/null 2>&1 || true
  (cd "${LLAMA_CPP}" && cmake -B build -DLLAMA_CUDA=OFF -DGGML_CUDA=OFF && cmake --build build --target llama-quantize -j4)
else
  echo "[1/4] llama.cpp 已就绪"
fi

echo "[2/4] merge LoRA + convert GGUF"
python "${MERGE_PY}" --llama-cpp "${LLAMA_CPP}" --gguf "${GGUF}"

echo "[3/4] ollama create ${MODEL_NAME}"
ollama create "${MODEL_NAME}" -f "${MODELFILE}"

echo "[4/4] 验证本地模型"
ollama list | grep "${MODEL_NAME}"
curl -s http://127.0.0.1:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"生成一条查询活跃用户的SQL\"}]}"
echo
echo "完成。可开启 DATACOPILOT_LLM__ROUTING_ENABLED=true 接入智能路由。"
