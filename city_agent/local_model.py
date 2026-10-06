from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any


class ModelUnavailable(RuntimeError):
    pass


@dataclass
class ModelConfig:
    model_path: str = "models/Qwen3-8B"
    model_name: str = "Qwen/Qwen3-8B"
    quantization: str = "4bit-nf4"
    # Qwen3-8B 4-bit fits the target 12 GB GPU only with a bounded KV cache.
    # The report context is already ranked before it reaches the tokenizer.
    # Keep the KV cache bounded on a 12 GiB GPU.  The extractor also ranks and
    # trims report fragments before they reach this tokenizer limit.
    max_input_tokens: int = 2048
    max_new_tokens: int = 512
    gpu_memory_gib: int = 9
    cpu_memory_gib: int = 24
    offload_dir: str = "cache/model_offload"
    generation_retries: int = 2
    temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "ModelConfig":
        return cls(
            model_path=os.getenv("CITY_AGENT_MODEL_PATH", cls.model_path),
            model_name=os.getenv("CITY_AGENT_MODEL_NAME", cls.model_name),
            quantization=os.getenv("CITY_AGENT_QUANTIZATION", cls.quantization),
            max_input_tokens=int(os.getenv("CITY_AGENT_MAX_INPUT_TOKENS", str(cls.max_input_tokens))),
            max_new_tokens=int(os.getenv("CITY_AGENT_MAX_NEW_TOKENS", str(cls.max_new_tokens))),
            gpu_memory_gib=int(os.getenv("CITY_AGENT_GPU_MEMORY_GIB", str(cls.gpu_memory_gib))),
            cpu_memory_gib=int(os.getenv("CITY_AGENT_CPU_MEMORY_GIB", str(cls.cpu_memory_gib))),
            offload_dir=os.getenv("CITY_AGENT_OFFLOAD_DIR", cls.offload_dir),
            generation_retries=int(os.getenv("CITY_AGENT_GENERATION_RETRIES", str(cls.generation_retries))),
        )

    def metadata(self) -> dict[str, Any]:
        return asdict(self)


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1) if fenced else cleaned
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型未返回 JSON 对象")
    value = json.loads(candidate[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型 JSON 顶层必须是对象")
    return value


class LocalModel:
    def __init__(self, config: ModelConfig | None = None):
        self.config = config or ModelConfig.from_env()
        self.tokenizer = None
        self.model = None
        self.torch = None
        self.load_latency_ms: float | None = None

    @property
    def metadata(self) -> dict[str, Any]:
        value = self.config.metadata()
        if self.load_latency_ms is not None:
            value["model_load_latency_ms"] = self.load_latency_ms
        if self.torch is not None and self.torch.cuda.is_available():
            value["cuda_device"] = self.torch.cuda.get_device_name(0)
            value["cuda_memory_allocated_bytes"] = int(self.torch.cuda.memory_allocated(0))
            value["cuda_memory_reserved_bytes"] = int(self.torch.cuda.memory_reserved(0))
        if self.model is not None and hasattr(self.model, "hf_device_map"):
            value["device_map"] = dict(self.model.hf_device_map)
        return value

    def load(self) -> "LocalModel":
        started = perf_counter()
        path = Path(self.config.model_path)
        if not path.exists():
            raise ModelUnavailable(f"模型目录不存在：{path}。请先运行 scripts/download_model.py")
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        except ImportError as exc:
            raise ModelUnavailable("缺少 torch/transformers，请安装 requirements-model.txt") from exc
        if self.config.quantization.startswith("4bit"):
            try:
                import bitsandbytes  # noqa: F401
            except ImportError as exc:
                raise ModelUnavailable("4-bit 推理需要 bitsandbytes；请安装 requirements-model.txt") from exc
            quantization = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.float16,
            )
        else:
            quantization = None
        self.tokenizer = AutoTokenizer.from_pretrained(str(path), trust_remote_code=True)
        offload_dir = Path(self.config.offload_dir)
        if not offload_dir.is_absolute():
            offload_dir = Path.cwd() / offload_dir
        offload_dir.mkdir(parents=True, exist_ok=True)
        if torch.cuda.is_available():
            max_memory: dict[Any, str] = {0: f"{self.config.gpu_memory_gib}GiB", "cpu": f"{self.config.cpu_memory_gib}GiB"}
        else:
            max_memory = {"cpu": f"{self.config.cpu_memory_gib}GiB"}
        kwargs: dict[str, Any] = {
            "trust_remote_code": True,
            "device_map": "auto",
            "max_memory": max_memory,
            "offload_folder": str(offload_dir),
            "offload_state_dict": True,
            "low_cpu_mem_usage": True,
        }
        if quantization is not None:
            kwargs["quantization_config"] = quantization
        else:
            kwargs["torch_dtype"] = torch.float16
        self.model = AutoModelForCausalLM.from_pretrained(str(path), **kwargs)
        self.model.eval()
        self.torch = torch
        self.load_latency_ms = round((perf_counter() - started) * 1000, 2)
        return self

    def _input_device(self):
        if self.model is None:
            raise ModelUnavailable("模型尚未加载")
        try:
            device = self.model.get_input_embeddings().weight.device
            if device.type != "meta":
                return device
        except Exception:
            pass
        for device in getattr(self.model, "hf_device_map", {}).values():
            if str(device) not in {"cpu", "disk", "meta"}:
                return self.torch.device(device)
        return self.torch.device("cuda:0" if self.torch.cuda.is_available() else "cpu")

    def generate_json(self, system: str, user: str, max_new_tokens: int | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.model is None or self.tokenizer is None:
            self.load()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error: Exception | None = None
        for attempt in range(self.config.generation_retries + 1):
            current_system = system
            if attempt:
                current_system += "\n上一次输出不是完整合法 JSON。请缩短内容，只输出完整 JSON 对象，不要 Markdown 或解释。"
            messages[0] = {"role": "system", "content": current_system}
            try:
                try:
                    prompt = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
                except TypeError:
                    prompt = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                inputs = self.tokenizer(prompt, return_tensors="pt", truncation=True, max_length=self.config.max_input_tokens)
                inputs = {key: value.to(self._input_device()) for key, value in inputs.items()}
                started = perf_counter()
                with self.torch.inference_mode():
                    output = self.model.generate(
                        **inputs,
                        max_new_tokens=max_new_tokens or self.config.max_new_tokens,
                        do_sample=False,
                        pad_token_id=self.tokenizer.eos_token_id,
                    )
                generated = output[0][inputs["input_ids"].shape[-1] :]
                text = self.tokenizer.decode(generated, skip_special_tokens=True)
                value = extract_json(text)
                metadata = {
                    **self.metadata,
                    "latency_ms": round((perf_counter() - started) * 1000, 2),
                    "generation_attempt": attempt + 1,
                }
                return value, metadata
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if self.torch is not None and self.torch.cuda.is_available():
                    self.torch.cuda.empty_cache()
        raise ValueError(f"模型在 {self.config.generation_retries + 1} 次尝试后仍未返回合法 JSON：{last_error}")
