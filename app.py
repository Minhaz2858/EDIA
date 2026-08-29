from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

import numpy as np
import random
import time
import csv
from textblob import TextBlob
from util import *
import xgboost as xgb
import warnings
import threading

warnings.filterwarnings("ignore")
from sklearn import svm
from sentence_transformers import SentenceTransformer
from joblib import dump, load
from nltk.tokenize import sent_tokenize
from sklearn.ensemble import RandomForestClassifier
from nltk.tokenize import TweetTokenizer
import sys, os, re
from sklearn.metrics.pairwise import cosine_similarity
from elasticsearch import Elasticsearch
import json
import openai
import subprocess
import requests
from dotenv import load_dotenv

# Suppress warnings
warnings.filterwarnings("ignore")
load_dotenv()

# Initialize LIWC parser
lex = liwc.parse_liwc("2015")


# Function to get features from text (from both original backends)
def get_vector(doc, cats):
    dic = extract(lex, doc)
    vec = np.zeros(len(cats))
    for i in range(len(cats)):
        if cats[i] in dic.keys():
            vec[i] = dic[cats[i]]
    sentences = sent_tokenize(doc)
    number_of_sentences = len(sentences)
    vec = np.append(vec, [number_of_sentences])
    tknzr = TweetTokenizer()
    words = tknzr.tokenize(doc)
    number_of_words = len(words)
    vec = np.append(vec, [number_of_words])
    blob = TextBlob(doc)
    vec = np.append(vec, [blob.sentiment.polarity])
    vec = np.append(vec, [blob.sentiment.subjectivity])
    return vec, dic


LIWC_features = [
    "affect",
    "posemo",
    "informal",
    "assent",
    "social",
    "cogproc",
    "insight",
    "tentat",
    "function",
    "article",
    "quant",
    "prep",
    "verb",
    "percept",
    "hear",
    "focuspresent",
    "drives",
    "reward",
    "focuspast",
    "pronoun",
    "ipron",
    "conj",
    "differ",
    "adj",
    "negemo",
    "sad",
    "feel",
    "relativ",
    "space",
    "compare",
    "auxverb",
    "power",
    "ppron",
    "you",
    "i",
    "cause",
    "work",
    "we",
    "affiliation",
    "see",
    "achieve",
    "leisure",
    "adverb",
    "negate",
    "interrog",
    "discrep",
    "focusfuture",
    "motion",
    "time",
    "nonflu",
    "anx",
    "bio",
    "health",
    "netspeak",
    "risk",
    "anger",
    "relig",
    "swear",
    "certain",
    "family",
    "female",
    "shehe",
    "home",
    "friend",
    "male",
    "body",
    "they",
    "number",
    "death",
    "money",
    "filler",
    "sexual",
    "ingest",
]

# --- LOAD NEW MODELS ---
# Models are now served via FastAPI backend (backend_api.py)
# We will make HTTP requests to localhost:8000 instead of loading them here.
import requests

BACKEND_URL = "http://127.0.0.1:8000"

# Load models (Keeping old ones for fallback/comparison if needed, but primarily using new ones)
print("Loading SentenceTransformer (this may take a while if downloading)...")
# Disable SSL verification for downloading model (Workaround for network issues)
import os

os.environ["HF_HUB_DISABLE_SSL_VERIFY"] = "1"
os.environ["CURL_CA_BUNDLE"] = ""
# Suppress Tokenizers Parallelism Warning
os.environ["TOKENIZERS_PARALLELISM"] = "false"

embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
print("Sentence-BERT model loaded successfully for embeddings.")
# IS_clf = load("models/is_model.joblib") # Commented out old model
# ES_clf_assess = xgb.XGBClassifier()
# ES_clf_assess.load_model("models/es_model.json") # Commented out old model
# ES_clf_recommend = load("models/es_model.joblib") # Commented out old model

# Feedback scripts and word lists for assessment (from assess_backend.py)
feedback_scripts_assess = [
    "Everything has two sides and let’s try to see things in a positive light",
    "Write more about it and then free your mind",
]
give_1 = ["Here are my small suggestions:", "Let me give you some small suggestions:"]
give_2 = [
    "Great! There is one point I think you could improve:",
    "Nice! Maybe you would like to try:",
    "Good! One small tip:",
    "Good job! I have one suggestion that could help you:",
    "Well done! I think you could further improve the comment like:",
]
give_3 = [
    "Excellent! You can polish the comment if you want:",
    "Really supportive! Here is a small tip:",
    "Amazing comment! Something you could further improve:",
]
more_detail = [
    "How about writing a little bit more detail?",
    "You can share more details.",
    "Try write down more stuffs.",
]
more_connect = [
    "Try support the help seeker using words like:",
    "Show connections to the help seeker, using words like:",
]
pnouns_1 = [
    "I",
    "I would",
    "I will",
    "I'm",
    "I have",
    "I would have",
    "I mean",
    "I might",
    "I think",
    "me",
    "myself",
    "my",
    "our",
    "ourselves",
    "us",
    "we",
    "we will",
    "we have",
]
pnouns_2 = [
    "I love you",
    "thank you",
    "u",
    "ur",
    "you are",
    "you will",
    "you would",
    "you have",
    "yourself",
    "your",
    "yours",
]
pnouns_3 = [
    "he will",
    "he is",
    "she will",
    "she would",
    "her",
    "his",
    "him",
    "himself",
    "their",
    "them",
    "themselves",
    "they",
    "they will",
    "they have",
    "they are",
]
pnouns_4 = [
    "others",
    "it",
    "itself",
    "no body",
    "someone",
    "something",
    "that",
    "that is",
    "that will",
    "this",
    "those",
    "what",
    "what's",
    "who",
    "who will",
    "whose",
]
more_experience = [
    "Share experience about yourself or others, like:",
    "Share knowledge learn from yourself or others, like:",
]
social_1 = [
    "uncle",
    "son",
    "sister",
    "brother",
    "parent",
    "nephew",
    "mother",
    "father",
    "mom",
    "marry",
    "grandfather",
    "grandmother",
    "dad",
    "family",
    "cousin",
    "baby",
    "aunt",
]
social_2 = [
    "beloved",
    "friend",
    "best friend",
    "boyfriend",
    "girlfriend",
    "buddy",
    "classmate",
    "colleague",
    "contact",
    "darling",
    "dear",
    "dude",
    "ex-boyfriend",
    "ex-girlfriend",
    "guy",
    "honney",
    "neighbor",
    "partner",
    "roommate",
    "sweetie",
]
more_positive = [
    "You can try to make it more positive with words like:",
    "More positive words can be used in the comment, like:",
]
positive_1 = [
    "accept",
    "active",
    "admire",
    "agree",
    "appreciate",
    "bless",
    "care",
    "encourage",
    "enjoy",
    "happy",
    "hope",
    "please",
    "share",
    "win",
    "wisdom",
]
positive_2 = [
    "advantage",
    "benefit",
    "cheer",
    "easy",
    "fun",
    "good",
    "great",
    "healthy",
    "interest",
    "joy",
    "pretty",
    "super",
    "support",
]
positive_3 = [
    "amazing",
    "awesome",
    "beautiful",
    "best",
    "bright",
    "comfortable",
    "cool",
    "exciting",
    "excellent",
    "fantasy",
    "favor",
    "helpful",
    "honest",
    "important",
    "inspire",
    "nice",
    "peace",
    "thankful",
    "thanks",
    "thx",
    "useful",
    "value",
    "warm",
    "well",
    "welcome",
    "wonderful",
    "worthwhile",
]
positive_4 = [
    "bold",
    "brave",
    "calm",
    "certain",
    "clever",
    "confident",
    "haha",
    "hero",
    "laugh",
    "like",
    "lucky",
    "perfect",
    "positive",
    "proud",
    "safe",
    "smart",
    "strong",
    "success",
    "trust",
]


def get_bert_embeddings(texts):
    if isinstance(texts, str):
        texts = [texts]
    return embedding_model.encode(texts, convert_to_numpy=True)


# Initialize Elasticsearch
es = Elasticsearch(
    hosts=[
        "http://127.0.0.1:9200"
    ]  # Changed to HTTP, removed http_auth and verify_certs
)

# Feedback scripts and word lists for recommendation (from recommend_backend.py)
feedback_scripts_recommend = [
    "<b> I found some good comments with highlighted words that could help you reflect: </b>",
    "<b> I found some highly rated examples with parts of interests highlighted: </b>",
    "<b> Here are some relevant good comments for your reference: </b>",
    "<b> You may want to take a look at these highly rated comments: </b>",
    "<b> I got some great relevant comments with highlighted words for you: </b>",
]
mark_word_list_1 = pnouns_1 + pnouns_2 + pnouns_3 + pnouns_4
mark_word_list_2 = social_1 + social_2
positive_1_rec = [
    "accepted",
    "accept",
    "active",
    "admire",
    "agreed",
    "agree",
    "appreciate",
    "bless",
    "care",
    "encourage",
    "enjoy",
    "happy",
    "hope",
    "please",
    "share",
    "win",
    "wisdom",
]
positive_2_rec = [
    "advantages",
    "advantage",
    "benefits",
    "benefit",
    "cheer",
    "easy",
    "easiest",
    "funny",
    "fun",
    "good",
    "great",
    "healthy",
    "interest",
    "joy",
    "pretty",
    "super",
    "support",
]
positive_3_rec = [
    "amazing",
    "awesome",
    "beautiful",
    "better",
    "best",
    "bright",
    "comfortable",
    "cool",
    "exciting",
    "excited",
    "excellent",
    "fantasy",
    "favor",
    "helpful",
    "honest",
    "important",
    "inspired",
    "inspiring",
    "inspire",
    "nice",
    "peaceful",
    "peace",
    "thankful",
    "thanks",
    "thx",
    "useful",
    "valuable",
    "value",
    "warm",
    "well",
    "welcome",
    "wonderful",
    "worthwhile",
]
positive_4_rec = [
    "bold",
    "brave",
    "calm",
    "certain",
    "clever",
    "confident",
    "haha",
    "smile",
    "hero",
    "laugh",
    "like",
    "lucky",
    "perfect",
    "positive",
    "pround",
    "safe",
    "smart",
    "strong",
    "success",
    "trust",
]
mark_word_list_3 = positive_1_rec + positive_2_rec + positive_3_rec + positive_4_rec

app = Flask(
    __name__,
    static_url_path="",
    template_folder="templates",
    static_folder="static",
)
CORS(app)
# app._static_folder = "static" # Removed as we set it in constructor


@app.route("/")
def index():
    return render_template("index.html")


# Global variables for experiment record and first click state
experiment_record_assess = []
experiment_record_recommend = []
first_click_assess = True
first_click_recommend = True


# Helper function to save assessment data
def save_assess_file(comment, record_list):
    file_name = "record/AF_record_" + str(int(time.time())) + ".json"
    payload = {
        "final_comment": comment,
        "record_count": len(record_list),
        "records": record_list,
    }
    with open(file_name, "w", encoding="utf_8_sig") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# Helper function to save recommendation data
def save_recommend_file(ori_op, comment, record_list):
    file_name = "record/RE_record_" + str(int(time.time())) + ".json"
    payload = {
        "final_comment": comment,
        "record_count": len(record_list),
        "records": record_list,
    }
    with open(file_name, "w", encoding="utf_8_sig") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


@app.route("/assess", methods=["POST"])
def assess_comment():
    global first_click_assess
    global experiment_record_assess
    if first_click_assess:
        experiment_record_assess = []
        first_click_assess = False

    # Handle JSON data (New Frontend)
    if request.is_json:
        data = request.get_json()
        ori_com = data.get("comment", "")
        click_event = data.get("click_event", "")
        op_text = data.get("op_text", "")
        is_final_submission = data.get("is_final", False)
    else:
        # Fallback for legacy support
        data = list(request.form.to_dict().keys())
        if not data:
            return jsonify({"error": "No data received"}), 400
        ori_com_raw = data[0]
        click_index = ori_com_raw.find("click event")
        ori_com = ori_com_raw[0:click_index] if click_index != -1 else ori_com_raw
        is_final_submission = False
        if ori_com_raw.startswith("Yeah final"):
            is_final_submission = True
            ori_com = (
                ori_com_raw[10:click_index] if click_index != -1 else ori_com_raw[10:]
            )
        op_text = ""
        click_event = ori_com_raw[click_index:-1] if click_index != -1 else ""

    click_time = time.time()
    print("Input comment (Assessment): {}".format(ori_com))

    # --- UPDATED: Use Backend API for Prediction ---
    try:
        response = requests.post(
            f"{BACKEND_URL}/predict_scores", json={"comment": ori_com}
        )
        if response.status_code == 200:
            result = response.json()
            IS_score = result.get("IS_score", 0)
            ES_score = result.get("ES_score", 0)
        else:
            print(f"Backend API Error: {response.status_code} - {response.text}")
            IS_score = 0
            ES_score = 0
    except Exception as e:
        print(f"Failed to connect to Backend API: {e}")
        IS_score = 0
        ES_score = 0

    # Get LIWC features for feedback logic (keeping existing feedback logic for now)
    features, details = get_vector(ori_com, LIWC_features)

    ran_pn1 = random.sample(range(len(pnouns_1)), 3)
    ran_pn2 = random.sample(range(len(pnouns_2)), 3)
    ran_pn3 = random.sample(range(len(pnouns_3)), 3)
    ran_pn4 = random.sample(range(len(pnouns_4)), 3)
    ran_so1 = random.sample(range(len(social_1)), 6)
    ran_so2 = random.sample(range(len(social_2)), 6)
    ran_po1 = random.sample(range(len(positive_1)), 3)
    ran_po2 = random.sample(range(len(positive_2)), 3)
    ran_po3 = random.sample(range(len(positive_3)), 3)
    ran_po4 = random.sample(range(len(positive_4)), 3)
    feedback_1 = ""
    score_pnouns = 0
    score_social = 0
    score_positive = 0
    if "ppron" in details.keys():
        score_pnouns = details["ppron"]
    if "social" in details.keys():
        score_social = details["social"]
    if "posemo" in details.keys():
        score_positive = details["posemo"]
    if IS_score < 3 and ES_score < 3:
        ran_0 = random.randint(0, 1)
        feedback_1 = give_1[random.randint(0, len(give_1) - 1)]
        if ran_0 == 0:
            ran = random.randint(0, 2)
            if ran == 0:
                feedback_2 = (
                    more_connect[random.randint(0, len(more_connect) - 1)]
                    + "<br>"
                    + "<br>"
                    + pnouns_1[ran_pn1[0]]
                    + ", "
                    + pnouns_1[ran_pn1[1]]
                    + ", "
                    + pnouns_1[ran_pn1[2]]
                    + ", "
                    + pnouns_2[ran_pn2[0]]
                    + ", "
                    + pnouns_2[ran_pn2[1]]
                    + ", "
                    + pnouns_2[ran_pn2[2]]
                    + ", "
                    + pnouns_3[ran_pn3[0]]
                    + ", "
                    + pnouns_3[ran_pn3[1]]
                    + ", "
                    + pnouns_3[ran_pn3[2]]
                    + ", "
                    + pnouns_4[ran_pn4[0]]
                    + ", "
                    + pnouns_4[ran_pn4[1]]
                    + ", "
                    + pnouns_4[ran_pn4[2]]
                    + "."
                )
            elif ran == 1:
                feedback_2 = (
                    more_experience[random.randint(0, len(more_experience) - 1)]
                    + "<br>"
                    + "<br>"
                    + social_1[ran_so1[0]]
                    + ", "
                    + social_1[ran_so1[1]]
                    + ", "
                    + social_1[ran_so1[2]]
                    + ", "
                    + social_1[ran_so1[3]]
                    + ", "
                    + social_2[ran_so2[0]]
                    + ", "
                    + social_2[ran_so2[1]]
                    + ", "
                    + social_2[ran_so2[2]]
                    + ", "
                    + social_2[ran_so2[3]]
                    + "."
                )
            else:
                feedback_2 = (
                    more_positive[random.randint(0, len(more_positive) - 1)]
                    + "<br>"
                    + "<br>"
                    + positive_1[ran_po1[0]]
                    + ", "
                    + positive_1[ran_po1[1]]
                    + ", "
                    + positive_1[ran_po1[2]]
                    + ", "
                    + positive_2[ran_po2[0]]
                    + ", "
                    + positive_2[ran_po2[1]]
                    + ", "
                    + positive_2[ran_po2[2]]
                    + ", "
                    + positive_3[ran_po3[0]]
                    + ", "
                    + positive_3[ran_po3[1]]
                    + ", "
                    + positive_3[ran_po3[2]]
                    + ", "
                    + positive_4[ran_po4[0]]
                    + ", "
                    + positive_4[ran_po4[1]]
                    + ", "
                    + positive_4[ran_po4[2]]
                    + "."
                )
        else:
            if score_pnouns == min(score_pnouns, score_positive, score_social):
                feedback_2 = (
                    more_connect[random.randint(0, len(more_connect) - 1)]
                    + "<br>"
                    + "<br>"
                    + pnouns_1[ran_pn1[0]]
                    + ", "
                    + pnouns_1[ran_pn1[1]]
                    + ", "
                    + pnouns_1[ran_pn1[2]]
                    + ", "
                    + pnouns_2[ran_pn2[0]]
                    + ", "
                    + pnouns_2[ran_pn2[1]]
                    + ", "
                    + pnouns_2[ran_pn2[2]]
                    + ", "
                    + pnouns_3[ran_pn3[0]]
                    + ", "
                    + pnouns_3[ran_pn3[1]]
                    + ", "
                    + pnouns_3[ran_pn3[2]]
                    + ", "
                    + pnouns_4[ran_pn4[0]]
                    + ", "
                    + pnouns_4[ran_pn4[1]]
                    + ", "
                    + pnouns_4[ran_pn4[2]]
                    + "."
                )
            elif score_social == min(score_pnouns, score_positive, score_social):
                feedback_2 = (
                    more_experience[random.randint(0, len(more_experience) - 1)]
                    + "<br>"
                    + "<br>"
                    + social_1[ran_so1[0]]
                    + ", "
                    + social_1[ran_so1[1]]
                    + ", "
                    + social_1[ran_so1[2]]
                    + ", "
                    + social_1[ran_so1[3]]
                    + ", "
                    + social_2[ran_so2[0]]
                    + ", "
                    + social_2[ran_so2[1]]
                    + ", "
                    + social_2[ran_so2[2]]
                    + ", "
                    + social_2[ran_so2[3]]
                    + "."
                )
            else:
                feedback_2 = (
                    more_positive[random.randint(0, len(more_positive) - 1)]
                    + "<br>"
                    + "<br>"
                    + positive_1[ran_po1[0]]
                    + ", "
                    + positive_1[ran_po1[1]]
                    + ", "
                    + positive_1[ran_po1[2]]
                    + ", "
                    + positive_2[ran_po2[0]]
                    + ", "
                    + positive_2[ran_po2[1]]
                    + ", "
                    + positive_2[ran_po2[2]]
                    + ", "
                    + positive_3[ran_po3[0]]
                    + ", "
                    + positive_3[ran_po3[1]]
                    + ", "
                    + positive_3[ran_po3[2]]
                    + ", "
                    + positive_4[ran_po4[0]]
                    + ", "
                    + positive_4[ran_po4[1]]
                    + ", "
                    + positive_4[ran_po4[2]]
                    + "."
                )
    elif IS_score == 3 and ES_score == 3:
        ran_0 = random.randint(0, 1)
        feedback_1 = give_3[random.randint(0, len(give_3) - 1)]
        if ran_0 == 0:
            ran = random.randint(0, 2)
            if ran == 0:
                feedback_2 = (
                    more_connect[random.randint(0, len(more_connect) - 1)]
                    + "<br>"
                    + "<br>"
                    + pnouns_1[ran_pn1[0]]
                    + ", "
                    + pnouns_1[ran_pn1[1]]
                    + ", "
                    + pnouns_1[ran_pn1[2]]
                    + ", "
                    + pnouns_2[ran_pn2[0]]
                    + ", "
                    + pnouns_2[ran_pn2[1]]
                    + ", "
                    + pnouns_2[ran_pn2[2]]
                    + ", "
                    + pnouns_3[ran_pn3[0]]
                    + ", "
                    + pnouns_3[ran_pn3[1]]
                    + ", "
                    + pnouns_3[ran_pn3[2]]
                    + ", "
                    + pnouns_4[ran_pn4[0]]
                    + ", "
                    + pnouns_4[ran_pn4[1]]
                    + ", "
                    + pnouns_4[ran_pn4[2]]
                    + "."
                )
            elif ran == 1:
                feedback_2 = (
                    more_experience[random.randint(0, len(more_experience) - 1)]
                    + "<br>"
                    + "<br>"
                    + social_1[ran_so1[0]]
                    + ", "
                    + social_1[ran_so1[1]]
                    + ", "
                    + social_1[ran_so1[2]]
                    + ", "
                    + social_1[ran_so1[3]]
                    + ", "
                    + social_2[ran_so2[0]]
                    + ", "
                    + social_2[ran_so2[1]]
                    + ", "
                    + social_2[ran_so2[2]]
                    + ", "
                    + social_2[ran_so2[3]]
                    + "."
                )
            else:
                feedback_2 = (
                    more_positive[random.randint(0, len(more_positive) - 1)]
                    + "<br>"
                    + "<br>"
                    + positive_1[ran_po1[0]]
                    + ", "
                    + positive_1[ran_po1[1]]
                    + ", "
                    + positive_1[ran_po1[2]]
                    + ", "
                    + positive_2[ran_po2[0]]
                    + ", "
                    + positive_2[ran_po2[1]]
                    + ", "
                    + positive_2[ran_po2[2]]
                    + ", "
                    + positive_3[ran_po3[0]]
                    + ", "
                    + positive_3[ran_po3[1]]
                    + ", "
                    + positive_3[ran_po3[2]]
                    + ", "
                    + positive_4[ran_po4[0]]
                    + ", "
                    + positive_4[ran_po4[1]]
                    + ", "
                    + positive_4[ran_po4[2]]
                    + "."
                )
        else:
            if score_pnouns == min(score_pnouns, score_positive, score_social):
                feedback_2 = (
                    more_connect[random.randint(0, len(more_connect) - 1)]
                    + "<br>"
                    + "<br>"
                    + pnouns_1[ran_pn1[0]]
                    + ", "
                    + pnouns_1[ran_pn1[1]]
                    + ", "
                    + pnouns_1[ran_pn1[2]]
                    + ", "
                    + pnouns_2[ran_pn2[0]]
                    + ", "
                    + pnouns_2[ran_pn2[1]]
                    + ", "
                    + pnouns_2[ran_pn2[2]]
                    + ", "
                    + pnouns_3[ran_pn3[0]]
                    + ", "
                    + pnouns_3[ran_pn3[1]]
                    + ", "
                    + pnouns_3[ran_pn3[2]]
                    + ", "
                    + pnouns_4[ran_pn4[0]]
                    + ", "
                    + pnouns_4[ran_pn4[1]]
                    + ", "
                    + pnouns_4[ran_pn4[2]]
                    + "."
                )
            elif score_social == min(score_pnouns, score_positive, score_social):
                feedback_2 = (
                    more_experience[random.randint(0, len(more_experience) - 1)]
                    + "<br>"
                    + "<br>"
                    + social_1[ran_so1[0]]
                    + ", "
                    + social_1[ran_so1[1]]
                    + ", "
                    + social_1[ran_so1[2]]
                    + ", "
                    + social_1[ran_so1[3]]
                    + ", "
                    + social_2[ran_so2[0]]
                    + ", "
                    + social_2[ran_so2[1]]
                    + ", "
                    + social_2[ran_so2[2]]
                    + ", "
                    + social_2[ran_so2[3]]
                    + "."
                )
            else:
                feedback_2 = (
                    more_positive[random.randint(0, len(more_positive) - 1)]
                    + "<br>"
                    + "<br>"
                    + positive_1[ran_po1[0]]
                    + ", "
                    + positive_1[ran_po1[1]]
                    + ", "
                    + positive_1[ran_po1[2]]
                    + ", "
                    + positive_2[ran_po2[0]]
                    + ", "
                    + positive_2[ran_po2[1]]
                    + ", "
                    + positive_2[ran_po2[2]]
                    + ", "
                    + positive_3[ran_po3[0]]
                    + ", "
                    + positive_3[ran_po3[1]]
                    + ", "
                    + positive_3[ran_po3[2]]
                    + ", "
                    + positive_4[ran_po4[0]]
                    + ", "
                    + positive_4[ran_po4[1]]
                    + ", "
                    + positive_4[ran_po4[2]]
                    + "."
                )
    else:
        ran_0 = random.randint(0, 1)
        feedback_1 = give_2[random.randint(0, len(give_2) - 1)]
        if ran_0 == 0:
            ran = random.randint(0, 1)
            if IS_score == 3:
                if ran == 0:
                    feedback_2 = (
                        more_connect[random.randint(0, len(more_connect) - 1)]
                        + "<br>"
                        + "<br>"
                        + pnouns_1[ran_pn1[0]]
                        + ", "
                        + pnouns_1[ran_pn1[1]]
                        + ", "
                        + pnouns_1[ran_pn1[2]]
                        + ", "
                        + pnouns_2[ran_pn2[0]]
                        + ", "
                        + pnouns_2[ran_pn2[1]]
                        + ", "
                        + pnouns_2[ran_pn2[2]]
                        + ", "
                        + pnouns_3[ran_pn3[0]]
                        + ", "
                        + pnouns_3[ran_pn3[1]]
                        + ", "
                        + pnouns_3[ran_pn3[2]]
                        + ", "
                        + pnouns_4[ran_pn4[0]]
                        + ", "
                        + pnouns_4[ran_pn4[1]]
                        + ", "
                        + pnouns_4[ran_pn4[2]]
                        + "."
                    )
                if ran == 1:
                    feedback_2 = (
                        more_positive[random.randint(0, len(more_positive) - 1)]
                        + "<br>"
                        + "<br>"
                        + social_1[ran_so1[0]]
                        + ", "
                        + social_1[ran_so1[1]]
                        + ", "
                        + social_1[ran_so1[2]]
                        + ", "
                        + social_1[ran_so1[3]]
                        + ", "
                        + social_2[ran_so2[0]]
                        + ", "
                        + social_2[ran_so2[1]]
                        + ", "
                        + social_2[ran_so2[2]]
                        + ", "
                        + social_2[ran_so2[3]]
                        + "."
                    )
            else:
                if ran == 0:
                    feedback_2 = (
                        more_positive[random.randint(0, len(more_positive) - 1)]
                        + "<br>"
                        + "<br>"
                        + positive_1[ran_po1[0]]
                        + ", "
                        + positive_1[ran_po1[1]]
                        + ", "
                        + positive_1[ran_po1[2]]
                        + ", "
                        + positive_2[ran_po2[0]]
                        + ", "
                        + positive_2[ran_po2[1]]
                        + ", "
                        + positive_2[ran_po2[2]]
                        + ", "
                        + positive_3[ran_po3[0]]
                        + ", "
                        + positive_3[ran_po3[1]]
                        + ", "
                        + positive_3[ran_po3[2]]
                        + ", "
                        + positive_4[ran_po4[0]]
                        + ", "
                        + positive_4[ran_po4[1]]
                        + ", "
                        + positive_4[ran_po4[2]]
                        + "."
                    )
                if ran == 1:
                    feedback_2 = (
                        more_experience[random.randint(0, len(more_experience) - 1)]
                        + "<br>"
                        + "<br>"
                        + social_1[ran_so1[0]]
                        + ", "
                        + social_1[ran_so1[1]]
                        + ", "
                        + social_1[ran_so1[2]]
                        + ", "
                        + social_1[ran_so1[3]]
                        + ", "
                        + social_2[ran_so2[0]]
                        + ", "
                        + social_2[ran_so2[1]]
                        + ", "
                        + social_2[ran_so2[2]]
                        + ", "
                        + social_2[ran_so2[3]]
                        + "."
                    )
        else:
            if IS_score == 3:
                if score_pnouns == min(score_pnouns, score_positive, score_social):
                    feedback_2 = (
                        more_connect[random.randint(0, len(more_connect) - 1)]
                        + "<br>"
                        + "<br>"
                        + pnouns_1[ran_pn1[0]]
                        + ", "
                        + pnouns_1[ran_pn1[1]]
                        + ", "
                        + pnouns_1[ran_pn1[2]]
                        + ", "
                        + pnouns_2[ran_pn2[0]]
                        + ", "
                        + pnouns_2[ran_pn2[1]]
                        + ", "
                        + pnouns_2[ran_pn2[2]]
                        + ", "
                        + pnouns_3[ran_pn3[0]]
                        + ", "
                        + pnouns_3[ran_pn3[1]]
                        + ", "
                        + pnouns_3[ran_pn3[2]]
                        + ", "
                        + pnouns_4[ran_pn4[0]]
                        + ", "
                        + pnouns_4[ran_pn4[1]]
                        + ", "
                        + pnouns_4[ran_pn4[2]]
                        + "."
                    )
                else:
                    feedback_2 = (
                        more_positive[random.randint(0, len(more_positive) - 1)]
                        + "<br>"
                        + "<br>"
                        + social_1[ran_so1[0]]
                        + ", "
                        + social_1[ran_so1[1]]
                        + ", "
                        + social_1[ran_so1[2]]
                        + ", "
                        + social_1[ran_so1[3]]
                        + ", "
                        + social_2[ran_so2[0]]
                        + ", "
                        + social_2[ran_so2[1]]
                        + ", "
                        + social_2[ran_so2[2]]
                        + ", "
                        + social_2[ran_so2[3]]
                        + "."
                    )
            else:
                if score_positive == min(score_pnouns, score_positive, score_social):
                    feedback_2 = (
                        more_positive[random.randint(0, len(more_positive) - 1)]
                        + "<br>"
                        + "<br>"
                        + positive_1[ran_po1[0]]
                        + ", "
                        + positive_1[ran_po1[1]]
                        + ", "
                        + positive_1[ran_po1[2]]
                        + ", "
                        + positive_2[ran_po2[0]]
                        + ", "
                        + positive_2[ran_po2[1]]
                        + ", "
                        + positive_2[ran_po2[2]]
                        + ", "
                        + positive_3[ran_po3[0]]
                        + ", "
                        + positive_3[ran_po3[1]]
                        + ", "
                        + positive_3[ran_po3[2]]
                        + ", "
                        + positive_4[ran_po4[0]]
                        + ", "
                        + positive_4[ran_po4[1]]
                        + ", "
                        + positive_4[ran_po4[2]]
                        + "."
                    )
                else:
                    feedback_2 = (
                        more_experience[random.randint(0, len(more_experience) - 1)]
                        + "<br>"
                        + "<br>"
                        + social_1[ran_so1[0]]
                        + ", "
                        + social_1[ran_so1[1]]
                        + ", "
                        + social_1[ran_so1[2]]
                        + ", "
                        + social_1[ran_so1[3]]
                        + ", "
                        + social_2[ran_so2[0]]
                        + ", "
                        + social_2[ran_so2[1]]
                        + ", "
                        + social_2[ran_so2[2]]
                        + ", "
                        + social_2[ran_so2[3]]
                        + "."
                    )
    feedback_1_1 = more_detail[random.randint(0, len(more_detail) - 1)]
    tknzr = TweetTokenizer()
    words = tknzr.tokenize(ori_com)
    num_word = len(words)
    if num_word < 60 and first_click_assess:
        feedback_2 = feedback_1_1 + "<br>" + feedback_2
        first_click_assess = False
    one_record = {
        "timestamp": click_time,
        "input_comment": ori_com,
        "IS_score": int(IS_score),
        "ES_score": int(ES_score),
        "feedback_1": feedback_1,
        "feedback_2": feedback_2,
        "details": str(details),
    }
    experiment_record_assess.append(one_record)
    print("Assessment took {} s".format(time.time() - click_time))
    if is_final_submission:
        save_assess_file(ori_com, experiment_record_assess)
        experiment_record_assess = []
        first_click_assess = True
    return jsonify(
        {
            "mode": "AF",
            "IS_score": int(IS_score),
            "ES_score": int(ES_score),
            "feedback_1": feedback_1,
            "feedback_2": feedback_2,
            "details": details,
        }
    )


openai.api_key = os.getenv(
    "OPENAI_API_KEY"
)  # Make sure to set your API key as an environment variable


def polish_comment(comment, focus):
    if focus == "positive":
        prompt = (
            "You are an expert in supportive communication. "
            "Polish the following comment by increasing the use of positive emotion words "
            "(such as 'accept', 'active', 'admire', 'agree', 'appreciate', 'bless', 'care', 'encourage', 'enjoy', 'happy', 'hope', 'please', 'share', 'win', 'wisdom', 'advantage', 'benefit', 'cheer', 'easy', 'fun', 'good', 'great', 'healthy', 'interest', 'joy', 'pretty', 'super', 'support', 'amazing', 'awesome', 'beautiful', 'best', 'bright', 'comfortable', 'cool', 'exciting', 'excellent', 'fantasy', 'favor', 'helpful', 'honest', 'important', 'inspire', 'nice', 'peace', 'thankful', 'thanks', 'thx', 'useful', 'value', 'warm', 'well', 'welcome', 'wonderful', 'worthwhile', 'bold', 'brave', 'calm', 'certain', 'clever', 'confident', 'haha', 'hero', 'laugh', 'like', 'lucky', 'perfect', 'positive', 'proud', 'safe', 'smart', 'strong', 'success', 'trust', etc.), "
            "but do not change the original meaning, intent, or style. "
            "Do not add unrelated content. Only enhance positivity where appropriate.\n\n"
            f"Original comment:\n{comment}\n\nPolished comment:"
        )
    elif focus == "pronouns":
        prompt = (
            "You are an expert in supportive communication. "
            "Polish the following comment by increasing the use of personal pronouns "
            "(such as 'I', 'I would', 'I will', 'I'm', 'I have', 'I would have', 'I mean', 'I might', 'I think', 'me', 'myself', 'my', 'our', 'ourselves', 'us', 'we', 'we will', 'we have', 'I love you', 'thank you', 'u', 'ur', 'you are', 'you will', 'you would', 'you have', 'yourself', 'your', 'yours', 'he will', 'he is', 'she will', 'she would', 'her', 'his', 'him', 'himself', 'their', 'them', 'themselves', 'they', 'they will', 'they have', 'they are', 'others', 'it', 'itself', 'no body', 'someone', 'something', 'that', 'that is', 'that will', 'this', 'those', 'what','who', 'who will', 'whose', etc.), "
            "to make it feel more personal and connected. "
            "Do not change the original meaning, intent, or style. "
            "Do not add unrelated content. Only enhance personal connection where appropriate.\n\n"
            f"Original comment:\n{comment}\n\nPolished comment:"
        )
    elif focus == "family":
        prompt = (
            "You are an expert in supportive communication. "
            "Polish the following comment by increasing the use of family, friend, or social words "
            "(such as 'uncle', 'son', 'sister', 'brother', 'parent', 'nephew', 'mother', 'father', 'mom', 'marry', 'grandfather', 'grandmother', 'dad', 'family', 'cousin', 'baby', 'aunt', 'beloved', 'friend', 'best friend', 'boyfriend', 'girlfriend', 'buddy', 'classmate', 'colleague', 'contact', 'darling', 'dear', 'dude', 'ex-boyfriend', 'ex-girlfriend', 'guy', 'honney', 'neighbor', 'partner', 'roommate', 'sweetie', etc.), "
            "but do not change the original meaning, intent, or style. "
            "Do not add unrelated content. Only enhance the sense of social support where appropriate.\n\n"
            f"Original comment:\n{comment}\n\nPolished comment:"
        )
    else:
        prompt = comment

    client = openai.OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com/v1",
    )

    response = client.chat.completions.create(
        model="deepseek-chat",  # Use "deepseek-chat" for general tasks
        messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant that polishes user comments.",
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=200,
        temperature=0.5,
    )
    return response.choices[0].message.content.strip()


def _canonical_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _rule_based_polish(comment, focus):
    base = str(comment or "").strip()
    if not base:
        return ""

    if focus == "positive":
        addon = " I hope things get better soon, and you deserve support and care."
    elif focus == "pronouns":
        addon = (
            " I hear you, and I think you are taking a strong step by asking for help."
        )
    else:  # family
        addon = " If possible, reach out to a trusted friend or family member for extra support."

    if addon.strip().lower() in base.lower():
        return base
    return f"{base}{addon}"


def _ensure_distinct_candidates(candidates_data, ori_com):
    focus_map = {
        "candidate_1": "pronouns",
        "candidate_2": "family",
        "candidate_3": "positive",
    }
    normalized = {}
    seen = set()
    original_key = _canonical_text(ori_com)

    for key in ["candidate_1", "candidate_2", "candidate_3"]:
        raw_text = (
            candidates_data.get(key, "") if isinstance(candidates_data, dict) else ""
        )
        text = str(raw_text or "").strip()

        if not text:
            text = _rule_based_polish(ori_com, focus_map[key])

        text_key = _canonical_text(text)
        if (
            text_key == original_key
            or text_key in seen
            or text_key.startswith("[content filtered")
        ):
            text = _rule_based_polish(ori_com, focus_map[key])
            text_key = _canonical_text(text)

        # Final uniqueness safeguard if two fallback strings still collide.
        if text_key in seen:
            text = f"{text} ({focus_map[key]} emphasis)"
            text_key = _canonical_text(text)

        normalized[key] = text
        seen.add(text_key)

    return normalized


@app.route("/recommend", methods=["POST"])
def recommend_comment():
    global first_click_recommend
    global experiment_record_recommend
    if first_click_recommend:
        experiment_record_recommend = []
        first_click_recommend = False

    # Handle JSON data (New Frontend)
    if request.is_json:
        data = request.get_json()
        ori_com = data.get("comment", "")
        click_event = data.get("click_event", "")
        op_text = data.get("op_text", "")
        is_final_submission = data.get("is_final", False)
    else:
        # Fallback for legacy support
        data = list(request.form.to_dict().keys())
        if not data:
            return jsonify({"error": "No data received"}), 400
        ori_com_raw = data[0]
        click_index = ori_com_raw.find("click event")
        ori_com = ori_com_raw[0:click_index] if click_index != -1 else ori_com_raw
        is_final_submission = False
        if ori_com_raw.startswith("Yeah final"):
            is_final_submission = True
            ori_com = (
                ori_com_raw[10:click_index] if click_index != -1 else ori_com_raw[10:]
            )
        op_text = (
            "I am feeling very down and need some support."  # Placeholder for legacy
        )
        click_event = ori_com_raw[click_index:-1] if click_index != -1 else ""

    click_time = time.time()
    print("Input comment (Recommendation): {}".format(ori_com))

    # --- UPDATED: Use Generative Recommender ---
    # Note: We need the OP text for context.
    # Since the current frontend doesn't send OP text, we'll use a placeholder or modify frontend later.
    # For now, we'll assume a generic context or try to infer it if possible.
    # Ideally, the frontend should send the OP text.

    # Use received OP text if available, otherwise fallback
    if not op_text:
        op_text = "I am feeling very down and need some support."

    # --- UPDATED: Use Backend API for Recommendation & Safety ---
    # Skip generation if this is a final submission to save time
    if not is_final_submission:
        try:
            response = requests.post(
                f"{BACKEND_URL}/recommend_candidates",
                json={"op_text": op_text, "comment": ori_com},
            )

            if response.status_code == 200:
                result = response.json()

                # Check input safety
                if not result.get("is_safe", True):
                    safety_reason = result.get("reason", "Unknown")
                    print(f"Unsafe input detected: {safety_reason}")
                    return jsonify(
                        {
                            "mode": "RE",
                            "feedback": f"<b style='color: red;'>Safety Alert:</b> Your comment was flagged as potentially unsafe ({safety_reason}). Please revise it to be more supportive.",
                            "0": ori_com,
                            "0_description": "Original comment (Unsafe)",
                            "1": ori_com,
                            "1_description": "Original comment (Unsafe)",
                            "2": ori_com,
                            "2_description": "Original comment (Unsafe)",
                        }
                    )

                candidates_data = result.get("candidates", {})
            else:
                print(f"Backend API Error: {response.status_code} - {response.text}")
                candidates_data = {}

        except Exception as e:
            print(f"Failed to connect to Backend API: {e}")
            candidates_data = {}
    else:
        # For final submission, skip generation
        candidates_data = {
            "candidate_1": ori_com,
            "candidate_2": ori_com,
            "candidate_3": ori_com,
        }

    candidates_data = _ensure_distinct_candidates(candidates_data, ori_com)

    feedback = "<b>Here are three ways to polish your comment:</b>"
    candidates = {
        "mode": "RE",
        "feedback": feedback,
        "0": candidates_data.get("candidate_3", ori_com),  # Positive Words
        "0_description": "<span style='color: #28a745;'>This version uses more positive words.</span>",
        "1": candidates_data.get("candidate_1", ori_com),  # Personal Pronouns
        "1_description": "<span style='color: #28a745;'>This version uses more personal pronouns.</span>",
        "2": candidates_data.get("candidate_2", ori_com),  # Family/Friends
        "2_description": "<span style='color: #28a745;'>This version uses more family and friend words.</span>",
    }

    print("Recommendation took {} s".format(time.time() - click_time))
    one_record = {
        "timestamp": click_time,
        "input_comment": ori_com,
        "candidates": candidates,
        "feedback": feedback,
        "click_event": click_event,
    }
    experiment_record_recommend.append(one_record)
    if is_final_submission:
        save_recommend_file(ori_com, ori_com, experiment_record_recommend)
        experiment_record_recommend = []
        first_click_recommend = True
    return jsonify(candidates)


if __name__ == "__main__":
    if not os.path.exists("record"):
        os.makedirs("record")

    # Only start live-server and FastAPI backend in the original process (not the reloader)
    if os.environ.get("WERKZEUG_RUN_MAIN") is None:
        try:
            # Start FastAPI backend (backend_api.py) using uvicorn
            fastapi_process = subprocess.Popen(
                ["uvicorn", "backend_api:app", "--host", "127.0.0.1", "--port", "8000"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            print("FastAPI backend started on http://127.0.0.1:8000")
        except Exception as e:
            print(f"Failed to start FastAPI backend: {e}")

        try:
            live_server_process = subprocess.Popen(
                ["npx", "live-server", "forum_website", "--port=5500"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            print("live-server started on http://127.0.0.1:5000")
        except Exception as e:
            print(f"Failed to start live-server: {e}")

    app.run(debug=True, port=5000)
