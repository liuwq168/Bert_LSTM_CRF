# -*- encoding:utf-8 -*-
"""
    CCKS2017 Named Entity Recongition with Bert-Bilstm-CRF.
"""
import random
import argparse

import torch
import torch.nn as nn
from torch.nn import CrossEntropyLoss

from uer.model_builder import build_model
from uer.utils.config import load_hyperparam
from uer.utils.optimizers import  BertAdam
from uer.utils.constants import *
from uer.utils.vocab import Vocab
from uer.utils.seed import set_seed
from uer.model_saver import save_model
from uer.layers.crf import CRF

class CCKSTagger(nn.Module):
    def __init__(self, args, model):
        super(CCKSTagger, self).__init__()
        self.embedding = model.embedding
        self.encoder = model.encoder
        self.target = model.target
        self.labels_num = args.labels_num
        self.lstm_layers = args.lstm_layers
        self.lstm_hidden = args.lstm_hidden
        self.use_cuda = args.use_cuda
        """ self.lstm = nn.LSTM(input_size=args.hidden_size,
                           hidden_size=self.lstm_hidden,
                           num_layers=self.lstm_layers,
                           bidirectional=True,
                           dropout=args.dropout,
                           batch_first=True) """
        self.lstm = nn.LSTM(input_size=args.hidden_size,
                           hidden_size=self.lstm_hidden,
                           num_layers=self.lstm_layers,
                           bidirectional=True,
                           dropout=args.lstm_dropout,
                           batch_first=True)
        self.crf = CRF(target_size=self.labels_num,
                        average_batch=True,
                        use_cuda=args.use_cuda,
                        bad_pairs=args.bad_pairs,
                        good_pairs=args.good_pairs)
        self.linear = nn.Linear(self.lstm_hidden*2, self.labels_num+2)
        #self.droplayer = nn.Dropout(p=args.dropout)
        self.droplayer = nn.Dropout(p=args.lstm_dropout)
    
    """ 
        Initialize hidden variable
    """
    def init_hidden(self, batch_size, device):
        return (torch.zeros(2 * self.lstm_layers, batch_size, self.lstm_hidden, device=device),
                torch.zeros(2 * self.lstm_layers, batch_size, self.lstm_hidden, device=device))
    
    '''
        Forward Algorithm
        args:
            src (batch_size, seq_length) : word-level representation of sentence
            label (batch_size, seq_length) : the true label
            mask (batch_size, seq_length) : the mask
        return:
            loss: Sequence labeling loss.
            correct: Number of labels that are predicted correctly.
            predict: Predicted label.
            label: Gold label.
    '''
    def forward(self, src, label, mask):
        # Embedding.
        emb = self.embedding(src, mask)
        # Encoder. (batch_size, seq_length, hidden_size)
        context_vector = self.encoder(emb, mask)
        # lstm. (batch_size, seq_length, lstm_hidden)
        hidden = self.init_hidden(context_vector.size(0), emb.device)
        lstm_out, hidden = self.lstm(context_vector, hidden)
        lstm_out = lstm_out.contiguous().view(-1, self.lstm_hidden*2)

        d_lstm_out = self.droplayer(lstm_out)
        l_out = self.linear(d_lstm_out)
        # lstm_feats. (batch_size, seq_lenth, labels_num + 2)
        lstm_feats = l_out.contiguous().view(context_vector.size(0), context_vector.size(1), -1)

        return lstm_feats
    
    """
        CRF LOSS
        args:
            feats: size=(batch_size, seq_len, tag_size)
            mask: size=(batch_size, seq_len)
            tags: size=(batch_size, seq_len)
        return:
            loss_value
    """    
    def loss(self, feats, mask, tags):
        loss_value = self.crf.neg_log_likelihood_loss(feats, mask, tags)
        batch_size = feats.size(0)
        loss_value /= float(batch_size)
        return loss_value      

def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    # Path options.
    parser.add_argument("--pretrained_model_path", default=None, type=str,
                        help="Path of the pretrained model.")
    parser.add_argument("--output_model_path", default="./models/tagger_model.bin", type=str,
                        help="Path of the output model.")
    parser.add_argument("--vocab_path", default="./models/google_vocab.txt", type=str,
                        help="Path of the vocabulary file.")
    parser.add_argument("--train_path", type=str, required=True,
                        help="Path of the trainset.")
    parser.add_argument("--dev_path", type=str, required=True,
                        help="Path of the devset.")
    parser.add_argument("--test_path", type=str, required=True,
                        help="Path of the testset.")
    parser.add_argument("--config_path", default="./models/google_config.json", type=str,
                        help="Path of the config file.")
    
    # Model options
    parser.add_argument("--batch_size", type=int, default=32,
                        help="Batch_size.")
    parser.add_argument("--seq_length", default=128, type=int,
                        help="Sequence length.")
    parser.add_argument("--encoder", choices=["bert", "lstm", "gru", \
                                                   "cnn", "gatedcnn", "attn", \
                                                   "rcnn", "crnn", "gpt", "bilstm"], \
                                                   default="bert", help="Encoder type.")
    parser.add_argument("--bidirectional", action="store_true", help="Specific to recurrent model.")

    # Subword options.
    parser.add_argument("--subword_type", choices=["none", "char"], default="none",
                        help="Subword feature type.")
    parser.add_argument("--sub_vocab_path", type=str, default="models/sub_vocab.txt",
                        help="Path of the subword vocabulary file.")
    parser.add_argument("--subencoder", choices=["avg", "lstm", "gru", "cnn"], default="avg",
                        help="Subencoder type.")
    parser.add_argument("--sub_layers_num", type=int, default=2, help="The number of subencoder layers.")

    # Optimizer options.
    parser.add_argument("--learning_rate", type=float, default=2e-5,
                        help="Learning rate.")
    parser.add_argument("--warmup", type=float, default=0.1,
                        help="Warm up value.")
    
    # Training options.
    parser.add_argument("--dropout", type=float, default=0.1,
                        help="Dropout.")
    parser.add_argument("--epochs_num", type=int, default=3,
                        help="Number of epochs.")
    parser.add_argument("--report_steps", type=int, default=100,
                        help="Specific steps to print prompt.")
    parser.add_argument("--seed", type=int, default=7,
                        help="Random seed.")

    args = parser.parse_args()

    # Load the hyperparameters of the config file.
    args = load_hyperparam(args)

    set_seed(args.seed)

    # Find tagging labels.
    labels_map = {"NULL": 0, "O": 1} # ID for padding and non-entity.
    with open(args.train_path, mode="r", encoding="utf-8") as f:
        for line_id, line in enumerate(f):
            if line_id == 0:
                continue
            line = line.strip().split()
            if len(line) != 2:
                continue
            if line[1] not in labels_map:
                labels_map[line[1]] = len(labels_map)

    print("Labels: ", labels_map)
    print("Label Num: ", len(labels_map))
    args.labels_num = len(labels_map)

    # Create the bad pairs
    args.bad_pairs = []
    args.good_pairs = []
    for key1, value1 in labels_map.items():
        key1 = key1.strip().split('-')
        if len(key1) < 1 or len(key1) > 2:
            print("Error label: ", key1)
            exit()
        for key2, value2 in labels_map.items():
            key2 = key2.strip().split('-')
            if len(key2) == 1:
                continue
            if len(key1) == 1 and len(key2) == 2:
                if key2[0] == 'I':
                    args.bad_pairs.append([value1, value2])
                continue
            # p(B-X -> I-Y) = 0
            if key1[1] != key2[1] and key1[0] == 'B' and key2[0] == 'I':
                args.bad_pairs.append([value1, value2])
            # p(I-X -> I-Y) = 0
            if key1[1] != key2[1] and key1[0] == 'I' and key2[0] == 'I':
                args.bad_pairs.append([value1, value2])
            # p(B-X -> I-X) = 10
            if key1[1] == key2[1] and key1[0] == 'B' and key2[0] == 'I':
                args.good_pairs.append([value1, value2])
    
    print("Bad pairs: ", args.bad_pairs)
    print("Good pairs: ", args.good_pairs)

    # Load vocabulary.
    vocab = Vocab()
    vocab.load(args.vocab_path)
    args.vocab = vocab

    # Build bert model.
    # A pseudo target is added.
    args.target = "bert"
    model = build_model(args)

    # Load or initialize parameters.
    if args.pretrained_model_path is not None:
        # Initialize with pretrained model.
        model.load_state_dict(torch.load(args.pretrained_model_path), strict=False)  
    else:
        # Initialize with normal distribution.
        for n, p in list(model.named_parameters()):
            if 'gamma' not in n and 'beta' not in n:
                p.data.normal_(0, 0.02)

    # Some other parameters
    args.lstm_hidden = args.hidden_size
    args.lstm_layers = 2
    args.lstm_dropout = 0.1
    if torch.cuda.is_available():
        args.use_cuda = True
    
    # Build sequence labeling model.
    model = CCKSTagger(args, model)

    # For simplicity, we use DataParallel wrapper to use multiple GPUs.
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if torch.cuda.device_count() > 1:
        print("{} GPUs are available. Let's use them.".format(torch.cuda.device_count()))
        model = nn.DataParallel(model)
        model = model.module
    
    model = model.to(device)

    # Datset loader.
    def batch_loader(batch_size, input_ids, label_ids, mask_ids):
        instances_num = input_ids.size()[0]
        for i in range(instances_num // batch_size):
            input_ids_batch = input_ids[i*batch_size: (i+1)*batch_size, :]
            label_ids_batch = label_ids[i*batch_size: (i+1)*batch_size, :]
            mask_ids_batch = mask_ids[i*batch_size: (i+1)*batch_size, :]
            yield input_ids_batch, label_ids_batch, mask_ids_batch
        if instances_num > instances_num // batch_size * batch_size:
            input_ids_batch = input_ids[instances_num//batch_size*batch_size:, :]
            label_ids_batch = label_ids[instances_num//batch_size*batch_size:, :]
            mask_ids_batch = mask_ids[instances_num//batch_size*batch_size:, :]
            yield input_ids_batch, label_ids_batch, mask_ids_batch
    
    # Read dataset.
    def read_dataset(path):
        dataset = []
        with open(path, mode="r", encoding="utf-8") as f:
            tokens, labels = [], []
            for line_id, line in enumerate(f):
                if line_id == 0:
                    continue
                line = line.strip().split()
                if len(line) != 2:
                    if len(labels) == 0:
                        continue
                    assert len(tokens) == len(labels)
                    tokens = [vocab.get(t) for t in tokens]
                    labels = [labels_map[l] for l in labels]
                    mask = [1] * len(tokens)
                    if len(tokens) > args.seq_length:
                        tokens = tokens[:args.seq_length]
                        labels = labels[:args.seq_length]
                        mask = mask[:args.seq_length]
                    while len(tokens) < args.seq_length:
                        tokens.append(0)
                        labels.append(0)
                        mask.append(0)
                    dataset.append([tokens, labels, mask])

                    tokens, labels = [], []
                    continue
                tokens.append(line[0])
                labels.append(line[1])
        
        return dataset

    # Evaluation function.
    def evaluate(args, is_test):
        if is_test:
            dataset = read_dataset(args.test_path)
        else:
            dataset = read_dataset(args.dev_path)

        input_ids = torch.LongTensor([sample[0] for sample in dataset])
        label_ids = torch.LongTensor([sample[1] for sample in dataset])
        mask_ids = torch.LongTensor([sample[2] for sample in dataset])

        instances_num = input_ids.size(0)
        batch_size = args.batch_size

        if is_test:
            print("Batch size: ", batch_size)
            print("The number of test instances:", instances_num)

    
        correct = 0
        gold_entities_num = 0
        pred_entities_num = 0

        confusion = torch.zeros(len(labels_map), len(labels_map), dtype=torch.long)

        model.eval()

        for i, (input_ids_batch, label_ids_batch, mask_ids_batch) in enumerate(batch_loader(batch_size, input_ids, label_ids, mask_ids)):
            input_ids_batch = input_ids_batch.to(device)
            label_ids_batch = label_ids_batch.to(device)
            mask_ids_batch = mask_ids_batch.to(device)
            # loss, _, pred, gold = model(input_ids_batch, label_ids_batch, mask_ids_batch)
            feats = model(input_ids_batch, label_ids_batch, mask_ids_batch)
            path_score, best_path = model.crf(feats, mask_ids_batch.byte())
            pred = best_path.contiguous().view(-1)
            gold = label_ids_batch.contiguous().view(-1)

            """ if i == 0:
                print('pred', pred)
                print('gold', gold) """
            
            # Gold.
            for j in range(gold.size()[0]):
                if (j > 0 and gold[j-1].item() <= 1 and gold[j].item() > 1) or (j == 0 and gold[j].item() > 1):
                    gold_entities_num += 1

            # Predict.
            for j in range(pred.size()[0]):
                if (j > 0 and pred[j-1].item() <= 1 and pred[j].item() > 1 and gold[j].item() != 0) or (j == 0 and pred[j].item() > 1):
                    pred_entities_num += 1

            pred_entities_pos = []
            gold_entities_pos = []
            start, end = 0, 0

            # Correct.
            for j in range(gold.size()[0]):
                if (j > 0 and gold[j-1].item() <= 1 and gold[j].item() > 1) or (j == 0 and gold[j].item() > 1):
                    start = j
                    for k in range(j, gold.size()[0]):
                        if gold[k].item() <= 1:
                            end = k - 1
                            break
                    else:
                        end = gold.size()[0] - 1
                    gold_entities_pos.append((start, end))

            # Predict.
            for j in range(pred.size()[0]):
                if (j > 0 and pred[j-1].item() <= 1 and pred[j].item() > 1) or (j == 0 and pred[j].item() > 1):
                        start = j
                        for k in range(j, pred.size()[0]):
                            if pred[k].item() <= 1:
                                end = k - 1
                                break
                        else:
                            end = pred.size()[0] - 1
                        pred_entities_pos.append((start, end))

            for entity in pred_entities_pos:
                if entity not in gold_entities_pos:
                    continue
                for j in range(entity[0], entity[1]+1):
                    if gold[j].item() != pred[j].item():
                        break
                else: 
                    correct += 1

        print("Report precision, recall, and f1:")
        p = correct/pred_entities_num
        r = correct/gold_entities_num
        f1 = 2*p*r/(p+r)
        print("{:.3f}, {:.3f}, {:.3f}".format(p,r,f1))

        return f1
    
    # Training phase.
    print("Start training.")
    instances = read_dataset(args.train_path)

    input_ids = torch.LongTensor([ins[0] for ins in instances])
    label_ids = torch.LongTensor([ins[1] for ins in instances])
    mask_ids = torch.LongTensor([ins[2] for ins in instances])

    instances_num = input_ids.size(0)
    batch_size = args.batch_size
    train_steps = int(instances_num * args.epochs_num / batch_size) + 1

    print("Batch size: ", batch_size)
    print("The number of training instances:", instances_num)

    param_optimizer = list(model.named_parameters())
    no_decay = ['bias', 'gamma', 'beta']
    optimizer_grouped_parameters = [
                {'params': [p for n, p in param_optimizer if not any(nd in n for nd in no_decay)], 'weight_decay_rate': 0.01},
                {'params': [p for n, p in param_optimizer if any(nd in n for nd in no_decay)], 'weight_decay_rate': 0.0}
    ]
    optimizer = BertAdam(optimizer_grouped_parameters, lr=args.learning_rate, warmup=args.warmup, t_total=train_steps)

    total_loss = 0.
    f1 = 0.0
    best_f1 = 0.0

    for epoch in range(1, args.epochs_num+1):
        model.train()
        for i, (input_ids_batch, label_ids_batch, mask_ids_batch) in enumerate(batch_loader(batch_size, input_ids, label_ids, mask_ids)):
            model.zero_grad()

            input_ids_batch = input_ids_batch.to(device)
            label_ids_batch = label_ids_batch.to(device)
            mask_ids_batch = mask_ids_batch.to(device)

            """ loss, _, _, _ = model(input_ids_batch, label_ids_batch, mask_ids_batch)
            if torch.cuda.device_count() > 1:
                loss = torch.mean(loss)
            total_loss += loss.item()
            if (i + 1) % args.report_steps == 0:
                print("Epoch id: {}, Training steps: {}, Avg loss: {:.3f}".format(epoch, i+1, total_loss / args.report_steps))
                total_loss = 0. """
            """ print("mask1:", mask_ids_batch)
            print("label1:", label_ids_batch) """
            feats = model(input_ids_batch, label_ids_batch, mask_ids_batch)
            """ print("feats:", feats) """
            loss = model.loss(feats, mask_ids_batch, label_ids_batch)
            if (i + 1) % args.report_steps == 0:
                print("Epoch id: {}, Training steps: {}, Loss: {:.3f}".format(epoch, i+1, loss))

            loss.backward()
            optimizer.step()

        f1 = evaluate(args, False)
        if f1 > best_f1:
            best_f1 = f1
            save_model(model, args.output_model_path)
        #else:
        #    break

    # Evaluation phase.
    print("Start evaluation.")

    """ if torch.cuda.device_count() > 1:
        model.module.load_state_dict(torch.load(args.output_model_path))
    else:
        model.load_state_dict(torch.load(args.output_model_path)) """
    model.load_state_dict(torch.load(args.output_model_path))

    evaluate(args, True)

    """
    args.lstm_hidden
    args.lstm_layers
    args.use_cuda """

if __name__ == "__main__":
    main()