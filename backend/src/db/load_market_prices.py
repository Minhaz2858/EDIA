"""ETL: Load real upstream and ERP market prices into MySQL ``market_prices``.

Source of truth:
- Upstream market series come from source tables in the configured warehouse database.
- ERP-derived market series come from ERP views in the same configured database.

Run once (or re-run to refresh):
    python -m backend.src.db.load_market_prices

Target table schema:
    market_prices(date DATE, product_id VARCHAR, price_type VARCHAR, price FLOAT)
"""

from __future__ import annotations

import os
from typing import Callable, Iterable, Iterator

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from backend.src.db.price_semantics import erp_weighted_sale_series_sql

load_dotenv()


def _require_env_url(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    joined = ", ".join(names)
    raise RuntimeError(f"Required database URL is missing. Set one of: {joined}")


def _mysql_engine(url: str) -> Engine:
    """Create a MySQL engine with a short connect timeout so that an unreachable
    host fails immediately instead of blocking for OS-level TCP timeout."""
    connect_args = {"connect_timeout": 3} if "mysql" in url or "pymysql" in url else {}
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


def _get_target_engine() -> Engine:
    url = _require_env_url("DATABASE_URL")
    return _mysql_engine(url)


def _get_erp_engine() -> Engine:
    url = _require_env_url("DATABASE_URL")
    return _mysql_engine(url)


def _get_external_engine() -> Engine:
    url = _require_env_url("DATABASE_URL")
    return _mysql_engine(url)


_ERP_MARKET_SERIES = [
    ("c5_resin", "erp_sale_order_weighted", "碳五石油树脂"),
    ("raffinate_c5", "erp_sale_order_weighted", "抽余碳五"),
]

_OPTIONAL_EXTERNAL_SERIES_TABLE = os.getenv(
    "EXTERNAL_FEEDSTOCK_SERIES_TABLE", "upstream_market_series"
)
_OPTIONAL_EXTERNAL_PRODUCT_IDS = set()

_ERP_VIEW_SERIES = [
    ("v_forecast_price", "cracked_c9", "erp_forecast_price", "工业用裂解碳九"),
    (
        "v_partner_forecast_price",
        "hydrogenated_c9",
        "partner_forecast_price",
        "加氢碳九",
    ),
    (
        "v_partner_forecast_price",
        "trimethylbenzene",
        "partner_forecast_price",
        "混三甲苯",
    ),
]

# ERP transactional sale-order views — weighted avg sale price per product per date
_ERP_SALE_VIEW_SERIES: list[tuple[str, str, str]] = [
    ("dcpd",           "erp_sale_order_weighted", "sale_erp_v_双环戊二烯_data"),
    ("isoprene",       "erp_sale_order_weighted", "sale_erp_v_异戊二烯_data"),
    ("piperylene",     "erp_sale_order_weighted", "sale_erp_v_间戊二烯_data"),
    ("sis",            "erp_sale_order_weighted", "sale_erp_v_sis_d2015_data"),
    ("blowing_agent",  "erp_sale_order_weighted", "sale_erp_v_戊烷发泡剂_data"),
    ("cracked_c5",     "erp_sale_order_weighted", "sale_erp_v_工业用裂解碳五_data"),
    ("cracked_c9",     "erp_sale_order_weighted", "sale_erp_v_工业用裂解碳九_data"),
    ("trimethylbenzene","erp_sale_order_weighted","sale_erp_v_混三甲苯_data"),
]

_LZ_INTERNAL_VIEW_SPECS = [
    (
        "lz_v_裂解c5_data",
        [
            (("裂解c5", "c5"), ("布伦特", "布伦特原油", "brent"), "crude_oil", "brent"),
            (("裂解c5", "c5"), ("日本石脑油",), "naphtha", "japan_cif"),
            (("裂解c5", "c5"), ("裂解c5均价",), "cracked_c5", "average"),
            (("裂解c5", "c5"), ("扬子裂解c5",), "cracked_c5", "yangzi"),
            (("裂解c5", "c5"), ("茂名裂解c5",), "cracked_c5", "maoming"),
        ],
    ),
    (
        "lz_v_异戊二烯_data",
        [
            (("异戊二烯",), ("华东",), "isoprene", "east_china"),
            (("异戊二烯",), ("上海石化",), "isoprene", "sinopec_sh"),
            (("异戊二烯",), ("华中", "武汉"), "isoprene", "wuhan"),
            (("异戊二烯",), ("华南",), "isoprene", "south_china"),
            (("异戊二烯",), ("茂名裂解c5",), "isoprene", "maoming"),
        ],
    ),
    (
        "lz_v_间戊二烯_data",
        [
            (("间戊二烯",), ("华东",), "piperylene", "east_china"),
            (("间戊二烯",), ("上海石化",), "piperylene", "sinopec_sh"),
            (("间戊二烯",), ("华中", "武汉"), "piperylene", "wuhan"),
            (("间戊二烯",), ("中韩石化",), "piperylene", "sinokor"),
        ],
    ),
    (
        "lz_v_双环戊二烯_data",
        [
            (("双环戊二烯",), ("上海石化", "中韩石化"), "dcpd", "sinopec_sh"),
            (("双环戊二烯",), ("上海大户", "大户"), "dcpd", "bulk_sh"),
            (("双环戊二烯",), ("华东",), "dcpd", "east_china"),
            (("双环戊二烯",), ("华中", "武汉"), "dcpd", "wuhan"),
        ],
    ),
    (
        "lz_v_sis_data",
        [
            (("sis",), ("巴陵1105",), "sis", "baling_1105"),
        ],
    ),
    (
        "lz_v_苯乙烯_data",
        [
            (("苯乙烯",), ("中石化华东", "上海石化"), "styrene", "sinopec_sh"),
            (("苯乙烯",), ("茂名石化",), "styrene", "maoming"),
            (("苯乙烯",), ("广州石化",), "styrene", "guangzhou"),
        ],
    ),
    # --- New LZ tables: cracked C9 market reference, trimethylbenzene, MTHPA hardener ---
    (
        "lz_v_裂解c9_data",
        [
            (("裂解c9",), ("裂解c9均价",), "cracked_c9", "average"),
            (("裂解c9",), ("扬子石化",), "cracked_c9", "yangzi"),
            (("裂解c9",), ("扬子巴斯夫",), "cracked_c9", "yangzi_basf"),
        ],
    ),
    (
        "lz_v_混三甲苯_data",
        [
            (("混三甲苯",), ("华南",), "trimethylbenzene", "south_china"),
            (("混三甲苯",), ("恒河",), "trimethylbenzene", "henhe_hc9"),
        ],
    ),
    (
        "lz_v_甲基四氢苯酐_data",
        [
            (("甲基四氢苯酐",), ("聚辉",), "mthpa_hardener", "juhui"),
        ],
    ),
    # Pentane blowing agent — MySQL view name uses 泡发剂 (legacy spelling).
    (
        "lz_v_戊烷泡发剂_data",
        [
            (("戊烷发泡剂",), ("华东市场价",), "blowing_agent", "east_china"),
            (("戊烷发泡剂",), ("华中市场价",), "blowing_agent", "central_china"),
            (("戊烷发泡剂",), ("华南市场价",), "blowing_agent", "south_china"),
            (("戊烷发泡剂",), ("青岛炼化", "f7"), "blowing_agent", "qingdao_f7"),
        ],
    ),
]

# Unified market view (new schema): v_market_data
# Maps (material_name, supplier_name) pairs into internal product_id/price_type.
_V_MARKET_DATA_MAPPINGS: list[
    tuple[tuple[str, ...], tuple[str, ...], str, str]
] = [
    # Upstream
    # In v_market_data, Brent/Naphtha are commonly encoded as supplier rows under 裂解C5.
    ((), ("布伦特", "brent"), "crude_oil", "brent"),
    ((), ("日本石脑油",), "naphtha", "japan_cif"),
    (("布伦特", "brent"), (), "crude_oil", "brent"),
    (("石脑油", "naphtha"), (), "naphtha", "japan_cif"),
    (("裂解c5", "裂解碳五", "c5"), ("裂解c5均价",), "cracked_c5", "average"),
    (("裂解c5", "裂解碳五", "c5"), ("扬子裂解c5",), "cracked_c5", "yangzi"),
    (("裂解c5", "裂解碳五", "c5"), ("茂名裂解c5",), "cracked_c5", "maoming"),
    (("裂解c9", "裂解碳九", "c9"), ("裂解c9均价",), "cracked_c9", "average"),
    (("裂解c9", "裂解碳九", "c9"), ("扬子石化",), "cracked_c9", "yangzi"),
    (("裂解c9", "裂解碳九", "c9"), ("扬子巴斯夫",), "cracked_c9", "yangzi_basf"),
    # C5 derivatives
    (("异戊二烯",), ("华东市场价",), "isoprene", "east_china"),
    (("异戊二烯",), ("上海石化",), "isoprene", "sinopec_sh"),
    (("异戊二烯",), ("华中", "武汉"), "isoprene", "wuhan"),
    (("异戊二烯",), ("华南市场价",), "isoprene", "south_china"),
    (("异戊二烯",), ("茂名裂解c5",), "isoprene", "maoming"),
    (("间戊二烯",), ("华东市场价",), "piperylene", "east_china"),
    (("间戊二烯",), ("上海石化挂牌价",), "piperylene", "sinopec_sh"),
    (("间戊二烯",), ("华中武汉",), "piperylene", "wuhan"),
    (("间戊二烯",), ("中韩石化",), "piperylene", "sinokor"),
    (("双环戊二烯",), ("上海石化", "中韩石化"), "dcpd", "sinopec_sh"),
    (("双环戊二烯",), ("上海大户", "大户"), "dcpd", "bulk_sh"),
    (("双环戊二烯",), ("华东市场价",), "dcpd", "east_china"),
    (("双环戊二烯",), ("华中武汉",), "dcpd", "wuhan"),
    (("戊烷发泡剂",), ("华东市场价",), "blowing_agent", "east_china"),
    (("戊烷发泡剂",), ("华中市场价",), "blowing_agent", "central_china"),
    (("戊烷发泡剂",), ("华南市场价",), "blowing_agent", "south_china"),
    (("戊烷发泡剂",), ("青岛炼化", "f7"), "blowing_agent", "qingdao_f7"),
    (("正戊烷",), ("山东京博",), "blowing_agent_n_pentane", "shandong_jingbo"),
    (("异戊烷",), ("山东京博",), "blowing_agent_isopentane", "shandong_jingbo"),
    (("环戊烷",), ("山东京博",), "blowing_agent_cyclopentane", "shandong_jingbo"),
    # Downstream
    (("sis",), ("巴陵1105",), "sis", "baling_1105"),
    (("苯乙烯",), ("中石化华东", "上海石化"), "styrene", "sinopec_sh"),
    (("苯乙烯",), ("茂名石化",), "styrene", "maoming"),
    (("苯乙烯",), ("广州石化",), "styrene", "guangzhou"),
    # C9 derivatives / hardener
    (("混三甲苯",), ("华南二甲苯", "华南"), "trimethylbenzene", "south_china"),
    (("混三甲苯",), ("恒河",), "trimethylbenzene", "henhe_hc9"),
    (("甲基四氢苯酐",), ("聚辉",), "mthpa_hardener", "juhui"),
    # Extra products from the new schema (not currently in domain graph)
    (("丁二烯",), ("华东市场-江阴出罐",), "butadiene", "east_china_jiangyin"),
    (("丁二烯",), ("茂名石化",), "butadiene", "maoming"),
    (("sbs",), ("华东市场791-h", "791-h"), "sbs", "east_china_791h"),
    (("sebs",), ("巴陵503t", "503t"), "sebs", "baling_503t"),
]


DDL = """
CREATE TABLE IF NOT EXISTS market_prices (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    date        DATE        NOT NULL,
    product_id  VARCHAR(60) NOT NULL,
    price_type  VARCHAR(60) NOT NULL,
    price       FLOAT,
    INDEX idx_prod_date (product_id, date),
    INDEX idx_date (date)
) CHARACTER SET utf8mb4
"""


def _parse_date(value: object) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value or text_value.lower() == "nan":
        return None
    parsed = pd.to_datetime(text_value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.strftime("%Y-%m-%d")


def _numeric(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    text_value = str(value).strip()
    if not text_value or text_value.lower() == "nan":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _record(
    date_value: object, product_id: str, price_type: str, price_value: object
) -> dict | None:
    parsed_date = _parse_date(date_value)
    numeric_value = _numeric(price_value)
    if parsed_date is None or numeric_value is None:
        return None
    return {
        "date": parsed_date,
        "product_id": product_id,
        "price_type": price_type,
        "price": numeric_value,
    }


def _fetch_table_frame(engine: Engine, table_name: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql_query(text(f"SELECT * FROM `{table_name}`"), conn)


def _normalize_col_name(value: object) -> str:
    return "".join(str(value).split()).lower()


def _find_col(df: pd.DataFrame, *candidates: str) -> str | None:
    lookup = {_normalize_col_name(col): col for col in df.columns}
    for candidate in candidates:
        matched = lookup.get(_normalize_col_name(candidate))
        if matched is not None:
            return matched
    return None


def _find_col_contains(df: pd.DataFrame, *candidates: str) -> str | None:
    normalized_columns = [(_normalize_col_name(col), col) for col in df.columns]
    for candidate in candidates:
        normalized_candidate = _normalize_col_name(candidate)
        for normalized_column, original_column in normalized_columns:
            if normalized_candidate in normalized_column:
                return original_column
    return None


def _row_value(row: pd.Series, column_name: str | None) -> object:
    if column_name is None:
        return None
    return row.get(column_name)


def _normalize_text(value: object) -> str:
    return "".join(str(value or "").strip().lower().split())


def _matches_keywords(value: object, keywords: tuple[str, ...]) -> bool:
    text_value = _normalize_text(value)
    return bool(text_value) and any(
        _normalize_text(keyword) in text_value for keyword in keywords
    )


def _table_exists(engine: Engine, table_name: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text(
                    "SELECT 1 "
                    "FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name "
                    "LIMIT 1"
                ),
                {"table_name": table_name},
            ).scalar()
        )


def _iter_optional_external_series(
    engine: Engine, table_name: str = _OPTIONAL_EXTERNAL_SERIES_TABLE
) -> Iterator[dict]:
    if not _table_exists(engine, table_name):
        print(f"  optional external source not found: {table_name}")
        return

    try:
        df = _fetch_table_frame(engine, table_name)
    except Exception as exc:
        print(f"  optional external source invalid: {table_name} -> {exc}")
        return

    required_columns = {"date", "product_id", "price_type", "price"}
    missing_columns = required_columns.difference(df.columns)
    if missing_columns:
        missing_text = ", ".join(sorted(missing_columns))
        print(
            f"  optional external source missing columns: {table_name} -> {missing_text}"
        )
        return

    for row in df.itertuples(index=False):
        if row.product_id not in _OPTIONAL_EXTERNAL_PRODUCT_IDS:
            continue
        item = _record(row.date, row.product_id, row.price_type, row.price)
        if item:
            yield item


def _find_price_col(df: pd.DataFrame) -> str | None:
    return _find_col_contains(
        df,
        "price",
        "taxprice",
        "ftaxprice",
        "含税价",
        "单价",
        "价格",
    )


def _iter_lz_internal_view_series(
    df: pd.DataFrame,
    mappings: list[tuple[tuple[str, ...], tuple[str, ...], str, str]],
) -> Iterator[dict]:
    date_col = _find_col_contains(df, "biz_date", "date", "业务日期", "fdate") or df.columns[0]
    material_name_col = _find_col_contains(
        df,
        "material_name",
        "产品名称",
        "fmaterial_name",
    )
    supplier_name_col = _find_col_contains(
        df,
        "supplier_name",
        "厂商名称",
        "fsupplier_name",
        "supplier",
    )
    price_col = _find_price_col(df)
    if price_col is None:
        return

    for _, row in df.iterrows():
        material_name = _row_value(row, material_name_col)
        supplier_name = _row_value(row, supplier_name_col)
        for material_keywords, supplier_keywords, product_id, price_type in mappings:
            if material_keywords and not _matches_keywords(material_name, material_keywords):
                continue
            if supplier_keywords and not _matches_keywords(supplier_name, supplier_keywords):
                continue
            item = _record(
                _row_value(row, date_col),
                product_id,
                price_type,
                _row_value(row, price_col),
            )
            if item:
                yield item
            break


def _iter_c5_c9(df: pd.DataFrame) -> Iterator[dict]:
    date_col = _find_col_contains(df, "biz_date", "fdate", "date", "日期", "Unnamed: 0") or df.columns[0]
    naphtha_col = _find_col_contains(df, "日本石脑油")
    brent_col = _find_col_contains(df, "布伦特", "Brent")
    yangzi_col = _find_col_contains(df, "扬子裂解c5")
    maoming_col = _find_col_contains(df, "茂名裂解c5")

    for _, row in df.iterrows():
        for item in (
            _record(
                _row_value(row, date_col),
                "crude_oil",
                "brent",
                _row_value(row, brent_col),
            ),
            _record(
                _row_value(row, date_col),
                "naphtha",
                "japan_cif",
                _row_value(row, naphtha_col),
            ),
            _record(
                _row_value(row, date_col),
                "cracked_c5",
                "yangzi",
                _row_value(row, yangzi_col),
            ),
            _record(
                _row_value(row, date_col),
                "cracked_c5",
                "maoming",
                _row_value(row, maoming_col),
            ),
        ):
            if item:
                yield item


def _iter_isoprene(df: pd.DataFrame) -> Iterator[dict]:
    date_col = _find_col(df, "date", "日期", "Unnamed: 0") or df.columns[0]
    east_china_col = _find_col(df, "华东市场价")
    sinopec_sh_col = _find_col(df, "上海石化")
    wuhan_col = _find_col(df, "华中武汉")
    south_china_col = _find_col(df, "华南市场价")

    for _, row in df.iterrows():
        for item in (
            _record(
                _row_value(row, date_col),
                "isoprene",
                "east_china",
                _row_value(row, east_china_col),
            ),
            _record(
                _row_value(row, date_col),
                "isoprene",
                "sinopec_sh",
                _row_value(row, sinopec_sh_col),
            ),
            _record(
                _row_value(row, date_col),
                "isoprene",
                "wuhan",
                _row_value(row, wuhan_col),
            ),
            _record(
                _row_value(row, date_col),
                "isoprene",
                "south_china",
                _row_value(row, south_china_col),
            ),
        ):
            if item:
                yield item


def _iter_piperylene(df: pd.DataFrame) -> Iterator[dict]:
    date_col = _find_col(df, "date", "日期", "Unnamed: 0") or df.columns[0]
    east_china_col = _find_col(df, "华东市场价")
    sinopec_sh_col = _find_col(df, "上海石化挂牌价")
    wuhan_col = _find_col(df, "华中武汉")

    for _, row in df.iterrows():
        for item in (
            _record(
                _row_value(row, date_col),
                "piperylene",
                "east_china",
                _row_value(row, east_china_col),
            ),
            _record(
                _row_value(row, date_col),
                "piperylene",
                "sinopec_sh",
                _row_value(row, sinopec_sh_col),
            ),
            _record(
                _row_value(row, date_col),
                "piperylene",
                "wuhan",
                _row_value(row, wuhan_col),
            ),
        ):
            if item:
                yield item


def _iter_dcpd(df: pd.DataFrame) -> Iterator[dict]:
    date_col = _find_col(df, "date", "日期", "Unnamed: 0") or df.columns[0]
    sinopec_sh_col = _find_col(df, "上海石化")
    bulk_sh_col = _find_col(df, "上海(大户价)")
    east_china_col = _find_col(df, "华东市场价")
    wuhan_col = _find_col(df, "华中武汉")

    for _, row in df.iterrows():
        for item in (
            _record(
                _row_value(row, date_col),
                "dcpd",
                "sinopec_sh",
                _row_value(row, sinopec_sh_col),
            ),
            _record(
                _row_value(row, date_col),
                "dcpd",
                "bulk_sh",
                _row_value(row, bulk_sh_col),
            ),
            _record(
                _row_value(row, date_col),
                "dcpd",
                "east_china",
                _row_value(row, east_china_col),
            ),
            _record(
                _row_value(row, date_col), "dcpd", "wuhan", _row_value(row, wuhan_col)
            ),
        ):
            if item:
                yield item


def _iter_sis(df: pd.DataFrame) -> Iterator[dict]:
    date_col = _find_col(df, "date", "日期", "Unnamed: 0") or df.columns[0]
    baling_col = _find_col(df, "巴陵1105")
    isoprene_ref_col = _find_col(df, "异戊二烯-上海")

    for _, row in df.iterrows():
        for item in (
            _record(
                _row_value(row, date_col),
                "sis",
                "baling_1105",
                _row_value(row, baling_col),
            ),
            _record(
                _row_value(row, date_col),
                "sis",
                "isoprene_sh_ref",
                _row_value(row, isoprene_ref_col),
            ),
        ):
            if item:
                yield item


def _iter_blowing_agent(df: pd.DataFrame) -> Iterator[dict]:
    date_col = _find_col(df, "date", "日期", "Unnamed: 0") or df.columns[0]
    pentane_date_col = _find_col(df, "Unnamed: 6") or date_col
    east_china_col = _find_col(df, "华东市场价")
    central_china_col = _find_col(df, "华中市场价")
    qingdao_col = _find_col(df, "青岛炼化-发泡剂F7")
    south_china_col = _find_col(df, "华南市场价")
    n_pentane_col = _find_col(df, "正戊烷-山东京博")
    isopentane_col = _find_col(df, "异戊烷-山东京博")
    cyclopentane_col = _find_col(df, "环戊烷-山东京博")

    for _, row in df.iterrows():
        for item in (
            _record(
                _row_value(row, date_col),
                "blowing_agent",
                "east_china",
                _row_value(row, east_china_col),
            ),
            _record(
                _row_value(row, date_col),
                "blowing_agent",
                "central_china",
                _row_value(row, central_china_col),
            ),
            _record(
                _row_value(row, date_col),
                "blowing_agent",
                "qingdao_f7",
                _row_value(row, qingdao_col),
            ),
            _record(
                _row_value(row, date_col),
                "blowing_agent",
                "south_china",
                _row_value(row, south_china_col),
            ),
            _record(
                _row_value(row, pentane_date_col),
                "blowing_agent_n_pentane",
                "shandong_jingbo",
                _row_value(row, n_pentane_col),
            ),
            _record(
                _row_value(row, pentane_date_col),
                "blowing_agent_isopentane",
                "shandong_jingbo",
                _row_value(row, isopentane_col),
            ),
            _record(
                _row_value(row, pentane_date_col),
                "blowing_agent_cyclopentane",
                "shandong_jingbo",
                _row_value(row, cyclopentane_col),
            ),
        ):
            if item:
                yield item


def _iter_styrene(df: pd.DataFrame) -> Iterator[dict]:
    date_col = _find_col(df, "date", "日期", "Unnamed: 0") or df.columns[0]
    sinopec_sh_col = _find_col(df, "上海石化")
    maoming_col = _find_col(df, "茂名石化")
    guangzhou_col = _find_col(df, "广州石化")

    for _, row in df.iterrows():
        for item in (
            _record(
                _row_value(row, date_col),
                "styrene",
                "sinopec_sh",
                _row_value(row, sinopec_sh_col),
            ),
            _record(
                _row_value(row, date_col),
                "styrene",
                "maoming",
                _row_value(row, maoming_col),
            ),
            _record(
                _row_value(row, date_col),
                "styrene",
                "guangzhou",
                _row_value(row, guangzhou_col),
            ),
        ):
            if item:
                yield item


def _iter_sbs_butadiene(df: pd.DataFrame) -> Iterator[dict]:
    butadiene_date_col = _find_col(df, "date", "日期", "Unnamed: 0") or df.columns[0]
    sbs_791_date_col = _find_col(df, "Unnamed: 4") or butadiene_date_col
    sbs_503t_date_col = _find_col(df, "Unnamed: 7") or butadiene_date_col
    east_china_col = _find_col(df, "华东市场-江阴出罐")
    maoming_col = _find_col(df, "茂名石化")
    sbs_791_col = _find_col(df, "华东市场791-H")
    baling_503t_gd_col = _find_col(df, "广东市场-巴陵503T")
    baling_503t_zj_col = _find_col(df, "浙江市场-巴陵503T")

    for _, row in df.iterrows():
        for item in (
            _record(
                _row_value(row, butadiene_date_col),
                "butadiene",
                "east_china_jiangyin",
                _row_value(row, east_china_col),
            ),
            _record(
                _row_value(row, butadiene_date_col),
                "butadiene",
                "maoming",
                _row_value(row, maoming_col),
            ),
            _record(
                _row_value(row, sbs_791_date_col),
                "sbs",
                "east_china_791h",
                _row_value(row, sbs_791_col),
            ),
            _record(
                _row_value(row, sbs_503t_date_col),
                "sbs",
                "guangdong_baling503t",
                _row_value(row, baling_503t_gd_col),
            ),
            _record(
                _row_value(row, sbs_503t_date_col),
                "sbs",
                "zhejiang_baling503t",
                _row_value(row, baling_503t_zj_col),
            ),
        ):
            if item:
                yield item


def _iter_erp_market_series(engine: Engine) -> Iterator[dict]:
    with engine.connect() as conn:
        for product_id, price_type, material_name in _ERP_MARKET_SERIES:
            rows = conn.execute(
                text(
                    "SELECT DATE(PLANDATE) AS biz_date, "
                    "SUM(COALESCE(FDELIQTY, 0) * COALESCE(FTAXPRICE, FPRICE, 0)) "
                    "  / NULLIF(SUM(COALESCE(FDELIQTY, 0)), 0) AS weighted_price "
                    "FROM erp_v_sale_orderentry "
                    "WHERE material_name = :material_name "
                    "  AND PLANDATE IS NOT NULL "
                    "GROUP BY DATE(PLANDATE) "
                    "ORDER BY DATE(PLANDATE) ASC"
                ),
                {"material_name": material_name},
            )
            found = False
            for biz_date, weighted_price in rows:
                item = _record(biz_date, product_id, price_type, weighted_price)
                if item is None:
                    continue
                found = True
                yield item
            if not found:
                print(
                    f"  ERP source missing for {product_id}: material_name='{material_name}'"
                )


def _iter_erp_view_series(engine: Engine) -> Iterator[dict]:
    with engine.connect() as conn:
        for view_name, product_id, price_type, material_name in _ERP_VIEW_SERIES:
            rows = conn.execute(
                text(
                    f"SELECT date AS biz_date, AVG(price) AS avg_price "
                    f"FROM {view_name} "
                    "WHERE material_name = :material_name "
                    "  AND date IS NOT NULL "
                    "  AND price IS NOT NULL "
                    "GROUP BY date "
                    "ORDER BY date ASC"
                ),
                {"material_name": material_name},
            )
            found = False
            for biz_date, avg_price in rows:
                item = _record(biz_date, product_id, price_type, avg_price)
                if item is None:
                    continue
                found = True
                yield item
            if not found:
                print(
                    f"  ERP view source missing for {product_id}: "
                    f"view='{view_name}' material_name='{material_name}'"
                )


def _iter_erp_saleview_series(engine: Engine) -> Iterator[dict]:
    """Compute weighted-average sale price per day from each sale_erp_v_*_data view."""
    for product_id, price_type, table_name in _ERP_SALE_VIEW_SERIES:
        if not _table_exists(engine, table_name):
            print(f"  ERP sale view not found: {table_name}")
            continue
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(erp_weighted_sale_series_sql(table_name)))
                found = False
                for biz_date, weighted_price, _total_qty, _row_count in rows:
                    item = _record(biz_date, product_id, price_type, weighted_price)
                    if item is None:
                        continue
                    found = True
                    yield item
                if not found:
                    print(f"  ERP sale view empty: {table_name}")
        except Exception as exc:
            print(f"  ERP sale view read failed: {table_name} -> {exc}")


_UPSTREAM_TABLE_SPECS: list[tuple[str, Callable[[pd.DataFrame], Iterable[dict]]]] = [
    ("裂解C5", _iter_c5_c9),
    ("异戊二烯", _iter_isoprene),
    ("间戊二烯", _iter_piperylene),
    ("双环戊二烯", _iter_dcpd),
    ("SIS", _iter_sis),
    ("发泡剂", _iter_blowing_agent),
    ("苯乙烯", _iter_styrene),
    ("丁二烯_SBS", _iter_sbs_butadiene),
]


def _iter_internal_market_series(engine: Engine) -> Iterator[dict]:
    # Prefer the new unified schema when available.
    if _table_exists(engine, "v_market_data"):
        try:
            df_source = _fetch_table_frame(engine, "v_market_data")
            found = False
            for item in _iter_lz_internal_view_series(df_source, _V_MARKET_DATA_MAPPINGS):
                found = True
                yield item
            if found:
                return
            print("  v_market_data found but no mapped rows; falling back to legacy views")
        except Exception as exc:
            print(f"  v_market_data read failed -> {exc}; falling back to legacy views")

    for view_name, mappings in _LZ_INTERNAL_VIEW_SPECS:
        if not _table_exists(engine, view_name):
            print(f"  internal view not found: {view_name}")
            continue
        try:
            df_source = _fetch_table_frame(engine, view_name)
        except Exception as exc:
            print(f"  internal view read failed: {view_name} -> {exc}")
            continue

        found = False
        for item in _iter_lz_internal_view_series(df_source, mappings):
            found = True
            yield item
        if not found:
            print(f"  internal view empty or unmapped: {view_name}")


def _insert_rows(engine: Engine, rows: list[dict]) -> int:
    if not rows:
        return 0
    df_insert = pd.DataFrame(rows).dropna(
        subset=["date", "product_id", "price_type", "price"]
    )
    if df_insert.empty:
        return 0
    # Keep one value per day/product/type to avoid duplicate source rows bloating output.
    df_insert = (
        df_insert.groupby(["date", "product_id", "price_type"], as_index=False)["price"]
        .mean()
        .round({"price": 6})
    )
    batch_size = 2000
    with engine.begin() as conn:
        for start in range(0, len(df_insert), batch_size):
            batch = df_insert.iloc[start : start + batch_size]
            conn.execute(
                text(
                    "INSERT INTO market_prices (date, product_id, price_type, price) "
                    "VALUES (:date, :product_id, :price_type, :price)"
                ),
                batch.to_dict("records"),
            )
    return len(df_insert)


def load_all(reset: bool = True) -> int:
    target_engine = _get_target_engine()
    erp_engine = _get_erp_engine()

    with target_engine.begin() as conn:
        conn.execute(text(DDL))
        if reset:
            conn.execute(text("TRUNCATE TABLE market_prices"))
            print("market_prices table truncated.")

    total = 0

    internal_inserted = _insert_rows(target_engine, list(_iter_internal_market_series(erp_engine)))
    if internal_inserted:
        print(f"  Internal LZ market series: {internal_inserted:,} rows loaded")
        total += internal_inserted

    erp_inserted = _insert_rows(
        target_engine, list(_iter_erp_market_series(erp_engine))
    )
    if erp_inserted:
        print(f"  ERP market series: {erp_inserted:,} rows loaded")
        total += erp_inserted

    erp_view_inserted = _insert_rows(
        target_engine, list(_iter_erp_view_series(erp_engine))
    )
    if erp_view_inserted:
        print(f"  ERP view market series: {erp_view_inserted:,} rows loaded")
        total += erp_view_inserted

    erp_saleview_inserted = _insert_rows(
        target_engine, list(_iter_erp_saleview_series(erp_engine))
    )
    if erp_saleview_inserted:
        print(f"  ERP sale view series: {erp_saleview_inserted:,} rows loaded")
        total += erp_saleview_inserted

    print(f"\nTotal rows loaded into market_prices: {total:,}")
    return total


if __name__ == "__main__":
    load_all(reset=True)
