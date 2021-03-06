# -*- encoding:utf-8 -*-
import sys
import os
import torch
import argparse

bert_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(bert_dir)

from bert.model_saver import save_model


def average_models(model_list_path):
    for i, model_path in enumerate(model_list_path):
        model = torch.load(model_path)
        if i == 0:
            avg_model = model
        else:
            for k, v in avg_model.items():
                avg_model[k].mul_(i).add_(model[k]).div_(i+1)
     
    return avg_model


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="")
    parser.add_argument("--model_list_path", nargs="+", required=True,
                        help="Path of the input model list.")
    parser.add_argument("--output_model_path", required=True,
                        help="Path of the output model.")
    args = parser.parse_args()

    avg_model = average_models(args.model_list_path)
    torch.save(avg_model, args.output_model_path)
