from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from models.transformer_predictor import TransformerPredictor
from models.generative_recommender import GenerativeRecommender
from models.safety_filter import SafetyFilter
import os
import concurrent.futures
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Bepsbot ML Backend")

# Initialize Models
print("Initializing Models in Backend...")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    is_predictor = TransformerPredictor(os.path.join(BASE_DIR, "models/roberta_is"))
except Exception as e:
    print(f"Warning: Could not load IS model: {e}")
    is_predictor = None

try:
    es_predictor = TransformerPredictor(os.path.join(BASE_DIR, "models/roberta_es"))
except Exception as e:
    print(f"Warning: Could not load ES model: {e}")
    es_predictor = None

gen_recommender = GenerativeRecommender(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1",
    model="deepseek-chat",
)

safety_filter = SafetyFilter(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1",
    model="deepseek-chat",
)
print("Backend Models Initialized.")


class PredictRequest(BaseModel):
    comment: str


class RecommendRequest(BaseModel):
    op_text: str
    comment: str


@app.post("/predict_scores")
def predict_scores(req: PredictRequest):
    try:
        if is_predictor is None or es_predictor is None:
            # Return dummy scores if models are missing so the frontend doesn't crash
            print("Warning: Models not loaded, returning default scores.")
            return {"IS_score": 0.0, "ES_score": 0.0}

        is_score = is_predictor.predict(req.comment)
        es_score = es_predictor.predict(req.comment)
        return {"IS_score": float(is_score), "ES_score": float(es_score)}
    except Exception as e:
        print(f"Error in predict_scores: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/recommend_candidates")
def recommend_candidates(req: RecommendRequest):
    try:
        # Safety Check Input
        is_safe, reason = safety_filter.is_safe(req.comment)
        if not is_safe:
            return {"is_safe": False, "reason": reason, "candidates": {}}

        # Generate
        candidates = gen_recommender.generate_candidates(req.op_text, req.comment)

        # Safety Check Candidates (Parallelized)
        def check_candidate_safety(key, text):
            is_safe, reason = safety_filter.is_safe(text)
            return key, is_safe, reason

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_key = {
                executor.submit(
                    check_candidate_safety, key, candidates.get(key, "")
                ): key
                for key in ["candidate_1", "candidate_2", "candidate_3"]
            }

            for future in concurrent.futures.as_completed(future_to_key):
                key, c_safe, c_reason = future.result()
                if not c_safe:
                    candidates[key] = f"[Content Filtered: {c_reason}]"

        return {"is_safe": True, "candidates": candidates}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
