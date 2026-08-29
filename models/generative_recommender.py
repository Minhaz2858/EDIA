import os
from openai import OpenAI
import json
import concurrent.futures
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings


class GenerativeRecommender:
    def __init__(self, api_key=None, base_url=None, model="gpt-3.5-turbo"):
        """
        Initializes the Generative Recommender.

        Args:
            api_key (str): OpenAI API Key. If None, tries to read from env 'OPENAI_API_KEY'.
            base_url (str): Optional base URL (e.g., for DeepSeek or other compatible APIs).
            model (str): The model name to use.
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url
        self.model = model

        if not self.api_key:
            print("Warning: No API Key provided for GenerativeRecommender.")

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

        # Initialize RAG Vector DB
        print("Initializing RAG Vector Database...")
        try:
            self.embedding_function = HuggingFaceEmbeddings(
                model_name="all-MiniLM-L6-v2"
            )
            self.vector_db = Chroma(
                persist_directory="chroma_db",
                embedding_function=self.embedding_function,
            )
            print("RAG Vector Database loaded successfully.")
        except Exception as e:
            print(f"Warning: Failed to load RAG Vector Database: {e}")
            self.vector_db = None

    def _get_rag_context(self, query_text, k=3):
        """
        Retrieves similar high-quality comments from the vector database.
        """
        if not self.vector_db:
            return ""

        try:
            results = self.vector_db.similarity_search(query_text, k=k)
            examples = "\n\n".join(
                [f"Example {i+1}: {doc.page_content}" for i, doc in enumerate(results)]
            )
            return f"\nHere are some examples of high-quality supportive comments from our database:\n{examples}\n"
        except Exception as e:
            print(f"Error retrieving RAG context: {e}")
            return ""

    def _generate_single_candidate(
        self, op_text, user_draft, constraint_type, constraint_desc
    ):
        """
        Helper function to generate a single candidate.
        """
        # Retrieve RAG context based on the OP text (to find similar situations)
        # or user draft (to find similar writing styles).
        # Using OP text is usually better to find relevant advice.
        rag_context = self._get_rag_context(op_text)

        system_prompt = f"""You are an expert mental health peer support assistant.
Your goal is to help users write supportive comments that are high in both Informational Support and Emotional Support.

You will be given an Original Post (OP) and a User's Draft Comment.
You must generate ONE polished version of the draft that follows this specific constraint:
**{constraint_type}**: {constraint_desc}

{rag_context}

The polished comment should be supportive, empathetic, and relevant to the OP.
Use the examples above as inspiration for tone and structure, but do not copy them directly.
Return ONLY the polished comment text. Do not include labels or explanations.
"""
        user_prompt = f"""
Original Post:
"{op_text}"

User's Draft:
"{user_draft}"

Generate the polished comment now.
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"Error generating {constraint_type}: {e}")
            return user_draft

    def generate_candidates(self, op_text, user_draft):
        """
        Generates 3 distinct polished versions of the user's draft in parallel.
        """
        constraints = [
            (
                "candidate_1",
                "Personal Pronouns",
                'Use "I", "we", "my", "our" to show personal connection and shared experience.',
            ),
            (
                "candidate_2",
                "Family/Friends",
                'Mention social support networks like "friend", "family", "partner", "parents" to encourage social connection.',
            ),
            (
                "candidate_3",
                "Positive Words",
                'Use uplifting words like "hope", "strength", "better", "support", "proud" to boost morale.',
            ),
        ]

        results = {}

        # Use ThreadPoolExecutor to run API calls in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_key = {
                executor.submit(
                    self._generate_single_candidate, op_text, user_draft, c_name, c_desc
                ): key
                for key, c_name, c_desc in constraints
            }

            for future in concurrent.futures.as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    data = future.result()
                    results[key] = data
                except Exception as exc:
                    print(f"{key} generated an exception: {exc}")
                    results[key] = user_draft

        return results
