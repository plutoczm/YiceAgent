#!/usr/bin/env bash
# WSL2 (Ubuntu 22.04/24.04) 一次性环境准备：训练 + 本地推理 + GGUF 导出
# 用法：bash training/scripts/prepare_wsl2.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "[1/6] 检查 NVIDIA 驱动（Blackwell sm_120 需 CUDA 12.8+ / 驱动 570+）"
nvidia-smi

echo "[2/6] conda 环境（复用已有 agent 环境）"
if ! command -v conda >/dev/null 2>&1; then
  echo "未找到 conda，请先安装 Miniconda（https://docs.conda.io/en/latest/miniconda.html）"
  exit 1
fi
ENV_NAME="${CONDA_TRAIN_ENV:-agent}"
if conda env list | grep -q "${ENV_NAME}"; then
  echo "使用已有 conda 环境：${ENV_NAME}"
else
  echo "创建 conda 环境：${ENV_NAME} (python 3.11)"
  # --override-channels + conda-forge：避免 Anaconda 默认渠道的 TOS 条款拦截
  conda create -y -n "${ENV_NAME}" python=3.11 --override-channels -c conda-forge
fi

echo "[3/6] 安装训练依赖"
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${ENV_NAME}"
# RTX 5060 Ti (Blackwell sm_120) 需要 CUDA 12.8+ 的 PyTorch wheel，先装 torch 再装其余依赖
pip install torch --index-url https://download.pytorch.org/whl/cu128
pip install -r "${ROOT}/training/requirements.txt"

echo "[4/6] 安装 Ollama"
if ! command -v ollama >/dev/null 2>&1; then
  echo "尝试从 ollama.com 安装 Ollama（若 github.com 不可达会失败，可稍后手动装）..."
  if ! curl -fsSL https://ollama.com/install.sh | sh; then
    echo "[warn] Ollama 安装失败（github.com 不可达）。不影响训练，仅影响微调后评测/部署。"
  fi
fi
if command -v ollama >/dev/null 2>&1; then
  ollama serve >/dev/null 2>&1 &
  sleep 3
else
  echo "[warn] 未检测到 ollama，跳过启动。"
fi

echo "[5/6] 校验 bitsandbytes/transformers 可导入"
python - <<'PY'
import torch, bitsandbytes, transformers, trl, unsloth
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "N/A")
PY

echo "[6/6] 完成。下一步："
echo "  python -m training.dataset_builder.build        # 构建 3200+ 指令集"
echo "  python -m training.train.train_unsloth          # QLoRA 微调"
echo "  python -m training.eval.evaluate --runner base  # 评测基座"
echo "  python -m training.eval.evaluate --runner ft    # 评测微调模型"
echo "  bash training/export/export.sh                  # GGUF + Ollama 导入"
