# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0.
"""
pip install "mteb>=2.3.10,<3.0.0"
python3 mteb2_eval.py --model_name nvidia/nemotron-colembed-vl-8b-v2 --batch_size 16 --benchmark "ViDoRe(v3)" --task-list Vidore3ComputerScienceRetrieval
"""

from __future__ import annotations

import argparse
import os
import mteb

import mteb

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_name", type=str, required=True) 
    parser.add_argument("--batch_size", type=int, default=16, required=False)
    parser.add_argument("--results_folder", type=str, default="results_csv", required=False)
    parser.add_argument("--predictions_folder", type=str, default=None, required=False)
    parser.add_argument("--benchmark", type=str, required=False, default="ViDoRe(v3)",
                        choices=["ViDoRe(v3)", # Vidore V3
                                 "VisualDocumentRetrieval" # Vidore V1 & V2
                                ])
    parser.add_argument(
        "--task-list",
        type=str,
        nargs='+',  # Accept one or more space-separated string arguments
        default=None, # Default to None if the argument is not provided
        help="Optional: A list of task class names to run. If not provided, all tasks will be run."
    )
    args = parser.parse_args()

    print(f"Loading model: {args.model_name}")
    model = mteb.get_model_meta(args.model_name)

    # Loads all benchmark tasks
    all_tasks = mteb.get_benchmark(args.benchmark).tasks
    all_tasks_names = " ".join([task.__class__.__name__ for task in all_tasks])
    print(f"Available tasks in benchmark {args.benchmark}: {all_tasks_names}")

    #filter tasks
    if args.task_list:
        # If user provided a list, filter all_tasks
        print(f"Running evaluation on specified tasks: {args.task_list}")
        requested_task_names = set(args.task_list)
        tasks = [
            task for task in all_tasks
            if task.__class__.__name__ in requested_task_names
        ]
        
        # Optional: Warn if a requested task was not found
        found_names = {t.__class__.__name__ for t in tasks}
        missing = requested_task_names - found_names
        if missing:
            print(f"Warning: The following requested tasks were not found and will be skipped: {missing}")
    else:
        # If --task-list was not provided, use all tasks
        print("Running evaluation on all available tasks.")
        tasks = all_tasks

    tasks_names = " ".join([task.__class__.__name__ for task in tasks])
    print(f"Evaluating tasks: {tasks_names}")

    results = mteb.evaluate(model=model, tasks=tasks, 
                    encode_kwargs = {
                        "batch_size": args.batch_size,
                    },
                    prediction_folder=args.predictions_folder,
                    overwrite_strategy="always",
                    )
    
    print(results)

    print(f"Saving results to {args.results_folder}")
    os.makedirs(args.results_folder, exist_ok=True)
    model_name = args.model_name.replace("/", "_")
    output_path = os.path.join(args.results_folder, f"{model_name}-{tasks_names.replace(' ', '-')}.csv")
    df = results.to_dataframe()
    df.to_csv(output_path, index=False)

if __name__ == "__main__":
    main()
