"""Unsloth 4-bit QLoRA 微调脚本（Qwen3 系列，RTX 5060 Ti 单卡 8G 显存）。

用法（在含 GPU 的 conda "agent" 环境，WSL2 推荐）：
    python -m training.train.train_unsloth [--config training/config/train.yaml]

要点：
- 仅训练 LoRA 低秩参数，冻结基座。
- 梯度检查点 + 梯度累积控制显存，4-bit 量化 + bf16。
- Qwen3 思考模式关闭，数据格式化与推理一致。
- 注意：8G 显存下建议用 Qwen3-4B（8B 会因显存占满而病态变慢）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.train.train_config import TrainConfig, load_config  # noqa: E402


def train(config: TrainConfig) -> None:
    try:
        from unsloth import FastLanguageModel, is_bfloat16_supported  # 延迟导入
        from datasets import load_dataset
        from unsloth.chat_templates import get_chat_template
        from transformers import TrainingArguments
        from trl import SFTTrainer
    except ImportError as exc:
        raise RuntimeError(
            "Unsloth/trl 未安装。请在 GPU 环境执行：pip install -r training/requirements.txt"
        ) from exc

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=config.base_model,
        max_seq_length=config.max_seq_length,
        dtype=None,
        load_in_4bit=config.use_4bit,
    )

    # Qwen3 ChatML 模板，训练阶段同样关闭思考模式，与线上推理一致
    tokenizer = get_chat_template(
        tokenizer,
        chat_template="qwen2.5",
        map_eos_token=True,
    )

    def _to_role_messages(conversation) -> list[dict]:
        role_map = {"human": "user", "gpt": "assistant", "system": "system"}
        return [
            {"role": role_map.get(turn.get("from", "user"), "user"), "content": turn.get("value", "")}
            for turn in conversation
            if turn.get("value")
        ]

    def format_sharegpt(examples):
        conversations = examples["conversations"]
        texts = [
            tokenizer.apply_chat_template(
                _to_role_messages(convo),
                tokenize=False,
                add_generation_prompt=False,
                chat_template_kwargs={"enable_thinking": False},
            )
            for convo in conversations
        ]
        return {"text": texts}

    dataset = load_dataset("json", data_files=str(PROJECT_ROOT / config.data_file))
    dataset = dataset.map(format_sharegpt, batched=True, num_proc=2)

    model = FastLanguageModel.get_peft_model(
        model,
        r=config.lora_r,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        target_modules=config.target_modules,
        use_gradient_checkpointing="unsloth" if config.gradient_checkpointing else False,
        random_state=config.seed,
    )

    training_args = TrainingArguments(
        per_device_train_batch_size=config.batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        warmup_steps=config.warmup_steps,
        num_train_epochs=config.epochs,
        learning_rate=config.learning_rate,
        fp16=not is_bfloat16_supported(),
        bf16=is_bfloat16_supported() and config.use_bf16,
        logging_steps=config.logging_steps,
        save_steps=config.save_steps,
        output_dir=str(PROJECT_ROOT / config.output_dir),
        seed=config.seed,
        report_to=config.report_to,
        torch_compile=False,
        dataloader_num_workers=2,
        # save_strategy="no"：Unsloth 修补 trl 后 torch.save(SFTConfig) 会抛 PicklingError，
        # 禁用 trainer 的 checkpoint 保存（含最终步），适配器由下方 model.save_pretrained 落盘。
        save_strategy="no",
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset["train"],
        dataset_text_field="text",
        max_seq_length=config.max_seq_length,
        dataset_num_proc=2,
        packing=False,
        args=training_args,
    )
    trainer.train()
    model.save_pretrained(str(PROJECT_ROOT / config.output_dir))
    tokenizer.save_pretrained(str(PROJECT_ROOT / config.output_dir))
    print(f"[done] LoRA adapter saved to {config.output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Unsloth QLoRA 微调")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()
    train(load_config(args.config))


if __name__ == "__main__":
    main()
