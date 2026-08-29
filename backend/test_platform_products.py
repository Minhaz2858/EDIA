import uuid
from pathlib import Path

import pytest
from sqlalchemy import text


_AUTH_HEADERS = {"Authorization": "Bearer test-token"}


def _setup_product_server(tmp_path: Path, monkeypatch):
    pytest.importorskip("fastapi.testclient")
    from backend.src.db.sqlite_db import Database
    from backend.test_report_blocks import _import_server_with_stubs

    server = _import_server_with_stubs(monkeypatch)
    test_db = Database(db_url=f"sqlite:///{tmp_path / f'products_{uuid.uuid4().hex}.db'}")
    monkeypatch.setattr(
        test_db,
        "get_user_by_token",
        lambda token: {
            "id": "test-user",
            "username": "test-user",
            "organization_id": "org-test",
            "is_admin": True,
            "is_active": True,
            "must_change_password": False,
        }
        if token == "test-token"
        else None,
        raising=False,
    )
    monkeypatch.setattr(server, "db", test_db)
    monkeypatch.setattr(server, "_refresh_platform_product_runtime", lambda *args, **kwargs: None)
    return server


def test_platform_product_api_requires_description(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    server = _setup_product_server(tmp_path, monkeypatch)

    with TestClient(server.app) as client:
        response = client.post(
            "/api/platform/products",
            json={
                "id": "custom_product_without_description",
                "name_en": "Custom Product",
                "name_cn": "自定义产品",
                "product_type": "downstream",
            },
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "description is required"


def test_platform_product_api_accepts_one_localized_name(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    server = _setup_product_server(tmp_path, monkeypatch)

    with TestClient(server.app) as client:
        response = client.post(
            "/api/platform/products",
            json={
                "id": "custom_cn_only_product",
                "name_cn": "加氢碳五树脂",
                "product_type": "downstream",
                "description": "C5 下游树脂，用于胶粘剂和道路标线涂料。",
            },
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["name_cn"] == "加氢碳五树脂"
    assert body["name_en"] == "加氢碳五树脂"


def test_platform_product_api_rejects_missing_names(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    server = _setup_product_server(tmp_path, monkeypatch)

    with TestClient(server.app) as client:
        response = client.post(
            "/api/platform/products",
            json={
                "id": "custom_product_without_names",
                "product_type": "downstream",
                "description": "Product description is present but no name was provided.",
            },
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "name_en or name_cn is required"


def test_platform_product_api_cleans_payload_and_normalizes_type(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    server = _setup_product_server(tmp_path, monkeypatch)

    with TestClient(server.app) as client:
        response = client.post(
            "/api/platform/products",
            json={
                "id": "custom_hc5_resin",
                "name_en": " Hydrogenated C5 Resin ",
                "name_cn": " 加氢碳五树脂 ",
                "product_type": "unknown-module",
                "unit": " CNY/ton ",
                "description": " Downstream C5 resin used in adhesive applications. ",
                "aliases": [" HC5 resin ", "", "氢化碳五树脂"],
                "data_table": " market_prices ",
                "data_key": " custom_hc5_resin ",
                "display_in": ["dashboard", "", "reports"],
            },
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["name_en"] == "Hydrogenated C5 Resin"
    assert body["name_cn"] == "加氢碳五树脂"
    assert body["product_type"] == "downstream"
    assert body["description"] == "Downstream C5 resin used in adhesive applications."
    assert body["aliases"] == ["HC5 resin", "氢化碳五树脂"]
    assert body["data_table"] == "market_prices"
    assert body["data_key"] == "custom_hc5_resin"
    assert body["display_in"] == ["dashboard", "reports"]


def test_platform_product_data_mapping_preview_returns_market_price_summary(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    server = _setup_product_server(tmp_path, monkeypatch)
    with server.db.engine.begin() as conn:
      conn.execute(text("DROP TABLE IF EXISTS market_prices"))
      conn.execute(
          text(
              "CREATE TABLE market_prices ("
              "date TEXT NOT NULL, "
              "product_id TEXT NOT NULL, "
              "price_type TEXT NOT NULL, "
              "price REAL NOT NULL"
              ")"
          )
      )
      conn.execute(
          text(
              "INSERT INTO market_prices (date, product_id, price_type, price) "
              "VALUES (:date, :product_id, :price_type, :price)"
          ),
          [
              {"date": "2026-05-10", "product_id": "custom_hc5_resin", "price_type": "east_china", "price": 7100.0},
              {"date": "2026-05-11", "product_id": "custom_hc5_resin", "price_type": "east_china", "price": 7150.0},
          ],
      )

    with TestClient(server.app) as client:
        response = client.post(
            "/api/platform/products/preview-data-mapping",
            json={"data_table": "market_prices", "data_key": "custom_hc5_resin"},
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["row_count"] == 2
    assert body["latest_date"] == "2026-05-11"
    assert body["latest_price"] == 7150.0
    assert body["price_type"] == "east_china"
    assert len(body["mini_chart_data"]) == 2


def test_platform_product_data_mapping_preview_reports_missing_rows(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    server = _setup_product_server(tmp_path, monkeypatch)
    with server.db.engine.begin() as conn:
      conn.execute(text("DROP TABLE IF EXISTS market_prices"))
      conn.execute(
          text(
              "CREATE TABLE market_prices ("
              "date TEXT NOT NULL, "
              "product_id TEXT NOT NULL, "
              "price_type TEXT NOT NULL, "
              "price REAL NOT NULL"
              ")"
          )
      )

    with TestClient(server.app) as client:
        response = client.post(
            "/api/platform/products/preview-data-mapping",
            json={"data_table": "market_prices", "data_key": "missing_product"},
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["row_count"] == 0
    assert body["message"] == "No market price rows found for this mapping"


def test_available_platform_products_filters_by_display_and_data(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    server = _setup_product_server(tmp_path, monkeypatch)
    with server.db.engine.begin() as conn:
      conn.execute(text("DROP TABLE IF EXISTS market_prices"))
      conn.execute(
          text(
              "CREATE TABLE market_prices ("
              "date TEXT NOT NULL, "
              "product_id TEXT NOT NULL, "
              "price_type TEXT NOT NULL, "
              "price REAL NOT NULL"
              ")"
          )
      )
      conn.execute(
          text(
              "INSERT INTO market_prices (date, product_id, price_type, price) "
              "VALUES (:date, :product_id, :price_type, :price)"
          ),
          [
              {"date": "2026-05-10", "product_id": "phase2_visible", "price_type": "east_china", "price": 7100.0},
              {"date": "2026-05-11", "product_id": "phase2_visible", "price_type": "east_china", "price": 7150.0},
          ],
      )
    server.db.save_platform_product(
        product_id="phase2_visible",
        org_id="org-test",
        name_en="Phase 2 Visible",
        name_cn="二期可见产品",
        product_type="midstream",
        unit="CNY/ton",
        description="Mapped product with dashboard data.",
        aliases=[],
        data_table="market_prices",
        data_key="phase2_visible",
        display_in=["dashboard", "reports"],
        created_by="test-user",
    )
    server.db.save_platform_product(
        product_id="phase2_unmapped",
        org_id="org-test",
        name_en="Phase 2 Unmapped",
        name_cn="二期无数据产品",
        product_type="midstream",
        unit="CNY/ton",
        description="Dashboard product without usable data.",
        aliases=[],
        data_table="market_prices",
        data_key="missing_phase2_product",
        display_in=["dashboard"],
        created_by="test-user",
    )
    server.db.save_platform_product(
        product_id="phase2_reports_only",
        org_id="org-test",
        name_en="Phase 2 Reports Only",
        name_cn="二期仅报告产品",
        product_type="downstream",
        unit="CNY/ton",
        description="Mapped product that is not configured for dashboard display.",
        aliases=[],
        data_table="market_prices",
        data_key="phase2_visible",
        display_in=["reports"],
        created_by="test-user",
    )

    with TestClient(server.app) as client:
        response = client.get(
            "/api/platform/products/available?display_in=dashboard&require_data=true",
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body] == ["phase2_visible"]
    assert body[0]["product_type"] == "midstream"


def test_market_snapshot_includes_dashboard_platform_product(tmp_path, monkeypatch):
    server = _setup_product_server(tmp_path, monkeypatch)

    server.db.save_platform_product(
        product_id="phase2_snapshot_visible",
        org_id="org-default",
        name_en="Phase 2 Snapshot Visible",
        name_cn="二期快照可见产品",
        product_type="midstream",
        unit="CNY/ton",
        description="Mapped product that should appear in dashboard market snapshots.",
        aliases=[],
        data_table="market_prices",
        data_key="isoprene",
        display_in=["dashboard", "reports"],
        created_by="test-user",
    )

    class FakeMarketData:
        def get_price_history(self, product_id):
            if product_id == "phase2_snapshot_visible":
                return [
                    {"date": "2026-05-10", "price": 7100.0},
                    {"date": "2026-05-11", "price": 7150.0},
                ]
            return []

        def get_data_source_detail(self, product_id):
            return "mysql" if product_id == "phase2_snapshot_visible" else "unknown"

    class FakeErpKPIService:
        def get_sales_window_bulk(self, period_days=7):
            return {}, {}

    from backend.src.service import erp_kpi_service

    monkeypatch.setattr(server, "market_data", FakeMarketData())
    monkeypatch.setattr(erp_kpi_service, "ErpKPIService", FakeErpKPIService)

    snapshot = server._compute_market_snapshot()

    product = next((item for item in snapshot if item["id"] == "phase2_snapshot_visible"), None)
    assert product is not None
    assert product["name"] == "二期快照可见产品"
    assert product["price"] == 7150.0
    assert product["unit"] == "CNY/ton"
    assert product["data_source"] == "real_mysql"


def test_domain_graph_refresh_updates_existing_singleton_reference(tmp_path, monkeypatch):
    from backend.src.db.sqlite_db import Database
    from backend.src.domain import knowledge_graph
    from backend.src.db import sqlite_db

    original_nodes = list(knowledge_graph.domain_graph.nodes)
    original_edges = list(knowledge_graph.domain_graph.edges)
    original_graph = knowledge_graph.domain_graph
    test_db = Database(db_url=f"sqlite:///{tmp_path / f'graph_{uuid.uuid4().hex}.db'}")
    monkeypatch.setattr(sqlite_db, "db", test_db)

    test_db.save_platform_product(
        product_id="graph_refresh_visible",
        org_id="org-default",
        name_en="Graph Refresh Visible",
        name_cn="图谱刷新可见产品",
        product_type="midstream",
        unit="CNY/ton",
        description="Mapped product that should become visible through existing graph references.",
        aliases=[],
        data_table="market_prices",
        data_key="isoprene",
        display_in=["chat", "dashboard", "reports"],
        created_by="test-user",
    )

    try:
        knowledge_graph.refresh_domain_graph()

        assert knowledge_graph.domain_graph is original_graph
        assert any(node.id == "graph_refresh_visible" for node in original_graph.nodes)
    finally:
        knowledge_graph.domain_graph.nodes = original_nodes
        knowledge_graph.domain_graph.edges = original_edges


def test_platform_product_import_template_downloads_csv(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    server = _setup_product_server(tmp_path, monkeypatch)

    with TestClient(server.app) as client:
        response = client.get(
            "/api/platform/products/import-template",
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    text = response.content.decode("utf-8-sig")
    assert "product_id,name_en,name_cn,module" in text
    assert "custom_hc5_resin" in text


def test_platform_product_import_preview_validates_rows(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    server = _setup_product_server(tmp_path, monkeypatch)
    with server.db.engine.begin() as conn:
      conn.execute(text("DROP TABLE IF EXISTS market_prices"))
      conn.execute(
          text(
              "CREATE TABLE market_prices ("
              "date TEXT NOT NULL, "
              "product_id TEXT NOT NULL, "
              "price_type TEXT NOT NULL, "
              "price REAL NOT NULL"
              ")"
          )
      )
      conn.execute(
          text(
              "INSERT INTO market_prices (date, product_id, price_type, price) "
              "VALUES (:date, :product_id, :price_type, :price)"
          ),
          [
              {"date": "2026-05-10", "product_id": "import_visible", "price_type": "east_china", "price": 7100.0},
              {"date": "2026-05-11", "product_id": "import_visible", "price_type": "east_china", "price": 7150.0},
          ],
      )

    csv_payload = "\n".join(
        [
            "product_id,name_en,name_cn,module,unit,description,aliases,data_source,data_table,data_key,display_in",
            "import_visible,Import Visible,导入可见产品,midstream,CNY/ton,Mapped import product,alias one;别名,market_prices,market_prices,import_visible,dashboard;reports",
            "bad_row,,导入坏产品,sideways,CNY/ton,,,,,,dashboard",
        ]
    )

    with TestClient(server.app) as client:
        response = client.post(
            "/api/platform/products/import-preview",
            headers=_AUTH_HEADERS,
            files={"file": ("products.csv", csv_payload.encode("utf-8"), "text/csv")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total_rows"] == 2
    assert body["valid_rows"] == 1
    assert body["invalid_rows"] == 1
    assert body["rows"][0]["product_id"] == "import_visible"
    assert body["rows"][0]["data_available"] is True
    assert body["rows"][0]["data_row_count"] == 2
    assert body["rows"][1]["valid"] is False
    assert "module must be upstream, midstream, or downstream" in body["rows"][1]["errors"]


def test_platform_product_import_commits_valid_rows(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    server = _setup_product_server(tmp_path, monkeypatch)
    csv_payload = "\n".join(
        [
            "product_id,name_en,name_cn,module,unit,description,aliases,data_source,data_table,data_key,display_in",
            "import_commit,Import Commit,导入提交产品,downstream,CNY/ton,Imported product description,commit alias,market_prices,market_prices,import_commit,reports",
            "invalid_commit,,导入无描述产品,downstream,CNY/ton,,,,,,reports",
        ]
    )

    with TestClient(server.app) as client:
        response = client.post(
            "/api/platform/products/import",
            headers=_AUTH_HEADERS,
            files={"file": ("products.csv", csv_payload.encode("utf-8"), "text/csv")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["imported_rows"] == 1
    assert body["valid_rows"] == 1
    assert body["invalid_rows"] == 1
    assert body["imported_products"][0]["id"] == "import_commit"

    saved = server.db.get_platform_product("import_commit", "org-test")
    assert saved is not None
    assert saved["name_cn"] == "导入提交产品"
    assert saved["display_in"] == ["reports"]