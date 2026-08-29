import pandas as pd
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document
import os
import shutil


def build_vector_db():
    # 1. Load Data
    print("Loading dataset...")
    try:
        df = pd.read_csv("dataset.csv")
    except FileNotFoundError:
        print("Error: dataset.csv not found.")
        return

    # 2. Filter for High Quality Comments (IS >= 4 or ES >= 4)
    # We want to retrieve *good* examples to guide the LLM.
    # Adjust threshold as needed.
    # Note: The dataset seems to have lower scores on average.
    # Lowering threshold to >= 3 to ensure we get some examples.
    good_comments = df[(df["IS_rating"] >= 3) | (df["ES_rating"] >= 3)]
    print(f"Found {len(good_comments)} high-quality comments out of {len(df)}.")

    if len(good_comments) == 0:
        print("No high-quality comments found. Check dataset or thresholds.")
        return

    # 3. Create Documents
    documents = []
    for _, row in good_comments.iterrows():
        # Ensure content is string
        content = str(row["comment_body"])
        if len(content.split()) < 5:  # Skip very short comments
            continue

        doc = Document(
            page_content=content,
            metadata={
                "IS_rating": int(row["IS_rating"]),
                "ES_rating": int(row["ES_rating"]),
            },
        )
        documents.append(doc)

    print(f"Prepared {len(documents)} documents for indexing.")

    # 4. Initialize Embeddings
    print("Initializing Embeddings (all-MiniLM-L6-v2)...")
    # This uses the same model as app.py for consistency
    embedding_function = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

    # 5. Create and Persist Vector DB
    persist_directory = "chroma_db"

    # Clear existing DB if it exists to avoid duplicates/conflicts on re-run
    if os.path.exists(persist_directory):
        print(f"Removing existing database at {persist_directory}...")
        shutil.rmtree(persist_directory)

    print("Creating Vector Database (this may take a moment)...")
    db = Chroma.from_documents(
        documents=documents,
        embedding=embedding_function,
        persist_directory=persist_directory,
    )
    print(f"Success! Vector Database created at '{persist_directory}'")


if __name__ == "__main__":
    build_vector_db()
