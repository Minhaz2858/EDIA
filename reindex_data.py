from elasticsearch import Elasticsearch, helpers
from sentence_transformers import SentenceTransformer
import csv
import os
import time

# --- Configuration ---
# Using localhost without auth for the containerized environment
ELASTICSEARCH_HOSTS = ["http://127.0.0.1:9200"]
INDEX_NAME = "is_es_3_256"
CSV_FILE_PATH = "dataset.csv"
BERT_VECTOR_DIMENSION = 384

# --- Initialize Clients ---
print("Connecting to Elasticsearch...")
es = Elasticsearch(
    hosts=ELASTICSEARCH_HOSTS,
    request_timeout=60,
)

# Wait for ES to be ready
for _ in range(30):
    try:
        if es.ping():
            print("Successfully connected to Elasticsearch.")
            break
    except Exception:
        pass
    time.sleep(2)
else:
    print("Could not connect to Elasticsearch after retries.")
    exit(1)

try:
    embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    print("Successfully loaded SentenceTransformer model 'all-MiniLM-L6-v2'.")
except Exception as e:
    print(f"Could not load SentenceTransformer model. Error: {e}")
    exit(1)


def create_index_with_mappings():
    """Creates the Elasticsearch index with specific mappings."""
    if es.indices.exists(index=INDEX_NAME):
        print(f"Index '{INDEX_NAME}' already exists. Skipping creation.")
        return

    mapping = {
        "mappings": {
            "properties": {
                "body": {"type": "text"},
                "bert_vec": {"type": "dense_vector", "dims": BERT_VECTOR_DIMENSION},
                "IS_rating": {"type": "integer"},
                "ES_rating": {"type": "integer"},
            }
        }
    }
    try:
        es.indices.create(index=INDEX_NAME, body=mapping)
        print(f"Index '{INDEX_NAME}' created successfully with mappings.")
    except Exception as e:
        print(f"Error creating index '{INDEX_NAME}': {e}")
        exit(1)


def generate_actions_from_csv():
    """Reads CSV, generates BERT vectors, and yields Elasticsearch bulk actions."""
    with open(CSV_FILE_PATH, mode="r", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            try:
                comment_body = row["comment_body"]
                # Handle potential missing or malformed data
                if not row["IS_rating"] or not row["ES_rating"]:
                    continue

                is_rating = int(row["IS_rating"])
                es_rating = int(row["ES_rating"])

                if not comment_body.strip():
                    continue

                bert_vector = embedding_model.encode(
                    [comment_body], convert_to_numpy=True
                )[0].tolist()

                document = {
                    "_index": INDEX_NAME,
                    "_source": {
                        "body": comment_body,
                        "bert_vec": bert_vector,
                        "IS_rating": is_rating,
                        "ES_rating": es_rating,
                    },
                }
                yield document
            except Exception as e:
                print(f"Error processing row: {e}")
                continue


if __name__ == "__main__":
    create_index_with_mappings()
    print("Indexing data...")
    try:
        helpers.bulk(es, generate_actions_from_csv())
        print("Data indexing complete.")
    except Exception as e:
        print(f"Error during bulk indexing: {e}")
