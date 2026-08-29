import json

def get_context_prompt(context_data: dict) -> str:
    """
    Takes a dictionary of business context and formats it as a structured JSON block
    within the prompt instructions.
    """
    context_str = json.dumps(context_data, indent=2, ensure_ascii=False)
    
    return f"""
BUSINESS CONTEXT:
The following JSON object defines the specific business context for this analysis.
You must adhere strictly to these parameters.

{context_str}

Use this context to tailor your analysis and recommendations.
"""
