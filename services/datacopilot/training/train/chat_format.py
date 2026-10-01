"""Qwen3 chat 模板处理。

Qwen3-8B-Instruct 默认开启思考模式，可能输出 <|thinking|> 块，破坏服务端
JSON 解析。训练与推理统一关闭思考，保证微调模型与线上提示行为一致。
"""

from __future__ import annotations


def apply_chat_template(tokenizer, messages: list[dict]) -> str:
    """对 Qwen3 关闭思考模式并渲染对话。

    基座模型（base，如 Qwen3-4B）的 tokenizer 可能没有 chat_template，
    此时回退到手写 ChatML，避免 apply_chat_template 抛错。
    """
    if getattr(tokenizer, "chat_template", None) is not None:
        return tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            chat_template_kwargs={"enable_thinking": False},
        )
    # 无 chat_template 的手写 ChatML 兜底
    rendered: list[str] = []
    for message in messages:
        role = message["role"]
        content = message["content"]
        if role == "system":
            rendered.append(f"<|im_start|>system\n{content}<|im_end|>")
        elif role == "user":
            rendered.append(f"<|im_start|>user\n{content}<|im_end|>")
        elif role == "assistant":
            rendered.append(f"<|im_start|>assistant\n{content}<|im_end|>")
    rendered.append("<|im_start|>assistant\n")
    return "\n".join(rendered)
