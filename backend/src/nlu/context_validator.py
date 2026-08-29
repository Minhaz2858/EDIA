from typing import List, Dict, Tuple
from backend.src.nlu.types import UserContext


class ContextValidator:
    """
    Checks if the extracted context is sufficient to proceed with decision generation.
    """

    REQUIRED_FIELDS = ["product", "period"]

    def validate(self, context: UserContext) -> Tuple[bool, List[str]]:
        """
        Returns (is_valid, list_of_missing_fields).
        """
        missing_fields = []

        if not context.product:
            # Try to infer product from industry or general context if missing
            pass  # Relaxed for now to allow broader questions

        if not context.period:
            # Default to "Current/Next Period" if not specified
            pass  # Relaxed for now

        # Fail only if NOTHING was extracted at all — the keyword fallback in
        # context_generator ensures this is extremely rare.
        if not context.product and not context.industry and not context.period:
            missing_fields.append("business context (product, industry, or period)")

        return (len(missing_fields) == 0, missing_fields)

    def generate_follow_up_question(self, missing_fields: List[str]) -> str:
        """
        Generates a question to ask the user for missing information.
        """
        if not missing_fields:
            return ""

        fields_str = ", ".join(missing_fields)
        return f"I need a bit more information to help you. Could you please specify the {fields_str}?"
