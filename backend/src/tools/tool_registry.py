from langchain.agents import Tool
from backend.src.tools.market_tools import get_current_price

def get_tools():
    return [
        Tool(
            name="GetMarketPrice",
            func=lambda q: get_current_price(q.split(",")[0], q.split(",")[1] if "," in q else "China"),
            description="Useful for getting current market price. Input should be 'Product, Region' or just 'Product'."
        )
    ]
