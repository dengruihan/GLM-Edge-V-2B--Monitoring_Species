# -*- coding: utf-8 -*-
import os

os.environ["WANDB_DISABLED"] = "true"
import jieba
import dataclasses as dc
import functools
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Union
import numpy as np
import ruamel.yaml as yaml
import torch
import typer
from datasets import Dataset, Split
from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
from peft import PeftConfig, get_peft_config, get_peft_model
from rouge_chinese import Rouge
from torch import nn
from transformers import (
    AutoModelForCausalLM,
    AutoImageProcessor,
    AutoTokenizer,
    EvalPrediction,
    GenerationConfig,
    PreTrainedTokenizer,
    Seq2SeqTrainingArguments,
)
from transformers import DataCollatorForSeq2Seq as _DataCollatorForSeq2Seq
from transformers import Seq2SeqTrainer as _Seq2SeqTrainer
from datasets import load_dataset, DatasetDict, NamedSplit
from typing import Optional
from PIL import Image

app = typer.Typer(pretty_exceptions_show_locals=False)


class DataCollatorForSeq2Seq(_DataCollatorForSeq2Seq):
    def __call__(self, features, return_tensors=None):
        output_ids = [feature["output_ids"] for feature in features] if "output_ids" in features[0].keys() else None
        if output_ids is not None:
            max_output_length = max(len(out) for out in output_ids)
            if self.pad_to_multiple_of is not None:
                max_output_length = (
                    (max_output_length + self.pad_to_multiple_of - 1)
                    // self.pad_to_multiple_of
                    * self.pad_to_multiple_of
                )
            for feature in features:
                remainder = [self.tokenizer.pad_token_id] * (max_output_length - len(feature["output_ids"]))
                if isinstance(feature["output_ids"], list):
                    feature["output_ids"] = feature["output_ids"] + remainder
                else:
                    feature["output_ids"] = np.concatenate([feature["output_ids"], remainder]).astype(np.int64)
        return super().__call__(features, return_tensors)


class Seq2SeqTrainer(_Seq2SeqTrainer):
    def prediction_step(
        self,
        model: nn.Module,
        inputs: dict,
        prediction_loss_only: bool,
        ignore_keys=None,
        **gen_kwargs,
    ) -> tuple[Optional[float], Optional[torch.Tensor], Optional[torch.Tensor]]:
        with torch.no_grad():
            if self.args.predict_with_generate:
                output_ids = inputs.pop("output_ids", None)

            if "labels" in inputs:
                del inputs["labels"]

            loss, generated_tokens, labels = super().prediction_step(
                model=model,
                inputs=inputs,
                prediction_loss_only=prediction_loss_only,
                ignore_keys=ignore_keys,
                **gen_kwargs,
            )

            if generated_tokens is not None:
                generated_tokens = generated_tokens[:, inputs["input_ids"].size()[1] :]

            if self.args.predict_with_generate:
                labels = output_ids
            
            del inputs, output_ids
            torch.cuda.empty_cache()

        return loss, generated_tokens, labels


@dc.dataclass
class DataConfig(object):
    train_file: Optional[str] = None
    val_file: Optional[str] = None
    test_file: Optional[str] = None
    num_proc: Optional[int] = None

    @property
    def data_format(self) -> str:
        return Path(self.train_file).suffix

    @property
    def data_files(self) -> dict[NamedSplit, str]:
        return {
            split: data_file
            for split, data_file in zip(
                [Split.TRAIN, Split.VALIDATION, Split.TEST],
                [self.train_file, self.val_file, self.test_file],
            )
            if data_file is not None
        }


@dc.dataclass
class FinetuningConfig(object):
    data_config: DataConfig

    max_input_length: int
    max_output_length: int
    freezeV: bool

    training_args: Seq2SeqTrainingArguments = dc.field(
        default_factory=lambda: Seq2SeqTrainingArguments(output_dir="./output")
    )
    peft_config: Optional[PeftConfig] = None

    def __post_init__(self):
        if not self.training_args.do_eval or self.data_config.val_file is None:
            self.training_args.do_eval = False
            self.training_args.evaluation_strategy = "no"
            self.data_config.val_file = None
        else:
            self.training_args.per_device_eval_batch_size = (
                self.training_args.per_device_eval_batch_size or self.training_args.per_device_train_batch_size
            )

    @classmethod
    def from_dict(cls, **kwargs) -> "FinetuningConfig":
        training_args = kwargs.get("training_args", None)
        if training_args is not None and not isinstance(training_args, Seq2SeqTrainingArguments):
            gen_config = training_args.get("generation_config")
            if not isinstance(gen_config, GenerationConfig):
                training_args["generation_config"] = GenerationConfig(**gen_config)
            kwargs["training_args"] = Seq2SeqTrainingArguments(**training_args)

        data_config = kwargs.get("data_config")
        if not isinstance(data_config, DataConfig):
            kwargs["data_config"] = DataConfig(**data_config)

        peft_config = kwargs.get("peft_config", None)
        if peft_config is not None and not isinstance(peft_config, PeftConfig):
            kwargs["peft_config"] = get_peft_config(config_dict=peft_config)
        return cls(**kwargs)

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "FinetuningConfig":
        path = Path(path)
        parser = yaml.YAML(typ="safe", pure=True)
        parser.indent(mapping=2, offset=2, sequence=4)
        parser.default_flow_style = False
        kwargs = parser.load(path)
        return cls.from_dict(**kwargs)


def _load_datasets(
    data_dir: str,
    data_format: str,
    data_files: dict[NamedSplit, str],
    num_proc: Optional[int],
) -> DatasetDict:
    if data_format == ".jsonl":
        dataset_dct = load_dataset(
            data_dir,
            data_files=data_files,
            split=None,
            num_proc=num_proc,
        )
    else:
        raise NotImplementedError(f"Cannot load dataset in the '{data_format}' format.")
    return dataset_dct


class DataManager(object):
    def __init__(self, data_dir: str, data_config: DataConfig):
        self._num_proc = data_config.num_proc

        self._dataset_dct = _load_datasets(
            data_dir,
            data_config.data_format,
            data_config.data_files,
            self._num_proc,
        )

    def _get_dataset(self, split: NamedSplit) -> Optional[Dataset]:
        return self._dataset_dct.get(split, None)

    def get_dataset(
        self,
        split: NamedSplit,
        process_fn: Callable[[dict[str, Any]], dict[str, Any]],
        batched: bool = True,
        remove_orig_columns: bool = True,
    ) -> Optional[Dataset]:
        orig_dataset = self._get_dataset(split)
        if orig_dataset is None:
            return
        if remove_orig_columns:
            remove_columns = orig_dataset.column_names
        else:
            remove_columns = None
        return orig_dataset.map(
            process_fn,
            batched=batched,
            remove_columns=remove_columns,
            num_proc=self._num_proc,
            # This is default params of  orig_dataset.map, and you can change it smaller
            # https://github.com/THUDM/GLM-4/issues/277
            writer_batch_size=1000,
            batch_size=1000,
        )


def process_batch(
    batch: Mapping[str, Sequence],
    tokenizer: PreTrainedTokenizer,
    processor,
    max_input_length: int,
    max_output_length: int,
) -> dict[str, list]:
    batched_conv = batch["messages"]
    batched_input_ids = []
    batched_attention_mask = []
    batched_position_ids = []
    batched_labels = []
    batched_images = []

    max_length = max_input_length + max_output_length

    for conv in batched_conv:
        input_ids = []
        attention_mask = []
        position_ids = []
        loss_masks = []
        pixel_values = []

        if conv[0]["content"][0].get("image"):
            image = Image.open(conv[0]["content"][0]["image"])
            pixel_values.append(torch.tensor(processor(image).pixel_values))

        for message in conv:
            loss_mask_val = False if message["role"] in ("system", "user") else True
            new_input_ids_all = tokenizer.apply_chat_template(
                [message],
                add_generation_prompt=False,
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
            )
            new_input_ids = new_input_ids_all["input_ids"][0].tolist()
            new_attention_mask = new_input_ids_all["attention_mask"][0].tolist()
            new_position_ids = list(range(len(position_ids), len(position_ids) + len(new_input_ids)))

            new_loss_masks = [loss_mask_val] * len(new_input_ids)
            input_ids += new_input_ids
            attention_mask += new_attention_mask
            position_ids += new_position_ids
            loss_masks += new_loss_masks

        input_ids.append(59253)  # EOS
        attention_mask.append(1)
        position_ids.append(len(position_ids))
        loss_masks.append(True)

        padding_length = max(0, max_length - len(input_ids))

        # Left padding with batch
        input_ids = [tokenizer.pad_token_id] * padding_length + input_ids[-max_length:]
        attention_mask = [0] * padding_length + attention_mask[-max_length:]
        position_ids = [0] * padding_length + position_ids[-max_length:]
        loss_masks = [False] * padding_length + loss_masks[-max_length:]

        labels = []
        for input_id, mask in zip(input_ids, loss_masks):
            if mask:
                labels.append(input_id)
            else:
                labels.append(-100)

        batched_input_ids.append(input_ids[:max_length])
        batched_attention_mask.append(attention_mask[:max_length])
        batched_position_ids.append(position_ids[:max_length])
        batched_labels.append(labels[:max_length])
        if len(pixel_values) > 0:
            batched_images.append(pixel_values[0][0])
        else:
            batched_images.append(torch.zeros([1, 1, 3, 672, 672]))

    del (
        batched_conv,
        conv,
        input_ids,
        attention_mask,
        position_ids,
        loss_masks,
        message,
        new_input_ids,
        new_loss_masks,
        labels,
        input_id,
        mask,
    )
    torch.cuda.empty_cache()

    return {
        "input_ids": batched_input_ids,
        "attention_mask": batched_attention_mask,
        "position_ids": batched_position_ids,
        "labels": batched_labels,
        "pixel_values": batched_images,
    }


def process_batch_eval(
    batch: Mapping[str, Sequence],
    tokenizer: PreTrainedTokenizer,
    processor,
    max_input_length: int,
    max_output_length: int,
) -> dict[str, list]:
    batched_conv = batch["messages"]
    batched_input_ids = []
    batched_attention_mask = []
    batched_position_ids = []
    batched_output_ids = []
    batched_images = []

    for conv in batched_conv:
        if conv[0]["content"][0].get("image"):
            image = Image.open(conv[0]["content"][0]["image"])

        new_input_ids_all = tokenizer.apply_chat_template(
            conv,
            add_generation_prompt=False,
            tokenize=True,
            padding=True,
            return_dict=True,
            return_tensors="pt",
        )

        input_ids = new_input_ids_all["input_ids"][0].tolist()
        attention_mask = new_input_ids_all["attention_mask"][0].tolist()
        position_ids = list(range(len(input_ids)))

        dialogue_parts = [0]
        for idx, token_id in enumerate(input_ids):
            if token_id == 59254:
                dialogue_parts.append(idx + 1)

        if not dialogue_parts or dialogue_parts[-1] != len(input_ids):
            dialogue_parts.append(len(input_ids))

        # Split the conversation into multiple dialogue segments
        for end_idx in range(1, len(dialogue_parts)):
            input_segment = input_ids[: dialogue_parts[end_idx]]
            attention_segment = attention_mask[: dialogue_parts[end_idx]]
            position_segment = position_ids[: dialogue_parts[end_idx]]
            output_segment = input_ids[dialogue_parts[end_idx - 1] : dialogue_parts[end_idx]]
            output_segment.append(59253)  # Add EOS token

            # Left Padding
            padding_length = max(0, max_input_length - len(input_segment))
            input_segment = [tokenizer.pad_token_id] * padding_length + input_segment[:max_input_length]
            attention_segment = [0] * padding_length + attention_segment[:max_input_length]
            position_segment = [0] * padding_length + position_segment[:max_input_length]
            output_segment = [tokenizer.pad_token_id] * padding_length + output_segment[:max_output_length]

            batched_input_ids.append(input_segment[:max_input_length])
            batched_attention_mask.append(attention_segment[:max_input_length])
            batched_position_ids.append(position_segment[:max_input_length])
            batched_output_ids.append(output_segment[:max_output_length])
            if conv[0]["content"][0].get("image"): 
                batched_images.append(torch.tensor(processor(image).pixel_values)[0])
            else:
                batched_images.append(torch.zeros([1, 1, 3, 672, 672]))

    del batched_conv, input_ids, attention_mask, position_ids, new_input_ids_all, output_segment
    torch.cuda.empty_cache()

    return {
        "input_ids": batched_input_ids,
        "attention_mask": batched_attention_mask,
        "position_ids": batched_position_ids,
        "output_ids": batched_output_ids,
        "pixel_values": batched_images,
    }


def load_tokenizer_and_model(
    model_dir: str,
    peft_config: Optional[PeftConfig] = None,
):
    tokenizer = AutoTokenizer.from_pretrained(model_dir, trust_remote_code=True, padding_side="left")
    processor = AutoImageProcessor.from_pretrained(model_dir, trust_remote_code=True, dtype=torch.bfloat16)
    if peft_config is not None:
        model = AutoModelForCausalLM.from_pretrained(model_dir, trust_remote_code=True, torch_dtype=torch.bfloat16)
        model = get_peft_model(model, peft_config)
        model.print_trainable_parameters()
    else:
        model = AutoModelForCausalLM.from_pretrained(model_dir, trust_remote_code=True, torch_dtype=torch.bfloat16)
    return tokenizer, model, processor


#    def compute_metrics(eval_preds: EvalPrediction, tokenizer):
#        batched_pred_ids, batched_label_ids = eval_preds
#        batched_pred_ids[batched_pred_ids == -100] = tokenizer.pad_token_id
#        batched_label_ids[batched_label_ids == -100] = tokenizer.pad_token_id
#        metrics_dct = {"rouge-1": [], "rouge-2": [], "rouge-l": [], "bleu-4": []}
#        for pred_ids, label_ids in zip(batched_pred_ids, batched_label_ids):
#            pred_txt = tokenizer.decode(pred_ids, skip_special_tokens=True).strip()
#            label_txt = tokenizer.decode(label_ids, skip_special_tokens=True).strip()
#            pred_tokens = list(jieba.cut(pred_txt))
#            label_tokens = list(jieba.cut(label_txt))
#            rouge = Rouge()
#            scores = rouge.get_scores(" ".join(pred_tokens), " ".join(label_tokens))
#            for k, v in scores[0].items():
#                metrics_dct[k].append(round(v["f"] * 100, 4))
#            metrics_dct["bleu-4"].append(
#                sentence_bleu([label_tokens], pred_tokens, smoothing_function=SmoothingFunction().method3)
#            )
#        return {k: np.mean(v) for k, v in metrics_dct.items()}


@app.command()
def main(
    data_dir: Annotated[str, typer.Argument(help="")],
    model_dir: Annotated[
        str,
        typer.Argument(
            help="A string that specifies the model id of a pretrained model configuration hosted on huggingface.co, or a path to a directory containing a model configuration file."
        ),
    ],
    config_file: Annotated[str, typer.Argument(help="")],
    auto_resume_from_checkpoint: str = typer.Argument(
        default="",
        help="If entered as yes, automatically use the latest save checkpoint. If it is a numerical example 12 15, use the corresponding save checkpoint. If the input is no, restart training",
    ),
):
    ft_config = FinetuningConfig.from_file(config_file)
    tokenizer, model, processor = load_tokenizer_and_model(model_dir, peft_config=ft_config.peft_config)


    if ft_config.freezeV:
        for param in model.base_model.model.model.vision.parameters():
            param.requires_grad = False
    data_manager = DataManager(data_dir, ft_config.data_config)

    train_dataset = data_manager.get_dataset(
        Split.TRAIN,
        functools.partial(
            process_batch,
            tokenizer=tokenizer,
            processor=processor,
            max_input_length=ft_config.max_input_length,
            max_output_length=ft_config.max_output_length,
        ),
        batched=True,
    )

    val_dataset = data_manager.get_dataset(
        Split.VALIDATION,
        functools.partial(
            process_batch_eval,
            tokenizer=tokenizer,
            processor=processor,
            max_input_length=ft_config.max_input_length,
            max_output_length=ft_config.max_output_length,
        ),
        batched=True,
    )

    test_dataset = data_manager.get_dataset(
        Split.TEST,
        functools.partial(
            process_batch_eval,
            tokenizer=tokenizer,
            processor=processor,
            max_input_length=ft_config.max_input_length,
            max_output_length=ft_config.max_output_length,
        ),
        batched=True,
    )

    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()

    trainer = Seq2SeqTrainer(
        model=model,
        args=ft_config.training_args,
        data_collator=DataCollatorForSeq2Seq(
            tokenizer=tokenizer,
            padding="longest",
            return_tensors="pt",
        ),
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        # compute_metrics=functools.partial(compute_metrics, tokenizer=tokenizer),
    )

    if auto_resume_from_checkpoint.upper() == "" or auto_resume_from_checkpoint is None:
        trainer.train()
    else:
        output_dir = ft_config.training_args.output_dir
        dirlist = os.listdir(output_dir)
        checkpoint_sn = 0
        for checkpoint_str in dirlist:
            if checkpoint_str.find("checkpoint") > 0 and checkpoint_str.find("tmp") == -1:
                checkpoint = int(checkpoint_str.replace("checkpoint-", ""))
                if checkpoint > checkpoint_sn:
                    checkpoint_sn = checkpoint
        if auto_resume_from_checkpoint.upper() == "YES":
            if checkpoint_sn > 0:
                model.gradient_checkpointing_enable()
                model.enable_input_require_grads()
                checkpoint_directory = os.path.join(output_dir, "checkpoint-" + str(checkpoint_sn))
                print("resume checkpoint from checkpoint-" + str(checkpoint_sn))
                trainer.train(resume_from_checkpoint=checkpoint_directory)
            else:
                trainer.train()
        else:
            if auto_resume_from_checkpoint.isdigit():
                if int(auto_resume_from_checkpoint) > 0:
                    checkpoint_sn = int(auto_resume_from_checkpoint)
                    model.gradient_checkpointing_enable()
                    model.enable_input_require_grads()
                    checkpoint_directory = os.path.join(output_dir, "checkpoint-" + str(checkpoint_sn))
                    print("resume checkpoint from checkpoint-" + str(checkpoint_sn))
                    trainer.train(resume_from_checkpoint=checkpoint_directory)
            else:
                print(
                    auto_resume_from_checkpoint,
                    "The specified checkpoint sn("
                    + auto_resume_from_checkpoint
                    + ") has not been saved. Please search for the correct checkpoint in the model output directory",
                )

    if test_dataset is not None:
        trainer.predict(test_dataset)


if __name__ == "__main__":
    app()
