import os
import json
import sys
import pandas as pd
from tqdm import tqdm

# Add parent directory to path to import models
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.safety_filter import SafetyFilter
from models.transformer_predictor import TransformerPredictor


def clean_and_label_data(
    record_dir="record", output_file="training/new_training_data.csv"
):
    """
    Reads user records, filters them for safety and quality,
    pseudo-labels them with current models, and saves to CSV.
    """
    print(f"Scanning records in {record_dir}...")

    records = []
    if not os.path.exists(record_dir):
        print(f"Directory {record_dir} not found.")
        return

    # 1. Load all JSON records
    for filename in os.listdir(record_dir):
        if filename.endswith(".json"):
            filepath = os.path.join(record_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                    final_comment = data.get("final_comment", "").strip()
                    if final_comment:
                        records.append(final_comment)
            except Exception as e:
                print(f"Error reading {filename}: {e}")

    print(f"Found {len(records)} raw comments.")

    # 2. Initialize Models
    print("Initializing Safety Filter and Predictors...")
    # Use environment variables for credentials
    safety_filter = SafetyFilter(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
    )

    # Check if models exist before loading
    is_model_path = "models/roberta_is"
    es_model_path = "models/roberta_es"

    if not os.path.exists(is_model_path) or not os.path.exists(es_model_path):
        print("Error: RoBERTa models not found. Please train them first.")
        return

    is_predictor = TransformerPredictor(is_model_path)
    es_predictor = TransformerPredictor(es_model_path)

    cleaned_data = []

    # 3. Process each comment
    print("Processing comments (Safety Check + Pseudo-Labeling)...")
    for comment in tqdm(records):
        # A. Length Filter
        if len(comment.split()) < 10:
            continue  # Too short

        # B. Safety Filter
        is_safe, reason = safety_filter.is_safe(comment)
        if not is_safe:
            print(f"Skipping unsafe comment: {reason}")
            continue

        # C. Pseudo-Labeling (Predict IS/ES)
        try:
            is_score = is_predictor.predict(comment)
            es_score = es_predictor.predict(comment)

            # Optional: Filter by quality?
            # For now, we keep them all but record the scores.
            # If we assume user submissions are "good", we might want to manually set them to high?
            # But pseudo-labeling is safer for regression tasks.

            cleaned_data.append(
                {
                    "comment_body": comment,
                    "IS_rating": is_score,
                    "ES_rating": es_score,
                    "source": "user_submission",
                }
            )
        except Exception as e:
            print(f"Error predicting for comment: {e}")

    # 4. Save to CSV
    if cleaned_data:
        df = pd.DataFrame(cleaned_data)
        # Append to existing file if it exists, else create new
        if os.path.exists(output_file):
            df.to_csv(output_file, mode="a", header=False, index=False)
            print(f"Appended {len(df)} rows to {output_file}")
        else:
            df.to_csv(output_file, index=False)
            print(f"Created {output_file} with {len(df)} rows")

        print("\nSample of new data:")
        print(df.head())
    else:
        print("No valid data found after filtering.")


if __name__ == "__main__":
    clean_and_label_data()
