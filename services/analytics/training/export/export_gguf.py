"""LoRA 合并 + GGUF 量化导出。

步骤：
1. 合并 LoRA 适配器到基座，得到 merged 16-bit 模型。
2. 调用 llama.cpp convert_hf_to_gguf.py 导出 q4_k_m GGUF。

用法（GPU 环境）：
    python -m training.export.export_gguf \
        --lora training/data/models/lora-datacopilot \
        --base Qwen/Qwen3-8B-Instruct \
        --merged training/data/models/merged \
        --gguf training/data/models/datacopilot-qwen3-8b-q4_k_m.gguf
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def merge_lora(*, lora_dir: Path, base_model: str, merged_dir: Path) -> None:
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import PeftModel
    except ImportError as exc:
        raise RuntimeError("transformers/peft 未安装") from exc

    model = AutoModelForCausalLM.from_pretrained(base_model, torch_dtype="auto")
    model = PeftModel.from_pretrained(model, str(lora_dir))
    merged = model.merge_and_unload()
    merged.save_pretrained(str(merged_dir))
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    tokenizer.save_pretrained(str(merged_dir))
    print(f"[done] merged model saved to {merged_dir}")


def convert_to_gguf(*, merged_dir: Path, gguf_path: Path, llama_cpp_dir: Path) -> None:
    """新版 llama.cpp 两步导出：先转 f16 GGUF，再用 llama-quantize 量化 q4_k_m。"""
    converter = llama_cpp_dir / "convert_hf_to_gguf.py"
    quantizer = llama_cpp_dir / "build" / "bin" / "llama-quantize"
    if not converter.is_file():
        raise FileNotFoundError(
            f"llama.cpp 未找到：{converter}。请先运行 scripts/prepare_wsl2.sh 或手动 clone。"
        )
    gguf_path.parent.mkdir(parents=True, exist_ok=True)
    f16_path = gguf_path.with_suffix(".f16.gguf")
    subprocess.run(
        [
            sys.executable,
            str(converter),
            str(merged_dir),
            "--outfile",
            str(f16_path),
            "--outtype",
            "f16",
        ],
        check=True,
    )
    print(f"[1/2] f16 GGUF saved to {f16_path}")
    if quantizer.is_file():
        subprocess.run(
            [
                str(quantizer),
                str(f16_path),
                str(gguf_path),
                "q4_k_m",
            ],
            check=True,
        )
        print(f"[2/2] q4_k_m GGUF saved to {gguf_path}")
    else:
        print(
            "[warn] llama-quantize 未编译（llama.cpp 需 cmake 构建 build/bin/llama-quantize），"
            "已产出 f16 GGUF。可直接用 Ollama 导入 f16 版本，或先构建量化工具。"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="LoRA 合并 + GGUF 量化导出")
    parser.add_argument("--lora", type=Path, default=PROJECT_ROOT / "training/data/models/lora-datacopilot")
    parser.add_argument("--base", default="Qwen/Qwen3-8B-Instruct")
    parser.add_argument("--merged", type=Path, default=PROJECT_ROOT / "training/data/models/merged")
    parser.add_argument("--gguf", type=Path, default=PROJECT_ROOT / "training/data/models/datacopilot-qwen3-8b-q4_k_m.gguf")
    parser.add_argument("--llama-cpp", type=Path, default=PROJECT_ROOT / "training/.deps/llama.cpp")
    parser.add_argument("--skip-merge", action="store_true")
    args = parser.parse_args()

    if not args.skip_merge:
        merge_lora(lora_dir=args.lora, base_model=args.base, merged_dir=args.merged)
    if shutil.which("ollama") is None:
        print("[warning] ollama 未在 PATH，请先安装，然后执行：")
        print(f"  ollama create datacopilot-qwen3-8b -f training/export/Modelfile")
    convert_to_gguf(merged_dir=args.merged, gguf_path=args.gguf, llama_cpp_dir=args.llama_cpp)


if __name__ == "__main__":
    main()
