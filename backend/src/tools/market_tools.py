from backend.src.domain.knowledge_graph import domain_graph
from backend.src.service.market_data_service import market_data


def get_current_price(product: str, region: str = "China") -> str:
    """
    Returns current real MySQL-backed market price.
    Region is currently informational only and does not change the data source.
    """
    query_lower = product.lower()
    target_node = None
    for node in domain_graph.nodes:
        if query_lower in node.name_en.lower() or query_lower in node.name_cn.lower():
            target_node = node
            break

    if not target_node:
        return f"Price data unavailable: product '{product}' not found."
    if not market_data.is_real_data(target_node.id):
        return f"Price data unavailable: no real MySQL market series for {target_node.name_en}."

    price = market_data.get_latest_price(target_node.id)
    history = market_data.get_price_history(target_node.id)
    date_str = history[-1]["date"] if history else "unknown"
    return f"{target_node.name_cn} ({target_node.name_en}) [{region}]: {price} {target_node.unit} on {date_str}"


def calculate_margin(price: float, cost: float) -> float:
    """
    Calculates profit margin.
    """
    return price - cost
