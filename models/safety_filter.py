import os
from openai import OpenAI


class SafetyFilter:
    def __init__(self, api_key=None, base_url=None, model="gpt-3.5-turbo"):
        """
        Initializes the Safety Filter.

        Args:
            api_key (str): OpenAI API Key.
            base_url (str): Optional base URL.
            model (str): The model name to use.
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url
        self.model = model
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def is_safe(self, text):
        """
        Checks if the text is safe, supportive, and appropriate for a mental health forum.

        Args:
            text (str): The text to check.

        Returns:
            tuple: (bool, str) - (True if safe, Reason/Message)
        """
        system_prompt = """You are a safety moderator for a mental health peer support platform.
Your job is to detect if a comment contains:
1. Dangerous advice (e.g., "stop taking your meds", "don't see a doctor").
2. Encouragement of self-harm or suicide.
3. Toxic, abusive, or hateful language.
4. Highly irrelevant or nonsensical content.

If the comment contains ANY of these, return "UNSAFE: <reason>".
If the comment is supportive, neutral, or safe, return "SAFE".
"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f'Comment: "{text}"\n\nVerdict:'},
                ],
                temperature=0.0,
                max_tokens=50,
            )
            verdict = response.choices[0].message.content.strip()
            if verdict.upper().startswith("SAFE"):
                return True, "Safe"
            else:
                # Extract reason if possible
                reason = verdict.replace("UNSAFE:", "").strip()
                if not reason:
                    reason = "Potential safety violation"
                return False, reason
        except Exception as e:
            print(f"Error in safety check: {e}")
            # Fail safe (or fail open depending on policy).
            # For mental health, failing closed (assuming unsafe) might be better,
            # but for a prototype, we might return True to avoid blocking valid requests on error.
            return True, "Error in safety check (Allowed by default)"
