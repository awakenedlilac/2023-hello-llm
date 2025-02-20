"""
Laboratory work.

Working with Large Language Models.
"""
# pylint: disable=too-few-public-methods, undefined-variable, too-many-arguments, super-init-not-called, duplicate-code
from collections import namedtuple
from pathlib import Path
from typing import Iterable, Sequence

from datasets import load_dataset
from transformers import AutoModelForQuestionAnswering, AutoTokenizer

try:
    import torch
    from torch.utils.data.dataset import Dataset
    from torchinfo import summary
except ImportError:
    print('Library "torch" not installed. Failed to import.')
    Dataset = dict
    torch = namedtuple('torch', 'no_grad')(lambda: lambda fn: fn)  # type: ignore

try:
    from pandas import DataFrame
except ImportError:
    print('Library "pandas" not installed. Failed to import.')
    DataFrame = dict  # type: ignore

from core_utils.llm.llm_pipeline import AbstractLLMPipeline
from core_utils.llm.metrics import Metrics
from core_utils.llm.raw_data_importer import AbstractRawDataImporter
from core_utils.llm.raw_data_preprocessor import AbstractRawDataPreprocessor, ColumnNames
from core_utils.llm.task_evaluator import AbstractTaskEvaluator
from core_utils.llm.time_decorator import report_time


class RawDataImporter(AbstractRawDataImporter):
    """
    A class that imports the HuggingFace dataset.
    """

    @report_time
    def obtain(self) -> None:
        """
        Download a dataset.

        Raises:
            TypeError: In case of downloaded dataset is not pd.DataFrame
        """
        self._raw_data = load_dataset(self._hf_name,
                                      name='wikiomnia_ruGPT3_filtered',
                                      split='train').to_pandas()


class RawDataPreprocessor(AbstractRawDataPreprocessor):
    """
    A class that analyzes and preprocesses a dataset.
    """

    def analyze(self) -> dict:
        """
        Analyze a dataset.

        Returns:
            dict: Dataset key properties
        """
        analysis = {'dataset_number_of_samples': len(self._raw_data),
                    'dataset_columns': self._raw_data.shape[1],
                    'dataset_duplicates': self._raw_data.duplicated().sum(),
                    'dataset_empty_rows': self._raw_data.isna().sum().sum(),
                    'dataset_sample_min_len': min(len(min(self._raw_data['summary'], key=len)),
                                                  len(min(self._raw_data['question'], key=len))),
                    'dataset_sample_max_len': max(len(max(self._raw_data['summary'], key=len)),
                                                  len(max(self._raw_data['question'], key=len)))}

        return analysis

    @report_time
    def transform(self) -> None:
        """
        Apply preprocessing transformations to the raw dataset.
        """
        self._data = self._raw_data.rename(
            columns={'summary': ColumnNames.CONTEXT.value,
                     'answer': ColumnNames.TARGET.value}).reset_index(drop=True)
        self._data.dropna().reset_index()


class TaskDataset(Dataset):
    """
    A class that converts pd.DataFrame to Dataset and works with it.
    """

    def __init__(self, data: DataFrame) -> None:
        """
        Initialize an instance of TaskDataset.

        Args:
            data (pandas.DataFrame): Original data
        """
        self._data = data

    def __len__(self) -> int:
        """
        Return the number of items in the dataset.

        Returns:
            int: The number of items in the dataset
        """
        return len(self._data)

    def __getitem__(self, index: int) -> tuple[str, ...]:
        """
        Retrieve an item from the dataset by index.

        Args:
            index (int): Index of sample in dataset

        Returns:
            tuple[str, ...]: The item to be received
        """
        return str(self._data[ColumnNames.QUESTION.value].iloc[index])

    @property
    def data(self) -> DataFrame:
        """
        Property with access to preprocessed DataFrame.

        Returns:
            pandas.DataFrame: Preprocessed DataFrame
        """
        return self._data


class LLMPipeline(AbstractLLMPipeline):
    """
    A class that initializes a model, analyzes its properties and infers it.
    """

    def __init__(
            self,
            model_name: str,
            dataset: TaskDataset,
            max_length: int,
            batch_size: int,
            device: str
    ) -> None:
        """
        Initialize an instance of LLMPipeline.

        Args:
            model_name (str): The name of the pre-trained model
            dataset (TaskDataset): The dataset used
            max_length (int): The maximum length of generated sequence
            batch_size (int): The size of the batch inside DataLoader
            device (str): The device for inference
        """
        super().__init__(model_name,
                         dataset,
                         max_length,
                         batch_size,
                         device)

        self._model_name = model_name
        self._max_length = max_length
        self._model = AutoModelForQuestionAnswering.from_pretrained(self._model_name)
        self._tokenizer = AutoTokenizer.from_pretrained(self._model_name)

    def analyze_model(self) -> dict:
        """
        Analyze model computing properties.

        Returns:
            dict: Properties of a model
        """
        tensor_data = torch.ones(1,
                                 self._model.config.max_position_embeddings,
                                 dtype=torch.long)

        input_data = {"input_ids": tensor_data,
                      "attention_mask": tensor_data}

        model_statistics = summary(self._model,
                                   input_data=input_data,
                                   verbose=False)

        size, num_trainable_params, last_layer = (model_statistics.total_param_bytes,
                                                  model_statistics.trainable_params,
                                                  model_statistics.summary_list[-1].output_size)

        return {"input_shape": {"input_ids": [tensor_data.shape[0], tensor_data.shape[1]],
                                "attention_mask": [tensor_data.shape[0], tensor_data.shape[1]]},
                "embedding_size": self._model.config.max_position_embeddings,
                "output_shape": last_layer,
                "num_trainable_params": num_trainable_params,
                "vocab_size": self._model.config.vocab_size,
                "size": size,
                "max_context_length": self._model.config.max_length}

    @report_time
    def infer_sample(self, sample: tuple[str, ...]) -> str | None:
        """
        Infer model on a single sample.

        Args:
            sample (tuple[str, ...]): The given sample for inference with model

        Returns:
            str | None: A prediction
        """
        if self._model is None or self._tokenizer is None:
            return None

        tokens = self._tokenizer(sample[0],
                                 sample[1],
                                 max_length=120,
                                 padding=True,
                                 return_tensors='pt',
                                 truncation=True)

        outputs = self._model(**tokens)

        answer_start_index = outputs.start_logits.argmax()
        answer_end_index = outputs.end_logits.argmax()

        predict_answer_tokens = tokens.input_ids[0, answer_start_index: answer_end_index + 1]
        result = self._tokenizer.decode(predict_answer_tokens)

        return result

    @report_time
    def infer_dataset(self) -> DataFrame:
        """
        Infer model on a whole dataset.

        Returns:
            pd.DataFrame: Data with predictions
        """

    @torch.no_grad()
    def _infer_batch(self, sample_batch: Sequence[tuple[str, ...]]) -> list[str]:
        """
        Infer model on a single batch.

        Args:
            sample_batch (Sequence[tuple[str, ...]]): Batch to infer the model

        Returns:
            list[str]: Model predictions as strings
        """


class TaskEvaluator(AbstractTaskEvaluator):
    """
    A class that compares prediction quality using the specified metric.
    """

    def __init__(self, data_path: Path, metrics: Iterable[Metrics]) -> None:
        """
        Initialize an instance of Evaluator.

        Args:
            data_path (pathlib.Path): Path to predictions
            metrics (Iterable[Metrics]): List of metrics to check
        """

    @report_time
    def run(self) -> dict | None:
        """
        Evaluate the predictions against the references using the specified metric.

        Returns:
            dict | None: A dictionary containing information about the calculated metric
        """
