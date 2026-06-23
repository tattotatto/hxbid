#!/usr/bin/env python3
"""宏曦标书 - LoRA Fine-Tuning Script (DeepSeek / Qwen base models).

Uses Unsloth for efficient LoRA fine-tuning on consumer GPUs (RTX 3090/4090).

Usage:
    1. Export training data from the app:
       curl -o dataset.jsonl "http://localhost:8000/api/v1/dataset/export?format=jsonl&max_samples=500"

    2. Run this script:
       python scripts/lora_finetune.py --dataset dataset.jsonl --model deepseek-chat

Requirements:
    pip install unsloth transformers datasets accelerate peft
    # Or use the Unsloth Docker image for CUDA support.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import argparse
import json
import os
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="LoRA fine-tune a base model on bid-writing data"
    )
    parser.add_argument(
        "--dataset",
        type=str,
        required=True,
        help="Path to JSONL dataset exported from 宏曦标书",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="unsloth/DeepSeek-R1-Distill-Qwen-7B",
        help="Base model ID (HuggingFace or local path)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="./hongxi-bid-lora",
        help="Output directory for the fine-tuned adapter",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=3,
        help="Number of training epochs",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=4,
        help="Per-device batch size",
    )
    parser.add_argument(
        "--learning-rate",
        type=float,
        default=2e-4,
        help="Learning rate for LoRA",
    )
    parser.add_argument(
        "--lora-rank",
        type=int,
        default=16,
        help="LoRA rank (r)",
    )
    parser.add_argument(
        "--max-seq-length",
        type=int,
        default=4096,
        help="Max sequence length",
    )
    parser.add_argument(
        "--push-to-hub",
        type=str,
        default="",
        help="HuggingFace repo to push adapter to (e.g. myorg/hongxi-bid-lora)",
    )
    return parser.parse_args()


def load_dataset(path: str):
    """Load a JSONL dataset exported from 宏曦标书."""
    data = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))
    print(f"Loaded {len(data)} training samples from {path}")
    return data


def format_for_training(samples: list) -> list:
    """Convert JSONL format to Unsloth-compatible conversation format."""
    formatted = []
    for s in samples:
        messages = s.get("messages", [])
        text_parts = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                text_parts.append(f"<|im_start|>system\n{content}<|im_end|>")
            elif role == "user":
                text_parts.append(f"<|im_start|>user\n{content}<|im_end|>")
            elif role == "assistant":
                text_parts.append(f"<|im_start|>assistant\n{content}<|im_end|>")
        formatted.append({"text": "\n".join(text_parts)})
    return formatted


def main():
    args = parse_args()

    # Check dataset exists
    if not Path(args.dataset).exists():
        print(f"Error: dataset file not found: {args.dataset}")
        sys.exit(1)

    print("=" * 60)
    print("  宏曦标书 LoRA Fine-Tuning")
    print("=" * 60)
    print(f"  Dataset:      {args.dataset}")
    print(f"  Base model:   {args.model}")
    print(f"  Output:       {args.output}")
    print(f"  Epochs:       {args.epochs}")
    print(f"  Batch size:   {args.batch_size}")
    print(f"  LoRA rank:    {args.lora_rank}")
    print(f"  Max seq len:  {args.max_seq_length}")
    print("=" * 60)

    # --- Step 1: Load dataset ---
    samples = load_dataset(args.dataset)
    train_data = format_for_training(samples)

    # --- Step 2: Load model with Unsloth ---
    print("\n[1/4] Loading base model with Unsloth...")
    try:
        from unsloth import FastLanguageModel
        import torch

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=args.model,
            max_seq_length=args.max_seq_length,
            dtype=None,  # auto-detect
            load_in_4bit=True,  # 4-bit quantization for memory efficiency
        )
        print(f"  Model loaded: {args.model}")
        print(f"  GPU memory: {torch.cuda.max_memory_allocated() / 1024**3:.1f} GB")
    except ImportError:
        print("\n  [WARNING] Unsloth not installed.")
        print("  Install with: pip install unsloth")
        print("\n  Falling back to demo mode — showing dataset statistics only.\n")

        # Show dataset stats
        total_chars = sum(len(s.get("text", "")) for s in train_data)
        print(f"  Training samples:  {len(train_data)}")
        print(f"  Total characters:  {total_chars:,}")
        print(f"  Est. tokens (CN):  {total_chars // 2:,}")
        print(f"\n  To run actual training:")
        print(f"  1. Install Unsloth: pip install unsloth")
        print(f"  2. Or use Docker: docker run --gpus all -v $PWD:/workspace unsloth/unsloth")
        print(f"  3. Then re-run this script.")
        return

    # --- Step 3: Apply LoRA ---
    print("\n[2/4] Applying LoRA adapters...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_rank,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        lora_alpha=args.lora_rank * 2,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=3407,
    )

    # --- Step 4: Train ---
    print("\n[3/4] Training...")
    from transformers import TrainingArguments
    from trl import SFTTrainer

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_data,
        dataset_text_field="text",
        max_seq_length=args.max_seq_length,
        args=TrainingArguments(
            per_device_train_batch_size=args.batch_size,
            gradient_accumulation_steps=4,
            warmup_steps=10,
            num_train_epochs=args.epochs,
            learning_rate=args.learning_rate,
            fp16=not torch.cuda.is_bf16_supported(),
            bf16=torch.cuda.is_bf16_supported(),
            logging_steps=1,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="linear",
            seed=3407,
            output_dir=args.output,
        ),
    )

    trainer.train()

    # --- Step 5: Save ---
    print(f"\n[4/4] Saving LoRA adapter to {args.output}...")
    model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)
    print(f"  Adapter saved to {args.output}")

    # --- Optional: Push to HuggingFace Hub ---
    if args.push_to_hub:
        print(f"\n  Pushing to HuggingFace Hub: {args.push_to_hub}...")
        model.push_to_hub(args.push_to_hub, token=os.environ.get("HF_TOKEN"))
        print(f"  Pushed to https://huggingface.co/{args.push_to_hub}")

    # --- Final summary ---
    print("\n" + "=" * 60)
    print("  Fine-tuning complete!")
    print(f"  Adapter: {args.output}")
    print(f"  To use with vLLM: --lora-modules {args.output}")
    print(f"  To merge: see unsloth merge_and_save()")
    print("=" * 60)


if __name__ == "__main__":
    main()
