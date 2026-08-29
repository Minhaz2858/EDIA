import pandas as pd

from backend.src.db.load_market_prices import _iter_c5_c9, _iter_lz_internal_view_series


def test_lz_internal_view_series_maps_crude_naphtha_and_cracked_c5_rows():
    df = pd.DataFrame(
        [
            {
                "biz_date": "2025-06-13",
                "material_name": "C5",
                "supplier_name": "布伦特",
                "taxprice": 559.75,
            },
            {
                "biz_date": "2025-06-13",
                "material_name": "C5",
                "supplier_name": "裂解C5-扬子裂解C5",
                "taxprice": 4300,
            },
            {
                "biz_date": "2025-06-13",
                "material_name": "C5",
                "supplier_name": "裂解C5-日本石脑油",
                "taxprice": 584.5,
            },
        ]
    )

    rows = list(
        _iter_lz_internal_view_series(
            df,
            [
                (("裂解c5", "c5"), ("布伦特", "brent"), "crude_oil", "brent"),
                (("裂解c5", "c5"), ("日本石脑油",), "naphtha", "japan_cif"),
                (("裂解c5", "c5"), ("扬子裂解c5",), "cracked_c5", "yangzi"),
            ],
        )
    )

    assert rows == [
        {
            "date": "2025-06-13",
            "product_id": "crude_oil",
            "price_type": "brent",
            "price": 559.75,
        },
        {
            "date": "2025-06-13",
            "product_id": "cracked_c5",
            "price_type": "yangzi",
            "price": 4300.0,
        },
        {
            "date": "2025-06-13",
            "product_id": "naphtha",
            "price_type": "japan_cif",
            "price": 584.5,
        },
    ]


def test_lz_internal_view_series_maps_isoprene_market_rows():
    df = pd.DataFrame(
        [
            {
                "biz_date": "2025-06-18",
                "material_name": "异戊二烯",
                "supplier_name": "华东市场价",
                "taxprice": 8703,
            },
            {
                "biz_date": "2025-06-18",
                "material_name": "异戊二烯",
                "supplier_name": "上海石化挂牌价",
                "taxprice": 9000,
            },
            {
                "biz_date": "2025-06-18",
                "material_name": "异戊二烯",
                "supplier_name": "华中",
                "taxprice": 8500,
            },
            {
                "biz_date": "2025-06-18",
                "material_name": "异戊二烯",
                "supplier_name": "华南市场价",
                "taxprice": 8800,
            },
        ]
    )

    rows = list(
        _iter_lz_internal_view_series(
            df,
            [
                (("异戊二烯",), ("华东",), "isoprene", "east_china"),
                (("异戊二烯",), ("上海石化",), "isoprene", "sinopec_sh"),
                (("异戊二烯",), ("华中", "武汉"), "isoprene", "wuhan"),
                (("异戊二烯",), ("华南",), "isoprene", "south_china"),
            ],
        )
    )

    assert [row["price_type"] for row in rows] == [
        "east_china",
        "sinopec_sh",
        "wuhan",
        "south_china",
    ]


def test_iter_c5_c9_maps_wide_brent_naphtha_and_c5_columns():
    df = pd.DataFrame(
        [
            {
                "Unnamed: 0": "2026-03-19",
                "布伦特": 108.65,
                "日本石脑油": 1160.75,
                "扬子裂解C5": 7900,
                "茂名裂解C5": 7900,
            }
        ]
    )

    rows = list(_iter_c5_c9(df))

    assert rows == [
        {
            "date": "2026-03-19",
            "product_id": "crude_oil",
            "price_type": "brent",
            "price": 108.65,
        },
        {
            "date": "2026-03-19",
            "product_id": "naphtha",
            "price_type": "japan_cif",
            "price": 1160.75,
        },
        {
            "date": "2026-03-19",
            "product_id": "cracked_c5",
            "price_type": "yangzi",
            "price": 7900.0,
        },
        {
            "date": "2026-03-19",
            "product_id": "cracked_c5",
            "price_type": "maoming",
            "price": 7900.0,
        },
    ]


def test_lz_internal_view_series_uses_ftaxprice_column_when_present():
    df = pd.DataFrame(
        [
            {
                "biz_date": "2025-06-15",
                "material_name": "SIS",
                "supplier_name": "巴陵1105",
                "FTAXPRICE": 14700,
            }
        ]
    )

    rows = list(
        _iter_lz_internal_view_series(
            df,
            [(("sis",), ("巴陵1105",), "sis", "baling_1105")],
        )
    )

    assert rows == [
        {
            "date": "2025-06-15",
            "product_id": "sis",
            "price_type": "baling_1105",
            "price": 14700.0,
        }
    ]