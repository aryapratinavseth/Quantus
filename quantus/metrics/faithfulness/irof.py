"""This module contains the implementation of the Iterative Removal of Features metric."""

# This file is part of Quantus.
# Quantus is free software: you can redistribute it and/or modify it under the terms of the GNU Lesser General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
# Quantus is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more details.
# You should have received a copy of the GNU Lesser General Public License along with Quantus. If not, see <https://www.gnu.org/licenses/>.
# Quantus project URL: <https://github.com/understandable-machine-intelligence-lab/Quantus>.
import sys
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from quantus.functions.perturb_func import baseline_replacement_by_mask
from quantus.helpers import asserts, utils, warn
from quantus.helpers.enums import (
    DataType,
    EvaluationCategory,
    ModelType,
    ScoreDirection,
)
from quantus.helpers.model.model_interface import ModelInterface
from quantus.helpers.perturbation_utils import make_perturb_func
from quantus.metrics.base import Metric

if sys.version_info >= (3, 8):
    from typing import final
else:
    from typing_extensions import final


@final
class IROF(Metric[List[float]]):
    """
    Implementation of IROF (Iterative Removal of Features) by Rieger at el., 2020.

    The metric computes the area over the curve per class for sorted mean importances
    of feature segments (superpixels) as they are iteratively removed (and prediction scores are collected),
    averaged over several test samples.

    Assumptions:
        - The original metric definition relies on image-segmentation functionality. Therefore, only apply the
        metric to 3-dimensional (image) data. To extend the applicablity to other data domains,
        adjustments to the current implementation might be necessary.

    References:
        1) Laura Rieger and Lars Kai Hansen. "Irof: a low resource evaluation metric for
        explanation methods." arXiv preprint arXiv:2003.08747 (2020).

    Attributes:
        -  _name: The name of the metric.
        - _data_applicability: The data types that the metric implementation currently supports.
        - _models: The model types that this metric can work with.
        - score_direction: How to interpret the scores, whether higher/ lower values are considered better.
        - evaluation_category: What property/ explanation quality that this metric measures.
    """

    name = "IROF"
    data_applicability = {DataType.IMAGE}
    model_applicability = {ModelType.TORCH, ModelType.TF}
    score_direction = ScoreDirection.HIGHER
    evaluation_category = EvaluationCategory.FAITHFULNESS

    def __init__(
        self,
        segmentation_method: str = "slic",
        abs: bool = False,
        normalise: bool = True,
        normalise_func: Optional[Callable[[np.ndarray], np.ndarray]] = None,
        normalise_func_kwargs: Optional[Dict[str, Any]] = None,
        perturb_func: Optional[Callable] = None,
        perturb_baseline: str = "mean",
        perturb_func_kwargs: Optional[Dict[str, Any]] = None,
        return_aggregate: bool = True,
        aggregate_func: Optional[Callable] = None,
        default_plot_func: Optional[Callable] = None,
        disable_warnings: bool = False,
        display_progressbar: bool = False,
        **kwargs,
    ):
        """
        Parameters
        ----------
        segmentation_method: string
            Image segmentation method:'slic' or 'felzenszwalb', default="slic".
        abs: boolean
            Indicates whether absolute operation is applied on the attribution, default=False.
        normalise: boolean
            Indicates whether normalise operation is applied on the attribution, default=True.
        normalise_func: callable
            Attribution normalisation function applied in case normalise=True.
            If normalise_func=None, the default value is used, default=normalise_by_max.
        normalise_func_kwargs: dict
            Keyword arguments to be passed to normalise_func on call, default={}.
        perturb_func: callable
            Input perturbation function. If None, the default value is used,
            default=baseline_replacement_by_indices.
        perturb_baseline: string
            Indicates the type of baseline: "mean", "random", "uniform", "black" or "white",
            default="mean".
        perturb_func_kwargs: dict
            Keyword arguments to be passed to perturb_func, default={}.
        return_aggregate: boolean
            Indicates if an aggregated score should be computed over all instances.
        aggregate_func: callable
            Callable that aggregates the scores given an evaluation call.
        default_plot_func: callable
            Callable that plots the metrics result.
        disable_warnings: boolean
            Indicates whether the warnings are printed, default=False.
        display_progressbar: boolean
            Indicates whether a tqdm-progress-bar is printed, default=False.
        kwargs: optional
            Keyword arguments.
        """
        super().__init__(
            abs=abs,
            normalise=normalise,
            normalise_func=normalise_func,
            normalise_func_kwargs=normalise_func_kwargs,
            return_aggregate=return_aggregate,
            aggregate_func=aggregate_func,
            default_plot_func=default_plot_func,
            display_progressbar=display_progressbar,
            disable_warnings=disable_warnings,
            **kwargs,
        )

        if perturb_func is None:
            perturb_func = baseline_replacement_by_mask

        # Save metric-specific attributes.
        self.segmentation_method = segmentation_method
        self.nr_channels = None
        self.perturb_func = make_perturb_func(perturb_func, perturb_func_kwargs, perturb_baseline=perturb_baseline)

        # Asserts and warnings.
        if not self.disable_warnings:
            warn.warn_parameterisation(
                metric_name=self.__class__.__name__,
                sensitive_params=(
                    "baseline value 'perturb_baseline' and the method to segment "
                    "the image 'segmentation_method' (including all its associated"
                    " hyperparameters), also, IROF only works with image data"
                ),
                data_domain_applicability=(
                    f"Also, the current implementation only works for 3-dimensional (image) data."
                ),
                citation=(
                    "Rieger, Laura, and Lars Kai Hansen. 'Irof: a low resource evaluation metric "
                    "for explanation methods.' arXiv preprint arXiv:2003.08747 (2020)"
                ),
            )

    def __call__(
        self,
        model,
        x_batch: np.array,
        y_batch: np.array,
        a_batch: Optional[np.ndarray] = None,
        s_batch: Optional[np.ndarray] = None,
        channel_first: Optional[bool] = None,
        explain_func: Optional[Callable] = None,
        explain_func_kwargs: Optional[Dict] = None,
        model_predict_kwargs: Optional[Dict] = None,
        softmax: Optional[bool] = True,
        device: Optional[str] = None,
        batch_size: int = 64,
        **kwargs,
    ) -> List[float]:
        """
        This implementation represents the main logic of the metric and makes the class object callable.
        It completes instance-wise evaluation of explanations (a_batch) with respect to input data (x_batch),
        output labels (y_batch) and a torch or tensorflow model (model).

        Calls general_preprocess() with all relevant arguments, calls
        () on each instance, and saves results to evaluation_scores.
        Calls custom_postprocess() afterwards. Finally returns evaluation_scores.

        Parameters
        ----------
        model: torch.nn.Module, tf.keras.Model
            A torch or tensorflow model that is subject to explanation.
        x_batch: np.ndarray
            A np.ndarray which contains the input data that are explained.
        y_batch: np.ndarray
            A np.ndarray which contains the output labels that are explained.
        a_batch: np.ndarray, optional
            A np.ndarray which contains pre-computed attributions i.e., explanations.
        s_batch: np.ndarray, optional
            A np.ndarray which contains segmentation masks that matches the input.
        channel_first: boolean, optional
            Indicates of the image dimensions are channel first, or channel last.
            Inferred from the input shape if None.
        explain_func: callable
            Callable generating attributions.
        explain_func_kwargs: dict, optional
            Keyword arguments to be passed to explain_func on call.
        model_predict_kwargs: dict, optional
            Keyword arguments to be passed to the model's predict method.
        softmax: boolean
            Indicates whether to use softmax probabilities or logits in model prediction.
            This is used for this __call__ only and won't be saved as attribute. If None, self.softmax is used.
        device: string
            Indicated the device on which a torch.Tensor is or will be allocated: "cpu" or "gpu".
        kwargs: optional
            Keyword arguments.

        Returns
        -------
        evaluation_scores: list
            a list of Any with the evaluation scores of the concerned batch.

        Examples:
        --------
            # Minimal imports.
            >> import quantus
            >> from quantus import LeNet
            >> import torch

            # Enable GPU.
            >> device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

            # Load a pre-trained LeNet classification model (architecture at quantus/helpers/models).
            >> model = LeNet()
            >> model.load_state_dict(torch.load("tutorials/assets/pytests/mnist_model"))

            # Load MNIST datasets and make loaders.
            >> test_set = torchvision.datasets.MNIST(root='./sample_data', download=True)
            >> test_loader = torch.utils.data.DataLoader(test_set, batch_size=24)

            # Load a batch of inputs and outputs to use for XAI evaluation.
            >> x_batch, y_batch = iter(test_loader).next()
            >> x_batch, y_batch = x_batch.cpu().numpy(), y_batch.cpu().numpy()

            # Generate Saliency attributions of the test set batch of the test set.
            >> a_batch_saliency = Saliency(model).attribute(inputs=x_batch, target=y_batch, abs=True).sum(axis=1)
            >> a_batch_saliency = a_batch_saliency.cpu().numpy()

            # Initialise the metric and evaluate explanations by calling the metric instance.
            >> metric = Metric(abs=True, normalise=False)
            >> scores = metric(model=model, x_batch=x_batch, y_batch=y_batch, a_batch=a_batch_saliency)
        """
        return super().__call__(
            model=model,
            x_batch=x_batch,
            y_batch=y_batch,
            a_batch=a_batch,
            s_batch=s_batch,
            custom_batch=None,
            channel_first=channel_first,
            explain_func=explain_func,
            explain_func_kwargs=explain_func_kwargs,
            softmax=softmax,
            device=device,
            model_predict_kwargs=model_predict_kwargs,
            batch_size=batch_size,
            **kwargs,
        )

    def custom_preprocess(
        self,
        x_batch: np.ndarray,
        **kwargs,
    ) -> None:
        """
        Implementation of custom_preprocess_batch.

        Parameters
        ----------
        model: torch.nn.Module, tf.keras.Model
            A torch or tensorflow model e.g., torchvision.models that is subject to explanation.
        x_batch: np.ndarray
            A np.ndarray which contains the input data that are explained.
        y_batch: np.ndarray
            A np.ndarray which contains the output labels that are explained.
        a_batch: np.ndarray, optional
            A np.ndarray which contains pre-computed attributions i.e., explanations.
        s_batch: np.ndarray, optional
            A np.ndarray which contains segmentation masks that matches the input.
        custom_batch: any
            Gives flexibility ot the user to use for evaluation, can hold any variable.

        Returns
        -------
        None
        """
        # Infer number of input channels.
        self.nr_channels = x_batch.shape[1]

    @property
    def get_aoc_score(self):
        """Calculate the area over the curve (AOC) score for several test samples."""
        return np.mean(self.evaluation_scores)

    def evaluate_batch(
        self,
        model: ModelInterface,
        x_batch: np.ndarray,
        y_batch: np.ndarray,
        a_batch: np.ndarray,
        **kwargs,
    ) -> List[float]:
        """
        This method performs XAI evaluation on a single batch of explanations.
        For more information on the specific logic, we refer the metric’s initialisation docstring.

        Parameters
        ----------
        model: ModelInterface
            A ModelInterface that is subject to explanation.
        x_batch: np.ndarray
            The input to be evaluated on a batch-basis.
        y_batch: np.ndarray
            The output to be evaluated on a batch-basis.
        a_batch: np.ndarray
            The explanation to be evaluated on a batch-basis.
        kwargs:
            Unused.

        Returns
        -------
        scores_batch:
            The evaluation results.
        """
        # Prepare shapes. Expand a_batch if not the same shape
        if x_batch.shape != a_batch.shape:
            a_batch = np.broadcast_to(a_batch, x_batch.shape)

        # Flatten the attributions.
        batch_size = a_batch.shape[0]

        # Predict on input.
        x_input = model.shape_input(x_batch, x_batch.shape, channel_first=True, batched=True)
        y_pred = model.predict(x_input)[np.arange(batch_size), y_batch]

        # Segment image.
        segments_batch = []
        s_indices_batch_list = []
        for x, a in zip(x_batch, a_batch):
            segments = utils.get_superpixel_segments(
                img=np.moveaxis(x, 0, -1).astype("double"),
                segmentation_method=self.segmentation_method,
            )
            nr_segments = len(np.unique(segments))
            asserts.assert_nr_segments(nr_segments=nr_segments)
            segments_batch.append(segments)

            # Calculate average attribution of each segment.
            att_segs = np.zeros(nr_segments)
            for i, s in enumerate(range(nr_segments)):
                att_segs[i] = np.mean(a[:, segments == s])

            # Sort segments based on the mean attribution (descending order).
            s_indices = np.argsort(-att_segs)
            s_indices_batch_list.append(s_indices)
        segments_batch = np.stack(segments_batch, axis=0)
        max_segments_len = max([len(s_indices) for s_indices in s_indices_batch_list])
        mask_preds_batch = np.array(
            [[1.0] * len(s_indices) + [0] * (max_segments_len - len(s_indices)) for s_indices in s_indices_batch_list]
        )
        s_indices_batch = np.array(
            [s_indices.tolist() + [-1] * (max_segments_len - len(s_indices)) for s_indices in s_indices_batch_list]
        )

        preds = []
        x_perturbed = x_batch.copy()
        for s_indices_segment in s_indices_batch.T:
            # Perturb input by indices of attributions.
            mask = (segments_batch == s_indices_segment[:, None, None])[:, None]

            x_new_perturbed = self.perturb_func(
                arr=x_perturbed,
                mask=mask,
            )
            # Check if the perturbation caused change
            for x_element, x_perturbed_element in zip(x_new_perturbed, x_perturbed):
                warn.warn_perturbation_caused_no_change(x=x_element, x_perturbed=x_perturbed_element)

            # Predict on perturbed input x.
            x_input = model.shape_input(x_new_perturbed, x_new_perturbed.shape, channel_first=True, batched=True)
            y_pred_perturb = model.predict(x_input)[np.arange(batch_size), y_batch]

            # Normalise the scores to be within range [0, 1].
            preds.append(y_pred_perturb / y_pred)
            x_perturbed = x_new_perturbed
        preds = np.stack(preds, axis=1) * mask_preds_batch
        # Calculate the area over the curve (AOC) score.
        aoc = mask_preds_batch.sum(-1) - utils.calculate_auc(preds, batched=True)
        return aoc
