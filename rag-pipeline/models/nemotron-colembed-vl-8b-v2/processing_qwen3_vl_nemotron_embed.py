# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0.
"""Qwen3VLNemotronEmbed Processor for query and document processing."""

import math
from typing import Any, Dict, List, Optional, Union

import torch
from PIL import Image
from transformers import Qwen3VLProcessor


class Qwen3VLNemotronEmbedProcessor(Qwen3VLProcessor):
    """Processor for Qwen3VLNemotronEmbed that handles query/document processing.

    This processor extends Qwen3VLProcessor with methods for processing queries and
    documents for retrieval tasks.

    Args:
        image_processor: Image processor for vision inputs.
        tokenizer: Tokenizer for text inputs.
        chat_template: Optional chat template.
        q_max_length: Maximum length for query sequences (default: 512).
        p_max_length: Maximum length for passage/document sequences (default: 4096).
        query_prefix: Prefix to add to queries (default: "query:").
        passage_prefix: Prefix to add to passages (default: "passage:").
        reserve_tokens_for_images: Reserved tokens for image placeholders (default: 100).
    """

    # Override parent to exclude video_processor (not used by this model) and avoid
    # type-check failures on transformers <5.0 where BaseVideoProcessor doesn't exist.
    attributes = ["image_processor", "tokenizer"]
    video_processor_class = None

    # Attributes to save/load
    processor_attributes = [
        "q_max_length",
        "p_max_length",
        "query_prefix",
        "passage_prefix",
        "reserve_tokens_for_images",
    ]

    def __init__(
        self,
        image_processor=None,
        tokenizer=None,
        chat_template=None,
        q_max_length: int = 512,
        p_max_length: int = 4096,
        query_prefix: str = "query:",
        passage_prefix: str = "passage:",
        reserve_tokens_for_images: int = 100,
        **kwargs,
    ):
        if chat_template is not None:
            super().__init__(image_processor, tokenizer, chat_template, **kwargs)
        else:
            super().__init__(image_processor, tokenizer, **kwargs)

        self.q_max_length = q_max_length
        self.p_max_length = p_max_length
        self.query_prefix = query_prefix
        self.passage_prefix = passage_prefix
        self.reserve_tokens_for_images = reserve_tokens_for_images

        self.patch_size = self.image_processor.patch_size
        self.merge_size = self.image_processor.merge_size

    def apply_chat_template(
        self,
        conversation,
        chat_template=None,
        **kwargs,
    ) -> str:
        """Apply chat template to conversation."""
        return self.tokenizer.apply_chat_template(
            conversation,
            chat_template=chat_template,
            **kwargs,
        )

    @property
    def min_pixels(self) -> int:
        """Get min_pixels from image processor."""
        return self.image_processor.size["shortest_edge"]

    @property
    def max_pixels(self) -> int:
        """Get max_pixels from image processor."""
        return self.image_processor.size["longest_edge"]

    def calculate_image_tokens(
        self,
        image: Image.Image,
        min_pixels: Optional[int] = None,
        max_pixels: Optional[int] = None,
    ) -> int:
        """Calculate the number of tokens an image will use after processing.

        Args:
            image: PIL Image to calculate tokens for.
            min_pixels: Minimum pixels for resizing (uses processor default if None).
            max_pixels: Maximum pixels for resizing (uses processor default if None).

        Returns:
            Number of tokens the image will consume.
        """
        min_pixels = min_pixels or self.min_pixels
        max_pixels = max_pixels or self.max_pixels

        width, height = image.size
        factor = self.patch_size * self.merge_size

        h_bar = round(height / factor) * factor
        w_bar = round(width / factor) * factor

        if h_bar * w_bar > max_pixels:
            beta = math.sqrt((height * width) / max_pixels)
            h_bar = max(factor, math.floor(height / beta / factor) * factor)
            w_bar = max(factor, math.floor(width / beta / factor) * factor)
        elif h_bar * w_bar < min_pixels:
            beta = math.sqrt(min_pixels / (height * width))
            h_bar = math.ceil(height * beta / factor) * factor
            w_bar = math.ceil(width * beta / factor) * factor

        grid_h = h_bar // self.patch_size
        grid_w = w_bar // self.patch_size
        num_patches = grid_h * grid_w
        return num_patches // (self.merge_size**2)

    def process_queries(
        self,
        queries: List[str | dict],
        padding: bool = True,
        truncation: bool = True,
        pad_to_multiple_of: Optional[int] = None,
        return_tensors: str = "pt",
        **kwargs,
    ) -> Dict[str, torch.Tensor]:
        """Process text queries for retrieval.

        Args:
            queries: List of query strings or dicts with "text" key.
            padding: Whether to pad sequences.
            truncation: Whether to truncate sequences.
            pad_to_multiple_of: Pad to a multiple of this value.
            return_tensors: Return tensor type ("pt" for PyTorch).

        Returns:
            Dictionary with input_ids, attention_mask, and other model inputs.
        """
        query_texts = []
        for query in queries:
            if isinstance(query, dict):
                query_text = query["text"]
            else:
                query_text = query

            prefixed = f"{self.query_prefix} {query_text}" if self.query_prefix else query_text
            message = [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": f"Query: {prefixed}"}],
                }
            ]
            query_text = self.apply_chat_template(
                message, tokenize=False, add_generation_prompt=True
            )
            query_texts.append(query_text)

        return self(
            text=query_texts,
            truncation=truncation,
            max_length=self.q_max_length,
            padding=padding,
            pad_to_multiple_of=pad_to_multiple_of,
            return_tensors=return_tensors,
            **kwargs,
        )

    def process_documents(
        self,
        documents: Union[Dict[str, List], List[Dict[str, Any]]],
        padding: bool = True,
        truncation: bool = True,
        pad_to_multiple_of: Optional[int] = None,
        return_tensors: str = "pt",
        **kwargs,
    ) -> Dict[str, torch.Tensor]:
        """Process image-text documents for retrieval.

        Args:
            documents: Either a dict with "image" and "text" keys containing lists,
                      or a list of dicts each with "image" and "text" keys.
            padding: Whether to pad sequences.
            truncation: Whether to truncate sequences.
            pad_to_multiple_of: Pad to a multiple of this value.
            return_tensors: Return tensor type ("pt" for PyTorch).

        Returns:
            Dictionary with input_ids, attention_mask, pixel_values, and other model inputs.
        """
        if isinstance(documents, dict):
            images = documents["image"]
            texts = documents["text"]
            assert len(texts) == len(images), (
                "Number of texts must match number of images"
            )
        elif isinstance(documents, list):
            images = [d["image"] for d in documents]
            texts = [d["text"] for d in documents]
        else:
            raise ValueError("documents must be a dict or list of dicts")

        if self.passage_prefix:
            texts = [f"{self.passage_prefix} {t}" for t in texts]

        image_tokens_list = [self.calculate_image_tokens(img) for img in images]
        max_image_tokens = max(image_tokens_list) if image_tokens_list else 0

        assert self.p_max_length > max_image_tokens + self.reserve_tokens_for_images, (
            f"p_max_length ({self.p_max_length}) is too small for max_image_tokens "
            f"({max_image_tokens}) + reserve ({self.reserve_tokens_for_images})"
        )
        available_text_tokens = (
            self.p_max_length - max_image_tokens - self.reserve_tokens_for_images
        )

        if (
            pad_to_multiple_of is not None
            and available_text_tokens % pad_to_multiple_of != 0
        ):
            available_text_tokens = (
                available_text_tokens // pad_to_multiple_of
            ) * pad_to_multiple_of

        input_texts = []
        for text, image in zip(texts, images):
            message = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image},
                        {"type": "text", "text": text},
                    ],
                }
            ]
            input_text = self.apply_chat_template(
                message, tokenize=False, add_generation_prompt=True
            )
            input_texts.append(input_text)

        return self(
            text=input_texts,
            images=images,
            truncation=truncation,
            padding=padding,
            pad_to_multiple_of=pad_to_multiple_of,
            return_tensors=return_tensors,
            max_length=available_text_tokens,
            **kwargs,
        )
