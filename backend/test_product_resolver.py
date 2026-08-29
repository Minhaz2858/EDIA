import pytest

from backend.src.domain.product_resolver import (
    extract_product_ids_in_text,
    product_id_to_context_label,
    resolve_product_id,
)
from backend.src.nlu.context_generator import _keyword_extract_products
from backend.src.tools import db_tools as db_tools_module


def test_resolve_product_id_aliases_and_typos():
    assert resolve_product_id("间戊二烯") == "piperylene"
    assert resolve_product_id("piperlyene") == "piperylene"
    assert resolve_product_id("mixed c5") == "cracked_c5"
    assert resolve_product_id("裂解碳九") == "cracked_c9"
    assert resolve_product_id("SIS thermoplastic elastomer") == "sis"


def test_extract_product_ids_in_text_keeps_query_order_and_dedup():
    text = "请分析裂解C5、异戊二烯和裂解C5的价格走势"
    assert extract_product_ids_in_text(text) == ["cracked_c5", "isoprene"]


def test_extract_product_ids_in_text_avoids_ascii_substring_false_positive():
    text = "i want full market analysis with drivers, outlook, and recommendations"
    assert "sis" not in extract_product_ids_in_text(text)


def test_context_generator_keyword_products_use_shared_resolver_labels():
    products = _keyword_extract_products("请比较piperlyene和混合碳五")
    assert products == ["Piperylene", "Mixed C5"]


def test_get_db_price_resolves_typo_alias_before_lookup(monkeypatch):
    monkeypatch.setattr(db_tools_module.market_data, "is_real_data", lambda _pid: True)
    monkeypatch.setattr(db_tools_module.market_data, "get_latest_price", lambda _pid: 9100.0)
    monkeypatch.setattr(
        db_tools_module.market_data,
        "get_price_history",
        lambda _pid: [
            {"date": "2026-04-01", "price": 9000.0},
            {"date": "2026-04-07", "price": 9100.0},
        ],
    )
    monkeypatch.setitem(db_tools_module.REAL_DATA_MAP, "piperylene", ("piperylene", "east_china"))

    out = db_tools_module.get_db_price("piperlyene", change_days=1, language="en")

    assert "Piperylene" in out
    assert "[src=market_prices" in out


def test_get_db_price_ethene_stays_out_of_scope_message():
    out = db_tools_module.get_db_price("ethene", language="en")
    assert "not a directly monitored/traded price series" in out


def test_platform_product_names_and_aliases_are_registered(monkeypatch):
    import backend.src.db.sqlite_db as sqlite_module
    import backend.src.domain.product_resolver as resolver

    class FakePlatformProductDb:
        def list_platform_products(self, org_id):
            assert org_id == "org-default"
            return [
                {
                    "id": "hydrogenated_c5_resin",
                    "name_en": "Hydrogenated C5 Resin",
                    "name_cn": "加氢碳五树脂",
                    "aliases": ["HC5 resin", "氢化碳五树脂"],
                }
            ]

    with monkeypatch.context() as patch:
        patch.setattr(sqlite_module, "db", FakePlatformProductDb())
        resolver.rebuild_product_resolver()

        assert resolver.resolve_product_id("Hydrogenated C5 Resin") == "hydrogenated_c5_resin"
        assert resolver.resolve_product_id("加氢碳五树脂") == "hydrogenated_c5_resin"
        assert resolver.resolve_product_id("HC5 resin") == "hydrogenated_c5_resin"

    resolver.rebuild_product_resolver()
