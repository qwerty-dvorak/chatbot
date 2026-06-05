import torch
from transformers.cache_utils import Cache
from transformers.modeling_attn_mask_utils import _prepare_4d_attention_mask
from transformers.models.llama.configuration_llama import LlamaConfig
from transformers.models.llama.modeling_llama import LlamaModel


class LlamaBidirectionalConfig(LlamaConfig):
    model_type = "llama_bidirec"

    def __init__(self, pooling="avg", temperature=1.0, **kwargs):
        self.pooling = pooling
        self.temperature = temperature

        super().__init__(**kwargs)


class LlamaBidirectionalModel(LlamaModel):
    config_class = LlamaBidirectionalConfig

    def __init__(self, config: LlamaConfig):
        super().__init__(config)

        for layer in self.layers:
            layer.self_attn.is_causal = False

    def _update_causal_mask(
        self,
        attention_mask: torch.Tensor,
        input_tensor: torch.Tensor,
        cache_position: torch.Tensor,
        past_key_values: Cache,
        output_attentions: bool,
    ):
        assert self.config._attn_implementation in [
            "flash_attention_2",
            "eager",
        ], (
            f"Unsupported attention implementation: "
            f"{self.config._attn_implementation}, "
            f"only support flash_attention_2 or eager"
        )

        if self.config._attn_implementation == "flash_attention_2":
            if attention_mask is not None and (attention_mask == 0.0).any():
                return attention_mask
            return None
        elif self.config._attn_implementation == "eager":
            # Generates bi-directional attention.
            causal_mask = _prepare_4d_attention_mask(
                attention_mask,
                dtype=input_tensor.dtype,
            )
            return causal_mask
