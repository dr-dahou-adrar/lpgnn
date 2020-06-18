from argparse import ArgumentParser

import torch
from colorama import Fore, Style
from pytorch_lightning import seed_everything
from torch_geometric.nn import GCNConv
from torch_geometric.utils import degree

from datasets import load_dataset, get_available_datasets
from loggers import PandasLogger
from privacy import privatize, get_available_mechanisms


class ErrorEstimation:
    def __init__(self, data, raw_features, device='cuda'):
        self.data = data
        alpha = raw_features.min(dim=0)[0]
        beta = raw_features.max(dim=0)[0]
        self.delta = beta - alpha

        self.model = GCNConv(data.num_features, data.num_features, cached=True)
        if device == 'cuda' and torch.cuda.is_available():
            self.model = self.model.cuda()
        self.model.weight.data.copy_(torch.eye(data.num_features))  # identity transformation
        self.gc = self.model(raw_features, data.edge_index)

    @torch.no_grad()
    def run(self, logger):
        # calculate error
        gc_hat = self.model(self.data.x, self.data.edge_index)
        diff = (self.gc - gc_hat) / self.delta
        diff[:, (self.delta == 0)] = 0  # avoid division by zero
        error = torch.norm(diff, p=1, dim=1) / self.data.num_features

        # obtain node degrees
        row, col = self.data.edge_index
        deg = degree(row, self.data.num_nodes)

        # log results
        logger.log_metrics({'test_result': list(zip(error.cpu().numpy(), deg.cpu().numpy()))})


def error_estimation(dataset, method, eps, repeats, logger, device):
    assert method in get_available_mechanisms()

    if isinstance(dataset, str):
        dataset = load_dataset(dataset, device=device)

    for run in range(repeats):
        params = {
            'task': 'error',
            'dataset': dataset.name,
            'method': method,
            'eps': eps,
            'run': run
        }

        params_str = ' | '.join([f'{key}={val}' for key, val in params.items()])
        print(Fore.BLUE + params_str + Style.RESET_ALL)
        logger.log_params(params)

        data = privatize(dataset, method=method, eps=eps, pfr=1)
        ErrorEstimation(data=data, raw_features=dataset.x, device=device).run(logger)


def main():
    seed_everything(12345)

    # default and valid options
    list_datasets = get_available_datasets()
    list_methods = get_available_mechanisms()
    default_output_dir = './results'
    default_device = 'cuda'

    # parse arguments
    parser = ArgumentParser()
    parser.add_argument('-d', '--datasets', nargs='*', choices=list_datasets)
    parser.add_argument('-m', '--methods',  nargs='*', choices=list_methods)
    parser.add_argument('-e', '--eps',      nargs='*', type=float, dest='eps_list')
    parser.add_argument('-r', '--repeats',      type=int, default=1)
    parser.add_argument('-o', '--output-dir',   type=str, default=default_output_dir)
    parser.add_argument('--device',             type=str, default=default_device)
    args = parser.parse_args()
    print(args)

    # get results
    for dataset_name in args.datasets:
        dataset = load_dataset(dataset_name).to(args.device)
        for method in args.methods:
            experiment_name = f'error_{dataset_name}_{method}'
            with PandasLogger(
                output_dir=args.output_dir,
                experiment_name=experiment_name,
                write_mode='truncate'
            ) as logger:
                for eps in args.eps:
                    error_estimation(
                        dataset=dataset,
                        method=method,
                        eps=eps,
                        repeats=args.repeats,
                        logger=logger,
                        device=args.device
                    )


if __name__ == '__main__':
    main()