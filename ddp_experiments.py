import argparse
import importlib
import os
import submitit
import sys
import torch
import uuid

from pathlib import Path
from typing import List


def parse_args(args: List[str]=None):
    parser = argparse.ArgumentParser(description='Submitit for PyTorch Distributed Benchmark', add_help=False)

    parser.add_argument(
        "--ngpus",
        default=8,
        type=int,
        help="Number of gpus to request on each node"
    )

    parser.add_argument(
        "--nodes",
        default=1,
        type=int,
        help="Number of nodes to request"
    )

    parser.add_argument(
        "--timeout",
        default=1440,
        type=int,
        help="Duration of the job"
    )

    parser.add_argument(
        "--profiler",
        default=False,
        type=bool,
        help="Measure with PyTorch Profiler. Disabled by default, as it crashes on AWS"
    )

    parser.add_argument(
        "--partition",
        default="train",
        type=str,
        help="The Slurm partition to submit to"
    )

    parser.add_argument(
        "--job_dir",
        default=os.getcwd(),
        type=str,
        help="A shared folder across all worker processes"
    )

    parser.add_argument(
        "--model",
        type=str,
        default="torchbenchmark.models.hf_Bert.Model",
        help="specify the model to experiment with, by default uses e2e_models.hf_bert"
    )
    parser.add_argument(
        "--batch_size",
        type=int,
        default=32,
        help="specify the batch size of the input"
    )
    parser.add_argument(
        "--trainer",
        type=str,
        default="torchbenchmark.util.distributed.core_model.trainer.Trainer",
        help="training paradigm, by default using DDP"
    )

    try:
        if args:
            return parser.parse_args(args)
        else:
            return parser.parse_args()
    except:
        parser.print_help()
        sys.exit(0)


def get_init_file(args):
    # Init file must not exist, but it's parent dir must exist.
    os.makedirs(args.job_dir, exist_ok=True)
    init_file = Path(args.job_dir) / f"{uuid.uuid4().hex}_init"
    print(init_file)
    if init_file.exists():
        os.remove(str(init_file))
    return init_file


class TrainerWrapper(object):
    def __init__(self, args, model_args):
        self.args = args
        self.args.output_dir = args.job_dir
        
        # extra args just passed to the Trainer class ctor
        self.model_args=model_args

    def __call__(self):
        self._setup_gpu_args()

        pos = self.args.model.rfind(".")
        module = importlib.import_module(self.args.model[:pos])
        model_class = getattr(module, self.args.model[(pos+1):])

        pos = self.args.trainer.rfind(".")
        module = importlib.import_module(self.args.trainer[:pos])
        trainer_class = getattr(module, self.args.trainer[(pos+1):])

        return trainer_class(self.args, model_class, batch_size=self.args.batch_size, model_args=self.model_args).measure()

    def checkpoint(self):
        self.args.dist_url = get_init_file(self.args).as_uri()
        checkpoint_file = os.path.join(self.args.output_dir, "checkpoint.pth")
        if os.path.exists(checkpoint_file):
            self.args.resume = checkpoint_file
        print("Requeuing ", self.args)
        empty_trainer = type(self)(self.args)
        return submitit.helpers.DelayedSubmission(empty_trainer)

    def _setup_gpu_args(self):
        job_env = submitit.JobEnvironment()
        self.args.output_dir = Path(str(self.args.output_dir).replace("%j", str(job_env.job_id)))
        self.args.gpu = job_env.local_rank
        self.args.rank = job_env.global_rank
        self.args.world_size = job_env.num_tasks
        print(f"Process group: {job_env.num_tasks} tasks, rank: {job_env.global_rank}")

        os.environ["LOCAL_RANK"] = str(job_env.local_rank)
        os.environ["RANK"] = str(job_env.global_rank)
        os.environ["WORLD_SIZE"] = str(job_env.num_tasks)
        os.environ["GPUS_PER_NODE"] = str(job_env.num_tasks//job_env.num_nodes)
        # os.environ["NCCL_IB_DISABLE"] = str(1)
        os.environ["NCCL_DEBUG"] = 'INFO'
        os.environ["NCCL_DEBUG_SUBSYS"] = 'INIT,ENV,NET'
        os.environ['NCCL_SOCKET_IFNAME'] = 'ens'
        # os.environ["NCCL_ALGO"] = 'ring'
        os.environ["FI_PROVIDER"] = 'efa'
        os.environ["FI_EFA_USE_DEVICE_RDMA"]= str(1)
        os.environ["NET_TYPE"] = 'efa'


def main():
    args = parse_args()

    # Note that the folder will depend on the job_id, to easily track experiments
    executor = submitit.AutoExecutor(folder=args.job_dir, slurm_max_num_timeout=3000)
    
    executor.update_parameters(
        gpus_per_node=args.ngpus,
        # one task per GPU
        tasks_per_node=args.ngpus,
        cpus_per_task=10,
        nodes=args.nodes,
        timeout_min=args.timeout,
        # Below are cluster dependent parameters
        slurm_partition=args.partition,
        slurm_signal_delay_s=120,
    )

    executor.update_parameters(name="distbench", slurm_array_parallelism=1, timeout_min=1000)

    
    # args.dist_url = get_init_file(args).as_uri()
    # args.output_dir = args.job_dir
    # job = executor.submit(TrainerWrapper(args))
    #     # print ID of the Slurm job
    # print(job.job_id)

    # # waits for completion and returns output
    # print(job.results())

    # models = ['torchbenchmark.models.hf_Bert.Model', 'torchbenchmark.models.hf_BertLarge.Model', \
    #     'torchbenchmark.models.hf_GPT2_large.Model', 'torchbenchmark.models.hf_T5_large.Model', \
    #         'torchbenchmark.models.timm_vision_transformer_large.Model', 'torchbenchmark.models.hf_GPT2.Model', \
    #             'torchbenchmark.models.hf_T5.Model']

    # model_batch_size = {'torchbenchmark.models.hf_Bert.Model': 32, 'torchbenchmark.models.hf_BertLarge.Model': 16, \
    #     'torchbenchmark.models.hf_GPT2_large.Model': 4, 'torchbenchmark.models.hf_T5_large.Model': 4, \
    #         'torchbenchmark.models.timm_vision_transformer_large.Model': 16, 'torchbenchmark.models.hf_GPT2.Model': 24, \
    #             'torchbenchmark.models.hf_T5.Model': 12}
    models = ['torchbenchmark.models.hf_Bert.Model', 'torchbenchmark.models.resnet50.Model']
    model_batch_size = {'torchbenchmark.models.hf_Bert.Model': 32, 'torchbenchmark.models.resnet50.Model': 32}
    model_args_configs = [
        [],  # no args = pure eager baseline
        ["--torchdynamo", "eager"],  # runs dynamo without a backend
        ["--torchdynamo", "aot_nvfuser"],
    ]
    # node_list = [i for i in range(24, 25)]
    # node_list = [1, 2, 4, 8, 16, 24]
    node_list = [1, 2]
    for nodes in node_list:
        for model_name in models:
            for model_args in model_args_configs:
                print(f"submitting {model_name} for {nodes} nodes, with backend settings {model_args}")
                batch_size = model_batch_size[model_name]
                args.model = model_name
                args.batch_size = batch_size
                args.nodes = nodes
                args.dist_url = get_init_file(args).as_uri()
                args.output_dir = args.job_dir
                executor.update_parameters(
                    gpus_per_node=args.ngpus,
                    # one task per GPU
                    tasks_per_node=args.ngpus,
                    cpus_per_task=10,
                    nodes=args.nodes,
                    timeout_min=args.timeout,
                    # Below are cluster dependent parameters
                    slurm_partition=args.partition,
                    slurm_signal_delay_s=120,
                )
                job = executor.submit(TrainerWrapper(args, model_args))

                # print ID of the Slurm job
                print(job.job_id)

        # waits for completion and returns output
        print(job.results())


if __name__=="__main__":
    main()