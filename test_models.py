import sys
import os

# Add the current directory to sys.path so we can import from models/
sys.path.append(os.getcwd())

from models.transformer_predictor import TransformerPredictor


def test_predictions():
    print("--- Testing IS/ES Prediction Models ---")

    # 1. Load Models
    print("\n1. Loading Models...")
    is_model_path = "models/roberta_is"
    es_model_path = "models/roberta_es"

    if not os.path.exists(is_model_path):
        print(f"Error: IS model not found at {is_model_path}. Did you train it?")
        return
    if not os.path.exists(es_model_path):
        print(f"Error: ES model not found at {es_model_path}. Did you train it?")
        return

    is_predictor = TransformerPredictor(is_model_path)
    es_predictor = TransformerPredictor(es_model_path)

    # 2. Define Test Cases
    test_cases = [
        {
            "comment": "I am so sorry you are going through this. It sounds incredibly tough, but please know you are not alone.",
            "type": "High Emotional Support",
        },
        {
            "comment": "You should try Cognitive Behavioral Therapy (CBT). It really helped me with similar symptoms. Also, check out this book on bipolar disorder.",
            "type": "High Informational Support",
        },
        {"comment": "I don't know what to say.", "type": "Low Support"},
    ]

    # 3. Run Predictions
    print("\n2. Running Predictions...")
    print(
        f"{'Comment Type':<25} | {'IS Score':<10} | {'ES Score':<10} | {'Comment Snippet'}"
    )
    print("-" * 80)

    for case in test_cases:
        comment = case["comment"]

        # Predict IS
        is_score = is_predictor.predict(comment)

        # Predict ES
        es_score = es_predictor.predict(comment)

        print(
            f"{case['type']:<25} | {is_score:.4f}     | {es_score:.4f}     | {comment[:30]}..."
        )

    print("\n--- Test Complete ---")


if __name__ == "__main__":
    test_predictions()
