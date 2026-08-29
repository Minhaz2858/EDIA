from backend.src.domain.models import (
    ProductNode,
    ProductType,
    Relationship,
    RelationshipType,
    KnowledgeGraph,
)


def build_c5_c9_graph() -> KnowledgeGraph:
    """
    Constructs the static domain knowledge graph for the C5/C9 industry.
    This acts as the 'Digital Twin' skeleton.
    """
    nodes = [
        # Upstream
        ProductNode(
            id="crude_oil",
            name_en="Brent Crude Oil",
            name_cn="布伦特原油",
            type=ProductType.CRUDE,
            description="MySQL-backed upstream Brent benchmark from the external market tables",
            unit="USD/bbl",
        ),
        ProductNode(
            id="naphtha",
            name_en="Japanese Naphtha",
            name_cn="日本石脑油",
            type=ProductType.FEEDSTOCK,
            unit="USD/ton",
        ),
        # Midstream base materials
        ProductNode(
            id="cracked_c5",
            name_en="Cracked C5",
            name_cn="工业用裂解碳五",
            type=ProductType.C5_MIX,
            description="Primary C5 feedstock from cracking",
        ),
        ProductNode(
            id="cracked_c9",
            name_en="Cracked C9",
            name_cn="工业用裂解碳九",
            type=ProductType.C9_DERIVATIVE,
            description="Primary C9 feedstock from cracking",
        ),
        # C5 Separation Derivatives (High Value)
        ProductNode(
            id="isoprene",
            name_en="Isoprene",
            name_cn="异戊二烯",
            type=ProductType.C5_DERIVATIVE,
        ),
        ProductNode(
            id="piperylene",
            name_en="Piperylene",
            name_cn="间戊二烯",
            type=ProductType.C5_DERIVATIVE,
        ),
        ProductNode(
            id="dcpd",
            name_en="DCPD",
            name_cn="双环戊二烯",
            type=ProductType.C5_DERIVATIVE,
        ),
        ProductNode(
            id="raffinate_c5",
            name_en="Raffinate C5",
            name_cn="抽余碳五",
            type=ProductType.C5_DERIVATIVE,
        ),
        # Downstream products
        ProductNode(
            id="sis",
            name_en="SIS",
            name_cn="SIS",
            type=ProductType.DOWNSTREAM,
            description="Styrene-isoprene-styrene",
        ),
        ProductNode(
            id="c5_resin",
            name_en="C5 Petroleum Resin",
            name_cn="碳五石油树脂",
            type=ProductType.DOWNSTREAM,
            description="Road marking paint, adhesives",
        ),
        ProductNode(
            id="blowing_agent",
            name_en="Pentane Blowing Agent",
            name_cn="戊烷发泡剂",
            type=ProductType.DOWNSTREAM,
        ),
        ProductNode(
            id="styrene",
            name_en="Styrene",
            name_cn="苯乙烯",
            type=ProductType.DOWNSTREAM,
        ),
        # C9 products
        ProductNode(
            id="hydrogenated_c9",
            name_en="Hydrogenated C9",
            name_cn="加氢碳九",
            type=ProductType.C9_DERIVATIVE,
            description="Hydrogenated C9 product",
        ),
        ProductNode(
            id="trimethylbenzene",
            name_en="Trimethylbenzene",
            name_cn="混三甲苯",
            type=ProductType.C9_DERIVATIVE,
        ),
    ]

    edges = [
        # Feedstock to cracker outputs
        Relationship(
            source_id="crude_oil",
            target_id="naphtha",
            relation=RelationshipType.PRODUCES,
            yield_rate=0.25,
            description="Brent is the upstream cost benchmark for naphtha",
        ),
        Relationship(
            source_id="naphtha",
            target_id="cracked_c5",
            relation=RelationshipType.PRODUCES,
            yield_rate=0.18,
            description="Cracking byproduct",
        ),
        Relationship(
            source_id="naphtha",
            target_id="cracked_c9",
            relation=RelationshipType.PRODUCES,
            yield_rate=0.10,
            description="Cracking byproduct",
        ),
        # C5 separation logic
        Relationship(
            source_id="cracked_c5",
            target_id="isoprene",
            relation=RelationshipType.PRODUCES,
            yield_rate=0.15,
        ),
        Relationship(
            source_id="cracked_c5",
            target_id="piperylene",
            relation=RelationshipType.PRODUCES,
            yield_rate=0.10,
        ),
        Relationship(
            source_id="cracked_c5",
            target_id="dcpd",
            relation=RelationshipType.PRODUCES,
            yield_rate=0.18,
        ),
        Relationship(
            source_id="cracked_c5",
            target_id="raffinate_c5",
            relation=RelationshipType.PRODUCES,
            yield_rate=0.35,
        ),
        # Downstream Applications
        Relationship(
            source_id="isoprene", target_id="sis", relation=RelationshipType.CONSUMES
        ),
        Relationship(
            source_id="piperylene",
            target_id="c5_resin",
            relation=RelationshipType.CONSUMES,
        ),
        Relationship(
            source_id="raffinate_c5",
            target_id="blowing_agent",
            relation=RelationshipType.CONSUMES,
        ),
        Relationship(
            source_id="cracked_c9",
            target_id="hydrogenated_c9",
            relation=RelationshipType.CONSUMES,
        ),
        Relationship(
            source_id="cracked_c9",
            target_id="trimethylbenzene",
            relation=RelationshipType.CONSUMES,
        ),
    ]

    return KnowledgeGraph(nodes=nodes, edges=edges)


def load_platform_product_nodes() -> list:
    """Load admin-registered products from the SQLite DB as ProductNode objects."""
    try:
        from backend.src.db.sqlite_db import db as _sqlite_db
        rows = _sqlite_db.list_platform_products("org-default")
        nodes = []
        for row in rows:
            raw_type = str(row.get("product_type") or "downstream")
            module_type_map = {
                "upstream": ProductType.FEEDSTOCK,
                "midstream": ProductType.C5_DERIVATIVE,
                "downstream": ProductType.DOWNSTREAM,
            }
            try:
                product_type = module_type_map.get(raw_type) or ProductType(raw_type)
            except ValueError:
                product_type = ProductType.DOWNSTREAM
            nodes.append(
                ProductNode(
                    id=str(row["id"]),
                    name_en=str(row.get("name_en") or ""),
                    name_cn=str(row.get("name_cn") or ""),
                    type=product_type,
                    description=str(row.get("description") or "") or None,
                    unit=str(row.get("unit") or "CNY/ton"),
                )
            )
        return nodes
    except Exception:
        return []


def build_merged_graph() -> KnowledgeGraph:
    """Static C5/C9 graph merged with any admin-registered platform products."""
    static = build_c5_c9_graph()
    platform_nodes = load_platform_product_nodes()
    if not platform_nodes:
        return static
    existing_ids = {node.id for node in static.nodes}
    new_nodes = [n for n in platform_nodes if n.id not in existing_ids]
    return KnowledgeGraph(nodes=list(static.nodes) + new_nodes, edges=list(static.edges))


# Singleton instance — merged at startup
domain_graph = build_merged_graph()


def refresh_domain_graph() -> None:
    """Rebuild the module-level domain_graph after platform products change."""
    refreshed_graph = build_merged_graph()
    domain_graph.nodes = refreshed_graph.nodes
    domain_graph.edges = refreshed_graph.edges
