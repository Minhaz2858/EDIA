from elasticsearch import Elasticsearch, helpers
from sentence_transformers import SentenceTransformer
import csv
import os

# --- Configuration ---
# Your Elasticsearch instance runs on HTTPS with authentication.
# You must provide the path to the certificate authority (CA) file.
ELASTICSEARCH_HOSTS = [os.getenv("ELASTICSEARCH_HOST", "https://127.0.0.1:9200")]
ELASTIC_USERNAME = os.getenv("ELASTIC_USERNAME", "elastic")
ELASTIC_PASSWORD = os.getenv("ELASTIC_PASSWORD")
CA_CERTS_PATH = os.getenv("ELASTICSEARCH_CA_CERTS_PATH")

INDEX_NAME = "is_es_3_256"
CSV_FILE_PATH = "1.0.new_final - 1.0.new_final.csv.csv"
BERT_VECTOR_DIMENSION = 384

# --- Initialize Clients ---
try:
    if not os.path.exists(CA_CERTS_PATH):
        raise FileNotFoundError(f"CA certificate file not found at: {CA_CERTS_PATH}")

    es = Elasticsearch(
        hosts=ELASTICSEARCH_HOSTS,
        basic_auth=(ELASTIC_USERNAME, ELASTIC_PASSWORD),
        ca_certs=CA_CERTS_PATH,  # Use the CA certs to establish a secure, trusted connection
        request_timeout=60,
    )
    if not es.ping():
        raise ValueError("Connection to Elasticsearch failed!")
    print("Successfully connected to Elasticsearch.")
except FileNotFoundError as e:
    print(f"Error: {e}")
    exit()
except Exception as e:
    print(f"Could not connect to Elasticsearch: {e}")
    exit()

try:
    embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    embedding_model.encode(["test"])
    print("Successfully loaded SentenceTransformer model 'all-MiniLM-L6-v2'.")
except Exception as e:
    print(f"Could not load SentenceTransformer model. Error: {e}")
    exit()


def create_index_with_mappings():
    """Creates the Elasticsearch index with specific mappings."""
    if es.indices.exists(index=INDEX_NAME):
        print(f"Index '{INDEX_NAME}' already exists. Deleting it for a fresh start.")
        es.indices.delete(index=INDEX_NAME)

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
        exit()


def generate_actions_from_csv():
    """Reads CSV, generates BERT vectors, and yields Elasticsearch bulk actions."""
    with open(CSV_FILE_PATH, mode="r", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        count = 0
        for row in reader:
            try:
                comment_body = row["comment_body"]
                is_rating = int(row["IS_rating"])
                es_rating = int(row["ES_rating"])

                if not comment_body.strip():
                    print(f"Skipping empty comment body for row: {row}")
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
                count += 1
                if count % 100 == 0:
                    print(f"Prepared {count} documents for indexing...")

            except KeyError as e:
                print(f"Missing column in CSV: {e}. Row: {row}")
            except ValueError as e:
                print(f"Error converting rating to integer: {e}. Row: {row}")
            except Exception as e:
                print(f"An unexpected error occurred processing row {row}: {e}")
        print(f"Total documents prepared: {count}")


if __name__ == "__main__":
    create_index_with_mappings()

    print("Starting data indexing...")
    try:
        successes, errors = helpers.bulk(
            es, generate_actions_from_csv(), chunk_size=500
        )
        print(f"Successfully indexed {successes} documents.")
        if errors:
            print(f"Encountered {len(errors)} errors during indexing:")
            for i, error in enumerate(errors):
                print(f"Error {i+1}: {error}")
    except Exception as e:
        print(f"Bulk indexing failed: {e}")
