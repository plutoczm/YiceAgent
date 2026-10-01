"""训练配置加载：dataclass + YAML + 命令行覆盖。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class TrainConfig:
    base_model: str = "Qwen/Qwen3-8B-Instruct"
    data_file: str = "training/data/processed/train.jsonl"
    output_dir: str = "training/data/models/lora-datacopilot"
    epochs: int = 2
    learning_rate: float = 2.0e-4
    batch_size: int = 4
    gradient_accumulation_steps: int = 4
    optimizer: str = "adamw_8bit"
    lr_scheduler: str = "linear"
    warmup_steps: int = 10
    max_seq_length: int = 2048
    seed: int = 42
    lora_r: int = 32
    lora_alpha: int = 64
    lora_dropout: float = 0.0
    target_modules: list[str] = field(
        default_factory=lambda: [
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ]
    )
    use_4bit: bool = True
    quant_type: str = "nf4"
    use_bf16: bool = True
    gradient_checkpointing: bool = True
    save_steps: int = 100
    logging_steps: int = 10
    report_to: str = "none"

    @classmethod
    def from_yaml(cls, path: Path) -> "TrainConfig":
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        allowed = {field_name for field_name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in payload.items() if key in allowed})


def load_config(path: Path | None = None) -> TrainConfig:
    default = PROJECT_ROOT / "training/config/train.yaml"
    if path is None or not path.is_file():
        path = default
    return TrainConfig.from_yaml(path)
