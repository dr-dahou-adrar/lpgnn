import os
import sys
from abc import abstractmethod
from contextlib import contextmanager
from functools import partial

import torch
from pytorch_lightning import Trainer, seed_everything
from pytorch_lightning.callbacks import EarlyStopping
from torch_geometric.utils import degree

from datasets import load_dataset

try:
    from tsnecuda import TSNE
except ImportError:
    from sklearn.manifold import TSNE

from gnn import GConv
from models import GCNClassifier, VGAELinkPredictor, ResultLogger
from params import get_params


class Task:
    task_name = None

    def __init__(self, data, model_name):
        self.model_name = model_name
        self.data = data

    @abstractmethod
    def run(self): pass


class LearningTask(Task):
    def __init__(self, task_name, data, model_name):
        super().__init__(data, model_name)
        self.task_name = task_name
        self.model = self.get_model()

    def get_model(self):
        Model = {
            ('node', 'gcn'): partial(GCNClassifier),
            ('link', 'gcn'): partial(VGAELinkPredictor),
        }
        return Model[self.task_name, self.model_name](
            data=self.data,
            **get_params(
                section='model',
                task=self.task_name,
                dataset=self.data.name,
                model_name=self.model_name
            )
        )

    def run(self):
        logger = ResultLogger()
        early_stop_callback = EarlyStopping(**get_params(
            section='early-stop',
            task=self.task_name,
            dataset=self.data.name,
            model_name=self.model_name
        ))

        trainer = Trainer(
            gpus=1,
            checkpoint_callback=False,
            logger=logger,
            weights_summary=None,
            deterministic=True,
            early_stop_callback=early_stop_callback,
            **get_params(
                section='trainer',
                task=self.task_name,
                dataset=self.data.name,
                model_name=self.model_name
            )
        )
        trainer.fit(self.model)
        with self.silence_stdout(): trainer.test()
        return logger.result

    @contextmanager
    def silence_stdout(self):
        new_target = open(os.devnull, "w")
        old_target = sys.stdout
        sys.stdout = new_target
        try:
            yield new_target
        finally:
            sys.stdout = old_target


class ErrorEstimation(Task):
    task_name = 'error'

    def __init__(self, data, orig_features):
        super().__init__(data, 'gcn')
        self.model = GConv(cached=False)
        self.gc = self.model(orig_features, data.edge_index)

    @torch.no_grad()
    def run(self, **kwargs):
        gc_hat = self.model(self.data.x, self.data.edge_index)
        diff = (self.gc - gc_hat) / self.data.delta
        diff[:, (self.data.delta == 0)] = 0  # eliminate division by zero
        error = torch.norm(diff, p=1, dim=1) / diff.shape[1]
        deg = self.get_degree(self.data)
        return list(zip(error.cpu().numpy(), deg.cpu().numpy()))

    @staticmethod
    def get_degree(data):
        row, col = data.edge_index
        return degree(row, data.num_nodes)


def main():
    seed_everything()
    dataset = load_dataset('cora', split_edges=True).to('cuda')
    task = LearningTask(task_name='link', data=dataset, model_name='gcn')
    print(task.run())


if __name__ == '__main__':
    main()
