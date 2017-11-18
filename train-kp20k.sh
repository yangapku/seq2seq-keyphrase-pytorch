#!/usr/bin/env bash
#SBATCH --cluster=gpu
#SBATCH --gres=gpu:1
#SBATCH --partition=titanx
#SBATCH --job-name=tdr_dag
#SBATCH --output=tdr_dag.out
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=1

# Load modules
#module restore

# Run the job
#srun python -m train -data data/kp20k/kp20k.train_valid.pt -vocab data/kp20k/kp20k.vocab.pt -bidirectional -exp_path "exp/kp20k.bi-directional.no-loss-mask.%s" -exp "kp20k" -batch_size 256 -train_from "exp/kp20k.bi-directional.no-loss-mask.20171117-214914/kp20k.epoch=3.batch=5704.total_batch=26000.model"
srun python -m train -data data/kp20k/kp20k.train_valid.pt -vocab data/kp20k/kp20k.vocab.pt -bidirectional -exp_path "exp/kp20k.bi-directional.no-loss-mask.20171117-214914/" -exp "kp20k" -batch_size 256 -train_from "exp/kp20k.bi-directional.no-loss-mask.20171117-214914/kp20k.epoch=3.batch=5704.total_batch=26000.model"
