"""基座模型离线推理（transformers + 4-bit）。

仅做推理，不加载训练相关代码；Qwen3 思考模式关闭与训练保持一致。
"""

from __future__ import annotations

from training.train.chat_format import apply_chat_template


class BaseRunner:
    name = "base"

    def __init__(
        self,
        *,
        model_name: str,
        max_new_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> None:
        self.model_name = model_name
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature
        self._model = None
        self._tokenizer = None

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
            import torch
        except ImportError as exc:
            raise RuntimeError("transformers 未安装，请先执行 pip install -r training/requirements.txt") from exc

        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, trust_remote_code=True)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            quantization_config=quantization_config,
            device_map="auto",
            torch_dtype=torch.bfloat16,
            trust_remote_code=True,
        )

    def generate(self, messages: list[dict], *, max_new_tokens: int | None = None) -> str:
        self._load()
        prompt = apply_chat_template(self._tokenizer, messages)
        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._model.device)
        outputs = self._model.generate(
            **inputs,
            max_new_tokens=max_new_tokens or self.max_new_tokens,
            do_sample=False,
            temperature=self.temperature,
        )
        generated = outputs[0][inputs["input_ids"].shape[1]:]
        return self._tokenizer.decode(generated, skip_special_tokens=True).strip()


class LoraRunner(BaseRunner):
    """基座 + 本地 LoRA 适配器推理（transformers 4-bit），用于无需 Ollama 的微调后评测。"""

    name = "lora"

    def __init__(
        self,
        *,
        model_name: str,
        adapter_path: str,
        max_new_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> None:
        super().__init__(
            model_name=model_name,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
        )
        self.adapter_path = adapter_path

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
            from peft import PeftModel
            import torch
        except ImportError as exc:
            raise RuntimeError("transformers/peft 未安装") from exc

        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, trust_remote_code=True)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            quantization_config=quantization_config,
            device_map="auto",
            torch_dtype=torch.bfloat16,
            trust_remote_code=True,
        )
        self._model = PeftModel.from_pretrained(self._model, self.adapter_path)
