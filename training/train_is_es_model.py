import argparse
import pandas as pd
import numpy as np
import torch
from torch.utils.data import Dataset
from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    Trainer,
    TrainingArguments,
    DataCollatorWithPadding,
)
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, r2_score

# --- Configuration ---
MODEL_NAME = "roberta-base"
MAX_LENGTH = 512


class PeerSupportDataset(Dataset):
    def __init__(self, encodings, labels):
        self.encodings = encodings
        self.labels = labels

    def __getitem__(self, idx):
        item = {key: torch.tensor(val[idx]) for key, val in self.encodings.items()}
        item["labels"] = torch.tensor(self.labels[idx], dtype=torch.float)
        return item

    def __len__(self):
        return len(self.labels)


def compute_metrics(eval_pred):
    predictions, labels = eval_pred
    predictions = predictions.squeeze()
    mse = mean_squared_error(labels, predictions)
    r2 = r2_score(labels, predictions)
    return {"mse": mse, "r2": r2}


def train(args):
    print(f"Loading data from {args.csv_file}...")
    df = pd.read_csv(args.csv_file)

    # Check for required columns
    if args.target not in df.columns:
        raise ValueError(f"Target column '{args.target}' not found in CSV.")

    # Prepare Input Text
    # If context (OP) is available, format as: <s> OP </s></s> Comment </s>
    # Otherwise just: <s> Comment </s>
    if args.context_col and args.context_col in df.columns:
        print(f"Using Context Column: {args.context_col}")
        # RoBERTa uses </s></s> as separator between sentences
        df["input_text"] = (
            df[args.context_col].astype(str)
            + " </s></s> "
            + df[args.text_col].astype(str)
        )
    else:
        print("No Context Column used. Training on Comment only.")
        df["input_text"] = df[args.text_col].astype(str)

    # Filter out missing values
    df = df.dropna(subset=["input_text", args.target])

    # Split Data
    train_texts, val_texts, train_labels, val_labels = train_test_split(
        df["input_text"].tolist(),
        df[args.target].tolist(),
        test_size=0.2,
        random_state=42,
    )

    print(f"Training samples: {len(train_texts)}, Validation samples: {len(val_texts)}")

    # Tokenization
    print(f"Loading tokenizer for {MODEL_NAME}...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

    train_encodings = tokenizer(
        train_texts, truncation=True, padding=True, max_length=MAX_LENGTH
    )
    val_encodings = tokenizer(
        val_texts, truncation=True, padding=True, max_length=MAX_LENGTH
    )

    train_dataset = PeerSupportDataset(train_encodings, train_labels)
    val_dataset = PeerSupportDataset(val_encodings, val_labels)

    # Model Initialization
    print(f"Initializing model {MODEL_NAME} for regression...")
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME, num_labels=1, problem_type="regression"
    )

    # Training Arguments
    training_args = TrainingArguments(
        output_dir=args.output_dir,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        warmup_steps=500,
        weight_decay=0.01,
        logging_dir=f"{args.output_dir}/logs",
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="mse",
        greater_is_better=False,  # Lower MSE is better
        learning_rate=2e-5,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        compute_metrics=compute_metrics,
    )

    # Train
    print("Starting training...")
    trainer.train()

    # Save
    print(f"Saving model to {args.output_dir}...")
    trainer.save_model(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    print("Done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Train a Transformer model for IS/ES prediction."
    )
    parser.add_argument(
        "--csv_file", type=str, required=True, help="Path to the CSV dataset."
    )
    parser.add_argument(
        "--text_col",
        type=str,
        default="comment_body",
        help="Column name for the comment text.",
    )
    parser.add_argument(
        "--context_col",
        type=str,
        default=None,
        help="Column name for the OP/Context text (optional).",
    )
    parser.add_argument(
        "--target",
        type=str,
        required=True,
        choices=["IS_rating", "ES_rating"],
        help="Target score to predict.",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        required=True,
        help="Directory to save the trained model.",
    )
    parser.add_argument(
        "--epochs", type=int, default=3, help="Number of training epochs."
    )
    parser.add_argument("--batch_size", type=int, default=8, help="Batch size.")

    args = parser.parse_args()
    train(args)
