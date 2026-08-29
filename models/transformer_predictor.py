import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification


class TransformerPredictor:
    def __init__(self, model_path):
        """
        Initializes the predictor with a trained model.

        Args:
            model_path (str): Path to the directory containing the saved model and tokenizer.
        """
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"Loading model from {model_path} on {self.device}...")

        try:
            self.tokenizer = AutoTokenizer.from_pretrained(model_path)
            self.model = AutoModelForSequenceClassification.from_pretrained(model_path)
            self.model.to(self.device)
            self.model.eval()
            print("Model loaded successfully.")
        except Exception as e:
            print(f"Error loading model from {model_path}: {e}")
            self.model = None

    def predict(self, comment, op_text=None):
        """
        Predicts the score for a given comment (and optional OP context).

        Args:
            comment (str): The user's comment.
            op_text (str, optional): The original post text.

        Returns:
            float: The predicted score.
        """
        if self.model is None:
            print("Model not loaded. Returning default score 0.")
            return 0.0

        # Prepare input
        if op_text:
            # Context-aware format: <s> OP </s></s> Comment </s>
            input_text = f"{op_text} </s></s> {comment}"
        else:
            input_text = comment

        inputs = self.tokenizer(
            input_text,
            return_tensors="pt",
            truncation=True,
            padding=True,
            max_length=512,
        ).to(self.device)

        with torch.no_grad():
            outputs = self.model(**inputs)
            # For regression, the output is a single value
            prediction = outputs.logits.squeeze().item()

        return prediction
