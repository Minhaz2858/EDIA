import uuid
from pathlib import Path

from backend.src.db.sqlite_db import Database


def _new_db(tmp_path: Path) -> Database:
    db_path = tmp_path / f"platform_{uuid.uuid4().hex}.db"
    return Database(db_url=f"sqlite:///{db_path}")


def test_report_category_crud(tmp_path):
    db = _new_db(tmp_path)
    org_id = "org-test"

    created = db.save_report_category(
        category_id=None,
        org_id=org_id,
        name="Market Reports",
        parent_id=None,
        icon="chart",
        position=1,
        permissions={"roles": ["role-ceo"]},
        created_by="tester",
    )

    assert created["name"] == "Market Reports"
    assert created["permissions"]["roles"] == ["role-ceo"]

    updated = db.save_report_category(
        category_id=created["id"],
        org_id=org_id,
        name="Market Reports Updated",
        parent_id=None,
        icon="chart",
        position=2,
        permissions={"roles": ["role-market"]},
        created_by="tester",
    )

    assert updated["name"] == "Market Reports Updated"
    assert updated["position"] == 2

    listed = db.list_report_categories(org_id)
    assert any(item["id"] == created["id"] for item in listed)

    deleted = db.delete_report_category(created["id"], org_id)
    assert deleted is True


def test_cockpit_definition_crud(tmp_path):
    db = _new_db(tmp_path)
    org_id = "org-test"

    created = db.save_cockpit_definition(
        cockpit_id=None,
        org_id=org_id,
        name="Executive Cockpit",
        department_id=None,
        layout={"tabs": [{"id": "overview"}]},
        status="draft",
        version=1,
        is_default=True,
        permissions={"roles": ["role-ceo"]},
        created_by="tester",
    )

    assert created["name"] == "Executive Cockpit"
    assert created["is_default"] is True

    fetched = db.get_cockpit_definition(created["id"], org_id)
    assert fetched is not None
    assert fetched["layout"]["tabs"][0]["id"] == "overview"

    deleted = db.delete_cockpit_definition(created["id"], org_id)
    assert deleted is True


def test_alert_rule_crud(tmp_path):
    db = _new_db(tmp_path)
    org_id = "org-test"

    created = db.save_alert_rule(
        rule_id=None,
        org_id=org_id,
        name="C5 7d spike",
        metric_expression="product.cracked_c5.price.east_china",
        condition_type="pct_change",
        condition_params={"window_days": 7, "threshold_pct": 3.0},
        severity="high",
        category="price_spike",
        notification_channels=[{"type": "in_app"}],
        default_owner_id="gao",
        enabled=True,
        evaluation_interval_min=15,
        created_by="tester",
    )

    assert created["condition_params"]["window_days"] == 7
    assert created["enabled"] is True

    listed = db.list_alert_rules(org_id)
    assert any(item["id"] == created["id"] for item in listed)

    deleted = db.delete_alert_rule(created["id"], org_id)
    assert deleted is True


def test_data_source_dataset_and_mappings_permissions(tmp_path):
    db = _new_db(tmp_path)
    org_id = "org-test"

    source = db.save_data_source(
        source_id=None,
        org_id=org_id,
        name="Primary MySQL",
        connector_type="mysql",
        connection_config={"host": "localhost", "database": "edia"},
        status="active",
        last_tested_at=None,
        created_by="tester",
    )
    assert source["connector_type"] == "mysql"

    dataset = db.save_dataset(
        dataset_id=None,
        org_id=org_id,
        source_id=source["id"],
        friendly_name="Market Prices",
        physical_reference="market_prices",
        description="daily market prices",
        tags=["price", "external_market"],
        schema_snapshot={"columns": ["biz_date", "price"]},
        status="active",
    )

    assert dataset["friendly_name"] == "Market Prices"
    assert "price" in dataset["tags"]

    mappings = db.replace_dataset_column_mappings(
        dataset["id"],
        [
            {
                "physical_column": "biz_date",
                "ontology_concept": "date",
                "transform": None,
            },
            {
                "physical_column": "price",
                "ontology_concept": "price.amount",
                "transform": None,
            },
        ],
    )
    assert len(mappings) == 2

    permissions = db.replace_dataset_permissions(
        dataset["id"],
        [
            {
                "role_id": "role-ceo",
                "can_query": True,
                "can_export": True,
                "can_chat": True,
            },
            {
                "role_id": "role-market",
                "can_query": True,
                "can_export": False,
                "can_chat": True,
            },
        ],
    )
    assert len(permissions) == 2

    deleted_dataset = db.delete_dataset(dataset["id"], org_id)
    assert deleted_dataset is True

    deleted_source = db.delete_data_source(source["id"], org_id)
    assert deleted_source is True


def test_list_all_platform_products_returns_all_orgs(tmp_path):
    db = _new_db(tmp_path)

    first = db.save_platform_product(
        product_id="product_org_a",
        org_id="org-a",
        name_en="Product A",
        name_cn="产品A",
        product_type="downstream",
        unit="CNY/ton",
        description="Org A product",
        aliases=["A"],
        data_table="market_prices",
        data_key="product_org_a",
        display_in=["dashboard"],
        created_by="tester",
    )
    second = db.save_platform_product(
        product_id="product_org_b",
        org_id="org-b",
        name_en="Product B",
        name_cn="产品B",
        product_type="midstream",
        unit="CNY/ton",
        description="Org B product",
        aliases=["B"],
        data_table="market_prices",
        data_key="product_org_b",
        display_in=["reports"],
        created_by="tester",
    )

    rows = db.list_all_platform_products()

    assert {row["id"] for row in rows} == {first["id"], second["id"]}
    assert {row["org_id"] for row in rows} == {"org-a", "org-b"}
