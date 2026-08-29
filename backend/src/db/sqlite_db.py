from typing import List, Dict, Any, Optional
import uuid
from datetime import datetime
import os
import tempfile
import json
import logging
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.src.db.engine_factory import get_engine
from backend.src.service.auth_service import (
    create_session_token,
    hash_password,
    hash_token,
    normalize_username,
)


DEFAULT_INVENTORY_SAFETY_CONFIG = {
    "isoprene": {
        "name_cn": "异戊二烯",
        "safety_min": 200,
        "safety_max": 800,
        "daily_consumption": 70,
    },
    "piperylene": {
        "name_cn": "间戊二烯",
        "safety_min": 150,
        "safety_max": 600,
        "daily_consumption": 55,
    },
    "dcpd": {
        "name_cn": "双环戊二烯",
        "safety_min": 180,
        "safety_max": 700,
        "daily_consumption": 60,
    },
    "raffinate_c5": {
        "name_cn": "抽余碳五",
        "safety_min": 100,
        "safety_max": 500,
        "daily_consumption": 50,
    },
    "trimethylbenzene": {
        "name_cn": "混三甲苯",
        "safety_min": 80,
        "safety_max": 400,
        "daily_consumption": 30,
    },
    "hydrogenated_c9": {
        "name_cn": "加氢碳九",
        "safety_min": 100,
        "safety_max": 500,
        "daily_consumption": 35,
    },
}

DEFAULT_WEEKLY_REPORT_PROFILES = {
    "cracked_c5": {
        "chart_profiles": {
            "yearly": {
                "series": [
                    {"key": "yangzi", "label": "扬子裂解C5", "source": "market", "product_id": "cracked_c5", "price_type": "yangzi", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "maoming", "label": "茂名裂解C5", "source": "market", "product_id": "cracked_c5", "price_type": "maoming", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                    {"key": "japan_cif", "label": "日本石脑油", "source": "market", "product_id": "naphtha", "price_type": "japan_cif", "color": "#73d13d", "axis": "right", "unit": "美元/吨"},
                ],
            },
            "monthly": {
                "series": [
                    {"key": "yangzi", "label": "扬子裂解C5", "source": "market", "product_id": "cracked_c5", "price_type": "yangzi", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "maoming", "label": "茂名裂解C5", "source": "market", "product_id": "cracked_c5", "price_type": "maoming", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                    {"key": "japan_cif", "label": "日本石脑油", "source": "market", "product_id": "naphtha", "price_type": "japan_cif", "color": "#73d13d", "axis": "right", "unit": "美元/吨"},
                ],
            },
        },
        "snapshot_profile": {
            "lines": [
                {"label": "日本石脑油", "source": "market", "product_id": "naphtha", "price_type": "japan_cif", "unit": "美元/吨", "wow": "pct"},
                {"label": "扬子裂解C5", "source": "market", "product_id": "cracked_c5", "price_type": "yangzi", "unit": "元/吨", "wow": "delta"},
                {"label": "茂名裂解C5", "source": "market", "product_id": "cracked_c5", "price_type": "maoming", "unit": "元/吨", "wow": "delta"},
                {"label": "我司 ERP 周均成交价（加权）", "source": "erp", "unit": "元/吨", "wow": "pct"},
            ],
        },
    },
    "cracked_c9": {
        "chart_profiles": {
            "yearly": {
                "series": [
                    {"key": "yangzi_basf", "label": "扬子巴斯夫裂解C9", "source": "market", "product_id": "cracked_c9", "price_type": "yangzi_basf", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "yangzi", "label": "扬子裂解C9", "source": "market", "product_id": "cracked_c9", "price_type": "yangzi", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                    {"key": "average", "label": "裂解C9市场均价", "source": "market", "product_id": "cracked_c9", "price_type": "average", "color": "#73d13d", "axis": "left", "unit": "元/吨"},
                ],
            },
            "monthly": {
                "series": [
                    {"key": "yangzi_basf", "label": "扬子巴斯夫裂解C9", "source": "market", "product_id": "cracked_c9", "price_type": "yangzi_basf", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "yangzi", "label": "扬子裂解C9", "source": "market", "product_id": "cracked_c9", "price_type": "yangzi", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                    {"key": "average", "label": "裂解C9市场均价", "source": "market", "product_id": "cracked_c9", "price_type": "average", "color": "#73d13d", "axis": "left", "unit": "元/吨"},
                ],
            },
        },
        "snapshot_profile": {
            "lines": [
                {"label": "裂解C9市场均价", "source": "market", "product_id": "cracked_c9", "price_type": "average", "unit": "元/吨", "wow": "delta"},
                {"label": "扬子巴斯夫裂解C9", "source": "market", "product_id": "cracked_c9", "price_type": "yangzi_basf", "unit": "元/吨", "wow": "delta"},
                {"label": "我司 ERP 周均成交价（加权）", "source": "erp", "unit": "元/吨", "wow": "pct"},
            ],
        },
    },
    "isoprene": {
        "chart_profiles": {
            "yearly": {
                "series": [
                    {"key": "east_china", "label": "华东市场价", "source": "market", "product_id": "isoprene", "price_type": "east_china", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "sinopec_sh", "label": "上海石化", "source": "market", "product_id": "isoprene", "price_type": "sinopec_sh", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                    {"key": "wuhan", "label": "华中武汉", "source": "market", "product_id": "isoprene", "price_type": "wuhan", "color": "#13c2c2", "axis": "left", "unit": "元/吨"},
                    {"key": "south_china", "label": "华南市场价", "source": "market", "product_id": "isoprene", "price_type": "south_china", "color": "#73d13d", "axis": "left", "unit": "元/吨"},
                ],
            },
            "monthly": {
                "series": [
                    {"key": "east_china", "label": "华东市场价", "source": "market", "product_id": "isoprene", "price_type": "east_china", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "sinopec_sh", "label": "上海石化", "source": "market", "product_id": "isoprene", "price_type": "sinopec_sh", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                    {"key": "wuhan", "label": "华中武汉", "source": "market", "product_id": "isoprene", "price_type": "wuhan", "color": "#13c2c2", "axis": "left", "unit": "元/吨"},
                    {"key": "south_china", "label": "华南市场价", "source": "market", "product_id": "isoprene", "price_type": "south_china", "color": "#73d13d", "axis": "left", "unit": "元/吨"},
                ],
            },
        },
        "snapshot_profile": {
            "lines": [
                {"label": "上海石化异戊二烯挂牌价", "source": "market", "product_id": "isoprene", "price_type": "sinopec_sh", "unit": "元/吨", "wow": "delta"},
                {"label": "华东主流市场价", "source": "market", "product_id": "isoprene", "price_type": "east_china", "unit": "元/吨", "wow": "none"},
                {"label": "华中武汉主流市场价", "source": "market", "product_id": "isoprene", "price_type": "wuhan", "unit": "元/吨", "wow": "none"},
                {"label": "华南主流市场价", "source": "market", "product_id": "isoprene", "price_type": "south_china", "unit": "元/吨", "wow": "none"},
                {"label": "我司 ERP 周均成交价（加权）", "source": "erp", "unit": "元/吨", "wow": "pct"},
            ],
        },
    },
    "piperylene": {
        "chart_profiles": {
            "yearly": {
                "series": [
                    {"key": "east_china", "label": "华东市场价", "source": "market", "product_id": "piperylene", "price_type": "east_china", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "sinopec_sh", "label": "上海石化挂牌价", "source": "market", "product_id": "piperylene", "price_type": "sinopec_sh", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                    {"key": "wuhan", "label": "华中武汉", "source": "market", "product_id": "piperylene", "price_type": "wuhan", "color": "#73d13d", "axis": "left", "unit": "元/吨"},
                ],
            },
            "monthly": {
                "series": [
                    {"key": "east_china", "label": "华东市场价", "source": "market", "product_id": "piperylene", "price_type": "east_china", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "sinopec_sh", "label": "上海石化挂牌价", "source": "market", "product_id": "piperylene", "price_type": "sinopec_sh", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                    {"key": "wuhan", "label": "华中武汉", "source": "market", "product_id": "piperylene", "price_type": "wuhan", "color": "#73d13d", "axis": "left", "unit": "元/吨"},
                ],
            },
        },
        "snapshot_profile": {
            "lines": [
                {"label": "上海石化挂牌价", "source": "market", "product_id": "piperylene", "price_type": "sinopec_sh", "unit": "元/吨", "wow": "delta"},
                {"label": "华东市场价", "source": "market", "product_id": "piperylene", "price_type": "east_china", "unit": "元/吨", "wow": "none"},
                {"label": "我司 ERP 周均成交价（加权）", "source": "erp", "unit": "元/吨", "wow": "pct"},
            ],
        },
    },
    "dcpd": {
        "chart_profiles": {
            "yearly": {
                "series": [
                    {"key": "east_china", "label": "华东市场价", "source": "market", "product_id": "dcpd", "price_type": "east_china", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "sinopec_sh", "label": "上海石化", "source": "market", "product_id": "dcpd", "price_type": "sinopec_sh", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                    {"key": "bulk_sh", "label": "上海大户价", "source": "market", "product_id": "dcpd", "price_type": "bulk_sh", "color": "#13c2c2", "axis": "left", "unit": "元/吨"},
                    {"key": "wuhan", "label": "华中武汉", "source": "market", "product_id": "dcpd", "price_type": "wuhan", "color": "#73d13d", "axis": "left", "unit": "元/吨"},
                ],
            },
            "monthly": {
                "series": [
                    {"key": "east_china", "label": "华东市场价", "source": "market", "product_id": "dcpd", "price_type": "east_china", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "sinopec_sh", "label": "上海石化", "source": "market", "product_id": "dcpd", "price_type": "sinopec_sh", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                    {"key": "bulk_sh", "label": "上海大户价", "source": "market", "product_id": "dcpd", "price_type": "bulk_sh", "color": "#13c2c2", "axis": "left", "unit": "元/吨"},
                    {"key": "wuhan", "label": "华中武汉", "source": "market", "product_id": "dcpd", "price_type": "wuhan", "color": "#73d13d", "axis": "left", "unit": "元/吨"},
                ],
            },
        },
        "snapshot_profile": {
            "lines": [
                {"label": "上海石化双环戊二烯挂牌价", "source": "market", "product_id": "dcpd", "price_type": "sinopec_sh", "unit": "元/吨", "wow": "delta"},
                {"label": "华东主流市场价", "source": "market", "product_id": "dcpd", "price_type": "east_china", "unit": "元/吨", "wow": "none"},
                {"label": "我司 ERP 周均成交价（加权）", "source": "erp", "unit": "元/吨", "wow": "pct"},
            ],
        },
    },
    "sis": {
        "chart_profiles": {
            "yearly": {
                "series": [
                    {"key": "baling_1105", "label": "巴陵1105", "source": "market", "product_id": "sis", "price_type": "baling_1105", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "erp_value", "label": "ERP 加权均价", "source": "erp", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                ],
            },
            "monthly": {
                "series": [
                    {"key": "baling_1105", "label": "巴陵1105", "source": "market", "product_id": "sis", "price_type": "baling_1105", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "erp_value", "label": "ERP 加权均价", "source": "erp", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                ],
            },
        },
        "snapshot_profile": {
            "lines": [
                {"label": "巴陵1105", "source": "market", "product_id": "sis", "price_type": "baling_1105", "unit": "元/吨", "wow": "delta"},
                {"label": "我司 ERP 周均成交价（加权）", "source": "erp", "unit": "元/吨", "wow": "pct"},
            ],
        },
    },
    "styrene": {
        "chart_profiles": {
            "yearly": {
                "series": [
                    {"key": "sinopec_sh", "label": "上海石化", "source": "market", "product_id": "styrene", "price_type": "sinopec_sh", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "maoming", "label": "茂名石化", "source": "market", "product_id": "styrene", "price_type": "maoming", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                    {"key": "guangzhou", "label": "广州石化", "source": "market", "product_id": "styrene", "price_type": "guangzhou", "color": "#73d13d", "axis": "left", "unit": "元/吨"},
                ],
            },
            "monthly": {
                "series": [
                    {"key": "sinopec_sh", "label": "上海石化", "source": "market", "product_id": "styrene", "price_type": "sinopec_sh", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "maoming", "label": "茂名石化", "source": "market", "product_id": "styrene", "price_type": "maoming", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                    {"key": "guangzhou", "label": "广州石化", "source": "market", "product_id": "styrene", "price_type": "guangzhou", "color": "#73d13d", "axis": "left", "unit": "元/吨"},
                ],
            },
        },
        "snapshot_profile": {
            "lines": [
                {"label": "上海石化", "source": "market", "product_id": "styrene", "price_type": "sinopec_sh", "unit": "元/吨", "wow": "delta"},
                {"label": "茂名石化", "source": "market", "product_id": "styrene", "price_type": "maoming", "unit": "元/吨", "wow": "delta"},
                {"label": "广州石化", "source": "market", "product_id": "styrene", "price_type": "guangzhou", "unit": "元/吨", "wow": "delta"},
            ],
        },
    },
    "hydrogenated_c9": {
        "chart_profiles": {
            "yearly": {
                "series": [
                    {"key": "henhe_hc9", "label": "恒河加氢C9", "source": "market", "product_id": "trimethylbenzene", "price_type": "henhe_hc9", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "south_china", "label": "华南市场价", "source": "market", "product_id": "trimethylbenzene", "price_type": "south_china", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                ],
            },
            "monthly": {
                "series": [
                    {"key": "henhe_hc9", "label": "恒河加氢C9", "source": "market", "product_id": "trimethylbenzene", "price_type": "henhe_hc9", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "south_china", "label": "华南市场价", "source": "market", "product_id": "trimethylbenzene", "price_type": "south_china", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                ],
            },
        },
        "snapshot_profile": {
            "lines": [
                {"label": "恒河加氢C9", "source": "market", "product_id": "trimethylbenzene", "price_type": "henhe_hc9", "unit": "元/吨", "wow": "delta"},
                {"label": "华南市场价", "source": "market", "product_id": "trimethylbenzene", "price_type": "south_china", "unit": "元/吨", "wow": "delta"},
            ],
        },
    },
}


DEFAULT_ACCESS_MODEL_CONFIG = {
    "organizations": [
        {
            "id": "org-ecisco",
            "name": "Ecisco",
            "productChain": "C5/C9",
            "regionCoverage": "East China, South China",
        }
    ],
    "departments": [
        {"id": "dept-exec", "name": "Executive Office", "organizationId": "org-ecisco"},
        {"id": "dept-market", "name": "Market Intelligence", "organizationId": "org-ecisco"},
        {"id": "dept-sales", "name": "Sales", "organizationId": "org-ecisco"},
        {"id": "dept-finance", "name": "Finance", "organizationId": "org-ecisco"},
        {"id": "dept-production", "name": "Production", "organizationId": "org-ecisco"},
    ],
    "roles": [
        {
            "id": "role-ceo",
            "name": "CEO",
            "organizationId": "org-ecisco",
            "departmentId": "dept-exec",
            "productScope": "All products",
            "regionScope": "All regions",
            "featureAccess": {
                "chatbot": True,
                "dashboardUpstream": True,
                "dashboardMidstream": True,
                "dashboardDownstream": True,
                "dashboardComparison": True,
                "report": True,
            },
            "dataAccess": "All data including financial, competitor, contracts",
            "dashboardTemplate": "Full dashboard",
        },
        {
            "id": "role-market-analyst",
            "name": "Market Analyst",
            "organizationId": "org-ecisco",
            "departmentId": "dept-market",
            "productScope": "All products",
            "regionScope": "All regions",
            "featureAccess": {
                "chatbot": True,
                "dashboardUpstream": True,
                "dashboardMidstream": True,
                "dashboardDownstream": False,
                "dashboardComparison": True,
                "report": True,
            },
            "dataAccess": "Market + competitor intelligence",
            "dashboardTemplate": "Market monitoring template",
        },
        {
            "id": "role-sales-south",
            "name": "Sales Manager South",
            "organizationId": "org-ecisco",
            "departmentId": "dept-sales",
            "productScope": "Downstream products only",
            "regionScope": "South China only",
            "featureAccess": {
                "chatbot": True,
                "dashboardUpstream": False,
                "dashboardMidstream": True,
                "dashboardDownstream": True,
                "dashboardComparison": True,
                "report": True,
            },
            "dataAccess": "No internal cost structure",
            "dashboardTemplate": "South sales pipeline",
        },
        {
            "id": "role-sales-east",
            "name": "Sales Manager East",
            "organizationId": "org-ecisco",
            "departmentId": "dept-sales",
            "productScope": "Downstream products only",
            "regionScope": "East China only",
            "featureAccess": {
                "chatbot": True,
                "dashboardUpstream": False,
                "dashboardMidstream": True,
                "dashboardDownstream": True,
                "dashboardComparison": True,
                "report": True,
            },
            "dataAccess": "No internal cost structure",
            "dashboardTemplate": "East sales pipeline",
        },
        {
            "id": "role-cfo",
            "name": "CFO",
            "organizationId": "org-ecisco",
            "departmentId": "dept-finance",
            "productScope": "All products",
            "regionScope": "All regions",
            "featureAccess": {
                "chatbot": True,
                "dashboardUpstream": True,
                "dashboardMidstream": True,
                "dashboardDownstream": True,
                "dashboardComparison": False,
                "report": True,
            },
            "dataAccess": "Full financials, margin, cost breakdown",
            "dashboardTemplate": "Financial control tower",
        },
        {
            "id": "role-production",
            "name": "Production Manager",
            "organizationId": "org-ecisco",
            "departmentId": "dept-production",
            "productScope": "Own facility products only",
            "regionScope": "Assigned facilities",
            "featureAccess": {
                "chatbot": True,
                "dashboardUpstream": True,
                "dashboardMidstream": True,
                "dashboardDownstream": False,
                "dashboardComparison": False,
                "report": False,
            },
            "dataAccess": "Operations data only",
            "dashboardTemplate": "Production and capacity board",
        },
    ],
    "accessUsers": [
        {
            "id": "ua-president-liu",
            "username": "President Liu",
            "password": "CHANGE_ME_ON_FIRST_LOGIN",
            "organizationId": "org-ecisco",
            "departmentId": "dept-exec",
            "roleId": "role-ceo",
            "featureAccess": {
                "chatbot": True,
                "dashboardUpstream": True,
                "dashboardMidstream": True,
                "dashboardDownstream": True,
                "dashboardComparison": True,
                "report": True,
            },
            "isActive": True,
            "oneTimeOverride": "",
        },
        {
            "id": "ua-gao",
            "username": "Gao",
            "password": "CHANGE_ME_ON_FIRST_LOGIN",
            "organizationId": "org-ecisco",
            "departmentId": "dept-market",
            "roleId": "role-market-analyst",
            "featureAccess": {
                "chatbot": True,
                "dashboardUpstream": True,
                "dashboardMidstream": True,
                "dashboardDownstream": False,
                "dashboardComparison": True,
                "report": True,
            },
            "isActive": True,
            "oneTimeOverride": "",
        },
        {
            "id": "ua-zhang-wei",
            "username": "Zhang Wei",
            "password": "CHANGE_ME_ON_FIRST_LOGIN",
            "organizationId": "org-ecisco",
            "departmentId": "dept-sales",
            "roleId": "role-sales-south",
            "featureAccess": {
                "chatbot": True,
                "dashboardUpstream": False,
                "dashboardMidstream": True,
                "dashboardDownstream": True,
                "dashboardComparison": True,
                "report": True,
            },
            "isActive": True,
            "oneTimeOverride": "",
        },
        {
            "id": "ua-li-min",
            "username": "Li Min",
            "password": "CHANGE_ME_ON_FIRST_LOGIN",
            "organizationId": "org-ecisco",
            "departmentId": "dept-finance",
            "roleId": "role-cfo",
            "featureAccess": {
                "chatbot": True,
                "dashboardUpstream": True,
                "dashboardMidstream": True,
                "dashboardDownstream": True,
                "dashboardComparison": False,
                "report": True,
            },
            "isActive": True,
            "oneTimeOverride": "",
        },
    ],
}

_ISOPRENE_WUHAN_SNAPSHOT_LINE = {
    "label": "华中武汉主流市场价",
    "source": "market",
    "product_id": "isoprene",
    "price_type": "wuhan",
    "unit": "元/吨",
    "wow": "none",
}


def _normalize_weekly_report_profile(slug: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    if slug != "isoprene" or not isinstance(profile, dict):
        return profile

    snapshot_profile = profile.get("snapshot_profile")
    if not isinstance(snapshot_profile, dict):
        return profile

    lines = snapshot_profile.get("lines")
    if not isinstance(lines, list):
        return profile

    if any(
        isinstance(line, dict) and str(line.get("price_type") or "") == "wuhan"
        for line in lines
    ):
        return profile

    normalized_profile = dict(profile)
    normalized_snapshot = dict(snapshot_profile)
    normalized_lines = [dict(line) if isinstance(line, dict) else line for line in lines]
    insert_at = next(
        (
            index
            for index, line in enumerate(normalized_lines)
            if isinstance(line, dict) and str(line.get("price_type") or "") == "south_china"
        ),
        len(normalized_lines),
    )
    normalized_lines.insert(insert_at, dict(_ISOPRENE_WUHAN_SNAPSHOT_LINE))
    normalized_snapshot["lines"] = normalized_lines
    normalized_profile["snapshot_profile"] = normalized_snapshot
    return normalized_profile


class Database:
    """Session/message store using the configured MySQL database."""

    def __init__(self, db_url: str | None = None):
        repo_root = Path(__file__).resolve().parents[3]
        load_dotenv(repo_root / ".env")

        configured_url = db_url or os.getenv("DATABASE_URL")
        fallback_path = repo_root / "backend" / "local_auth_fallback.sqlite3"
        fallback_url = f"sqlite:///{fallback_path}"

        if configured_url:
            try:
                self.engine = get_engine(configured_url)
                self.init_db()
                return
            except SQLAlchemyError:
                logging.getLogger(__name__).warning(
                    "Primary DATABASE_URL unavailable; falling back to local SQLite at %s",
                    fallback_path,
                    exc_info=True,
                )

        # Local resilience mode for development: keep auth/session APIs available
        # even when remote MySQL is down.
        self.engine = sa_create_engine(
            fallback_url,
            connect_args={"check_same_thread": False},
            future=True,
        )
        self.init_db()

    def init_db(self) -> None:
        """Create sessions/messages tables if they do not exist.

        Uses generic SQL types that work for both SQLite and MySQL.
        """

        ddl_sessions = text(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id VARCHAR(64) PRIMARY KEY,
                user_id VARCHAR(64),
                title VARCHAR(255),
                created_at VARCHAR(64)
            )
            """
        )

        ddl_messages = text(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id VARCHAR(64) PRIMARY KEY,
                session_id VARCHAR(64),
                role VARCHAR(32),
                content TEXT,
                context_json TEXT,
                timestamp VARCHAR(64),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
            """
        )

        ddl_decision_history = text(
            """
            CREATE TABLE IF NOT EXISTS decision_history (
                product_id VARCHAR(255),
                date VARCHAR(64),
                recommendation_price DECIMAL(10, 2),
                confidence DECIMAL(3, 2),
                reasoning_hash VARCHAR(64),
                factors_json TEXT,
                scenario_json TEXT,
                PRIMARY KEY (product_id, date)
            )
            """
        )

        ddl_decision_log = text(
            """
            CREATE TABLE IF NOT EXISTS decision_log (
                id VARCHAR(64) PRIMARY KEY,
                product_id VARCHAR(60) NOT NULL,
                product_name VARCHAR(120),
                created_at DATETIME NOT NULL,
                user_id VARCHAR(64),
                session_id VARCHAR(64),
                ai_price_low REAL,
                ai_price_high REAL,
                ai_price_mid REAL,
                price_unit VARCHAR(32),
                ai_action VARCHAR(32),
                ai_confidence REAL,
                ai_reasoning TEXT,
                market_snapshot_json TEXT,
                executed_price REAL,
                executed_at DATETIME,
                executed_by VARCHAR(64),
                outcome VARCHAR(32) DEFAULT 'pending',
                outcome_reason VARCHAR(255),
                outcome_competitor_price REAL,
                outcome_note VARCHAR(500),
                outcome_at DATETIME,
                price_error_cny REAL,
                price_in_range INTEGER,
                status VARCHAR(32) DEFAULT 'pending',
                source VARCHAR(32) DEFAULT 'chat'
            )
            """
        )

        ddl_alerts_log = text(
            """
            CREATE TABLE IF NOT EXISTS alerts_log (
                id VARCHAR(64) PRIMARY KEY,
                product VARCHAR(64) NOT NULL,
                product_name VARCHAR(128) NOT NULL,
                alert_type VARCHAR(32) NOT NULL,
                severity VARCHAR(16) NOT NULL,
                change_7d_pct DECIMAL(8, 2) NOT NULL,
                price DECIMAL(12, 2) NOT NULL,
                message TEXT,
                triggered_at DATETIME NOT NULL,
                read_at DATETIME NULL,
                dismissed_at DATETIME NULL
            )
            """
        )

        ddl_users = text(
            """
            CREATE TABLE IF NOT EXISTS users (
                id VARCHAR(64) PRIMARY KEY,
                username VARCHAR(64) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                password_salt VARCHAR(255) NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                created_at VARCHAR(64) NOT NULL,
                created_by VARCHAR(64) NULL
            )
            """
        )

        ddl_auth_tokens = text(
            """
            CREATE TABLE IF NOT EXISTS auth_tokens (
                token_hash VARCHAR(64) PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                created_at VARCHAR(64) NOT NULL,
                expires_at VARCHAR(64) NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )

        ddl_inventory_safety_config = text(
            """
            CREATE TABLE IF NOT EXISTS inventory_safety_config (
                product_id VARCHAR(64) PRIMARY KEY,
                name_cn VARCHAR(128) NOT NULL,
                safety_min DECIMAL(12, 2) NOT NULL,
                safety_max DECIMAL(12, 2) NOT NULL,
                daily_consumption DECIMAL(12, 2) NOT NULL,
                updated_at VARCHAR(64) NOT NULL,
                updated_by VARCHAR(64) NULL
            )
            """
        )

        ddl_access_model_config = text(
            """
            CREATE TABLE IF NOT EXISTS access_model_config (
                id VARCHAR(64) PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at VARCHAR(64) NOT NULL,
                updated_by VARCHAR(64) NULL
            )
            """
        )

        ddl_generated_reports = text(
            """
            CREATE TABLE IF NOT EXISTS generated_reports (
                id VARCHAR(64) PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                session_id VARCHAR(64) NOT NULL,
                report_type VARCHAR(64) NOT NULL,
                title VARCHAR(255) NOT NULL,
                summary TEXT,
                markdown TEXT NOT NULL,
                blocks_json TEXT NULL,
                context_json TEXT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'draft',
                approved_at VARCHAR(64) NULL,
                approved_by VARCHAR(64) NULL,
                read_at VARCHAR(64) NULL,
                last_refreshed_at VARCHAR(64) NULL,
                created_at VARCHAR(64) NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
            """
        )

        ddl_report_schedules = text(
            """
            CREATE TABLE IF NOT EXISTS report_schedules (
                id VARCHAR(64) PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                report_type VARCHAR(64) NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                weekday INTEGER NOT NULL DEFAULT 0,
                hour INTEGER NOT NULL DEFAULT 8,
                minute INTEGER NOT NULL DEFAULT 0,
                timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
                last_run_at VARCHAR(64) NULL,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL
            )
            """
        )

        ddl_report_deliveries = text(
            """
            CREATE TABLE IF NOT EXISTS report_deliveries (
                id VARCHAR(64) PRIMARY KEY,
                report_id VARCHAR(64) NOT NULL,
                sender_user_id VARCHAR(64) NOT NULL,
                delivery_mode VARCHAR(32) NOT NULL,
                recipient_ids_json TEXT NOT NULL,
                recipient_labels_json TEXT NOT NULL,
                message TEXT NULL,
                status VARCHAR(32) NOT NULL,
                provider VARCHAR(64) NULL,
                delivered_at VARCHAR(64) NOT NULL,
                FOREIGN KEY (report_id) REFERENCES generated_reports(id)
            )
            """
        )

        ddl_report_templates = text(
            """
            CREATE TABLE IF NOT EXISTS report_templates (
                id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT NULL,
                time_period VARCHAR(64) NOT NULL,
                audience VARCHAR(255) NULL,
                schedule_hint VARCHAR(255) NULL,
                template_title VARCHAR(255) NULL,
                template_markdown TEXT NULL,
                template_blocks_json TEXT NULL,
                sections_json TEXT NOT NULL,
                category VARCHAR(128) NULL,
                thumbnail VARCHAR(255) NULL,
                estimated_time VARCHAR(64) NULL,
                tags_json TEXT NULL,
                is_system INTEGER NOT NULL DEFAULT 0,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL,
                updated_by VARCHAR(64) NULL
            )
            """
        )

        ddl_hidden_gallery_templates = text(
            """
            CREATE TABLE IF NOT EXISTS hidden_gallery_templates (
                user_id VARCHAR(64) NOT NULL,
                template_id VARCHAR(255) NOT NULL,
                hidden_at VARCHAR(64) NOT NULL,
                PRIMARY KEY (user_id, template_id)
            )
            """
        )

        ddl_pinned_report_templates = text(
            """
            CREATE TABLE IF NOT EXISTS pinned_report_templates (
                user_id VARCHAR(64) NOT NULL,
                template_id VARCHAR(255) NOT NULL,
                pinned_at VARCHAR(64) NOT NULL,
                PRIMARY KEY (user_id, template_id)
            )
            """
        )

        ddl_report_image_assets = text(
            """
            CREATE TABLE IF NOT EXISTS report_image_assets (
                id VARCHAR(64) PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                original_filename VARCHAR(255) NOT NULL,
                mime_type VARCHAR(128) NOT NULL,
                file_size INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                public_url TEXT NOT NULL,
                created_at VARCHAR(64) NOT NULL
            )
            """
        )

        ddl_report_categories = text(
            """
            CREATE TABLE IF NOT EXISTS report_categories (
                id VARCHAR(64) PRIMARY KEY,
                org_id VARCHAR(64) NOT NULL,
                parent_id VARCHAR(64) NULL,
                name VARCHAR(255) NOT NULL,
                icon VARCHAR(64) NULL,
                position INTEGER NOT NULL DEFAULT 0,
                permissions_json TEXT NULL,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL,
                created_by VARCHAR(64) NULL
            )
            """
        )

        ddl_cockpit_definitions = text(
            """
            CREATE TABLE IF NOT EXISTS cockpit_definitions (
                id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                org_id VARCHAR(64) NOT NULL,
                department_id VARCHAR(64) NULL,
                layout_json TEXT NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'draft',
                version INTEGER NOT NULL DEFAULT 1,
                is_default INTEGER NOT NULL DEFAULT 0,
                permissions_json TEXT NULL,
                created_by VARCHAR(64) NULL,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL,
                published_at VARCHAR(64) NULL
            )
            """
        )

        ddl_alert_rules = text(
            """
            CREATE TABLE IF NOT EXISTS alert_rules (
                id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                org_id VARCHAR(64) NOT NULL,
                created_by VARCHAR(64) NULL,
                metric_expression TEXT NOT NULL,
                condition_type VARCHAR(32) NOT NULL,
                condition_params_json TEXT NOT NULL,
                severity VARCHAR(16) NOT NULL,
                category VARCHAR(32) NOT NULL,
                notification_channels_json TEXT NOT NULL,
                default_owner_id VARCHAR(64) NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                evaluation_interval_min INTEGER NOT NULL DEFAULT 15,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL
            )
            """
        )

        ddl_data_sources = text(
            """
            CREATE TABLE IF NOT EXISTS data_sources (
                id VARCHAR(64) PRIMARY KEY,
                org_id VARCHAR(64) NOT NULL,
                name VARCHAR(255) NOT NULL,
                connector_type VARCHAR(32) NOT NULL,
                connection_config_json TEXT NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                last_tested_at VARCHAR(64) NULL,
                created_by VARCHAR(64) NULL,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL
            )
            """
        )

        ddl_datasets = text(
            """
            CREATE TABLE IF NOT EXISTS datasets (
                id VARCHAR(64) PRIMARY KEY,
                source_id VARCHAR(64) NOT NULL,
                org_id VARCHAR(64) NOT NULL,
                friendly_name VARCHAR(255) NOT NULL,
                physical_reference VARCHAR(512) NOT NULL,
                description TEXT NULL,
                tags_json TEXT NULL,
                schema_snapshot_json TEXT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL,
                FOREIGN KEY (source_id) REFERENCES data_sources(id)
            )
            """
        )

        ddl_dataset_column_mappings = text(
            """
            CREATE TABLE IF NOT EXISTS dataset_column_mappings (
                dataset_id VARCHAR(64) NOT NULL,
                physical_column VARCHAR(128) NOT NULL,
                ontology_concept VARCHAR(128) NOT NULL,
                transform VARCHAR(64) NULL,
                PRIMARY KEY (dataset_id, physical_column),
                FOREIGN KEY (dataset_id) REFERENCES datasets(id)
            )
            """
        )

        ddl_dataset_permissions = text(
            """
            CREATE TABLE IF NOT EXISTS dataset_permissions (
                dataset_id VARCHAR(64) NOT NULL,
                role_id VARCHAR(64) NOT NULL,
                can_query INTEGER NOT NULL DEFAULT 0,
                can_export INTEGER NOT NULL DEFAULT 0,
                can_chat INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (dataset_id, role_id),
                FOREIGN KEY (dataset_id) REFERENCES datasets(id)
            )
            """
        )

        ddl_platform_products = text(
            """
            CREATE TABLE IF NOT EXISTS platform_products (
                id VARCHAR(64) PRIMARY KEY,
                org_id VARCHAR(64) NOT NULL,
                name_en VARCHAR(255) NOT NULL,
                name_cn VARCHAR(255) NOT NULL,
                product_type VARCHAR(64) NOT NULL,
                unit VARCHAR(64) NOT NULL DEFAULT 'CNY/ton',
                description TEXT NULL,
                aliases_json TEXT NULL,
                data_table VARCHAR(255) NULL,
                data_key VARCHAR(128) NULL,
                display_in_json TEXT NULL,
                created_by VARCHAR(64) NULL,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL
            )
            """
        )

        ddl_erp_price_recommendations = text(
            """
            CREATE TABLE IF NOT EXISTS erp_price_recommendations (
                id VARCHAR(36) PRIMARY KEY,
                product_id VARCHAR(64) NOT NULL,
                recommended_price DECIMAL(12, 2) NOT NULL,
                valid_from DATE NOT NULL,
                valid_until DATE NULL,
                rationale TEXT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                submitted_by VARCHAR(64) NOT NULL,
                session_id VARCHAR(64) NULL,
                approved_by VARCHAR(64) NULL,
                approved_at VARCHAR(64) NULL,
                committed_at VARCHAR(64) NULL,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL
            )
            """
        )

        with self.engine.begin() as conn:
            conn.execute(ddl_sessions)
            conn.execute(ddl_messages)
            conn.execute(ddl_decision_history)
            conn.execute(ddl_decision_log)
            conn.execute(ddl_alerts_log)
            conn.execute(ddl_users)
            conn.execute(ddl_auth_tokens)
            conn.execute(ddl_inventory_safety_config)
            conn.execute(ddl_access_model_config)
            conn.execute(ddl_generated_reports)
            conn.execute(ddl_report_schedules)
            conn.execute(ddl_report_deliveries)
            conn.execute(ddl_report_templates)
            conn.execute(ddl_hidden_gallery_templates)
            conn.execute(ddl_pinned_report_templates)
            conn.execute(ddl_report_image_assets)
            conn.execute(ddl_report_categories)
            conn.execute(ddl_cockpit_definitions)
            conn.execute(ddl_alert_rules)
            conn.execute(ddl_data_sources)
            conn.execute(ddl_datasets)
            conn.execute(ddl_dataset_column_mappings)
            conn.execute(ddl_dataset_permissions)
            conn.execute(ddl_platform_products)
            conn.execute(ddl_erp_price_recommendations)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS report_product_catalog (
                    slug VARCHAR(64) PRIMARY KEY,
                    name_zh VARCHAR(128) NOT NULL,
                    name_en VARCHAR(128) NOT NULL,
                    category VARCHAR(64) NOT NULL DEFAULT 'liquid_product',
                    in_scope INTEGER NOT NULL DEFAULT 1,
                    lz_view VARCHAR(128) NULL,
                    erp_sales_view VARCHAR(255) NULL,
                    report_profile_json TEXT NULL,
                    forecast_key VARCHAR(128) NULL,
                    sort_order INTEGER NOT NULL DEFAULT 99
                )
            """))
        # ------------------------------------------------------------------
        # Post-DDL: seed data in separate transactions so a failure here
        # does NOT poison the DDL transaction (MySQL-compatible).
        # ------------------------------------------------------------------
        _is_sqlite = self.engine.dialect.name == "sqlite"
        _insert_ignore = "INSERT OR IGNORE" if _is_sqlite else "INSERT IGNORE"

        # Seed a system session for reports generated outside a chat session
        try:
            with self.engine.begin() as conn:
                conn.execute(text(f"""
                    {_insert_ignore} INTO sessions (id, user_id, title, created_at)
                    VALUES ('system', 'system', 'System Generated Reports', '2020-01-01T00:00:00')
                """))
        except Exception as _seed_err:
            logging.getLogger(__name__).debug(
                "System session seed skipped (already exists or not supported): %s", _seed_err
            )

        # Seed the C5/C9 product catalog if empty
        try:
            with self.engine.connect() as conn:
                catalog_count = conn.execute(
                    text("SELECT COUNT(*) FROM report_product_catalog")
                ).scalar() or 0
        except Exception:
            catalog_count = 0

        if catalog_count == 0:
            catalog_rows = [
                ("cracked_c5", "裂解 C5", "Cracked C5", "liquid_product", 1, "lz_v_裂解c5_data", "sale_erp_v_工业用裂解碳五_data", json.dumps(DEFAULT_WEEKLY_REPORT_PROFILES.get("cracked_c5") or {}, ensure_ascii=False), "cracked_c5", 1),
                ("isoprene", "异戊二烯", "Isoprene", "liquid_product", 1, "lz_v_异戊二烯_data", "sale_erp_v_异戊二烯_data", json.dumps(DEFAULT_WEEKLY_REPORT_PROFILES.get("isoprene") or {}, ensure_ascii=False), "isoprene", 2),
                ("sis", "SIS", "SIS", "liquid_product", 1, "lz_v_sis_data", "sale_erp_v_sis_d2015_data", json.dumps(DEFAULT_WEEKLY_REPORT_PROFILES.get("sis") or {}, ensure_ascii=False), "sis", 3),
                ("piperylene", "间戊二烯", "Piperylene", "liquid_product", 1, "lz_v_间戊二烯_data", "sale_erp_v_间戊二烯_data", json.dumps(DEFAULT_WEEKLY_REPORT_PROFILES.get("piperylene") or {}, ensure_ascii=False), "piperylene", 4),
                ("road_marking_resin", "路标漆 C5 树脂", "Road-marking C5 Resin", "resin", 1, None, "sale_erp_v_碳五石油树脂_data", json.dumps(DEFAULT_WEEKLY_REPORT_PROFILES.get("road_marking_resin") or {}, ensure_ascii=False), "road_marking_resin", 5),
                ("adhesive_resin", "胶粘剂 C5 树脂", "Adhesive C5 Resin", "resin", 1, None, "sale_erp_v_碳五石油树脂_data", json.dumps(DEFAULT_WEEKLY_REPORT_PROFILES.get("adhesive_resin") or {}, ensure_ascii=False), "adhesive_resin", 6),
                ("styrene", "苯乙烯", "Styrene", "liquid_product", 1, "lz_v_苯乙烯_data", None, json.dumps(DEFAULT_WEEKLY_REPORT_PROFILES.get("styrene") or {}, ensure_ascii=False), "styrene", 7),
                ("dcpd", "双环戊二烯", "DCPD", "liquid_product", 1, "lz_v_双环戊二烯_data", "sale_erp_v_双环戊二烯_data", json.dumps(DEFAULT_WEEKLY_REPORT_PROFILES.get("dcpd") or {}, ensure_ascii=False), "dcpd", 8),
                ("pentane_blowing_agent", "抽余 C5 (戊烷发泡剂)", "Raffinate C5 / Pentane Blowing Agent", "liquid_product", 1, "lz_v_戊烷泡发剂_data", "sale_erp_v_戊烷发泡剂_data", json.dumps(DEFAULT_WEEKLY_REPORT_PROFILES.get("pentane_blowing_agent") or {}, ensure_ascii=False), "pentane_blowing_agent", 9),
                ("cracked_c9", "裂解 C9", "Cracked C9", "liquid_product", 1, "lz_v_裂解c9_data", "sale_erp_v_工业用裂解碳九_data", json.dumps(DEFAULT_WEEKLY_REPORT_PROFILES.get("cracked_c9") or {}, ensure_ascii=False), "cracked_c9", 10),
                ("hydrogenated_c9", "混三甲苯 (加氢 C9)", "Mixed Trimethylbenzene / Hydrogenated C9", "liquid_product", 1, "lz_v_混三甲苯_data", "sale_erp_v_混三甲苯_data", json.dumps(DEFAULT_WEEKLY_REPORT_PROFILES.get("hydrogenated_c9") or {}, ensure_ascii=False), "hydrogenated_c9", 11),
            ]
            for row in catalog_rows:
                try:
                    with self.engine.begin() as conn:
                        conn.execute(text(f"""
                            {_insert_ignore} INTO report_product_catalog
                                (slug, name_zh, name_en, category, in_scope, lz_view, erp_sales_view, report_profile_json, forecast_key, sort_order)
                            VALUES (:slug, :name_zh, :name_en, :category, :in_scope, :lz_view, :erp_sales_view, :report_profile_json, :forecast_key, :sort_order)
                        """), {
                            "slug": row[0], "name_zh": row[1], "name_en": row[2],
                            "category": row[3], "in_scope": row[4], "lz_view": row[5],
                            "erp_sales_view": row[6], "report_profile_json": row[7], "forecast_key": row[8], "sort_order": row[9],
                        })
                except Exception:
                    pass  # Duplicate or not supported — skip

        index_ddls = []
        if self.engine.dialect.name == "sqlite":
            index_ddls = [
                text(
                    "CREATE INDEX IF NOT EXISTS idx_alerts_product ON alerts_log (product)"
                ),
                text(
                    "CREATE INDEX IF NOT EXISTS idx_alerts_triggered ON alerts_log (triggered_at)"
                ),
                text(
                    "CREATE INDEX IF NOT EXISTS idx_alerts_dismissed ON alerts_log (dismissed_at)"
                ),
            ]
        else:
            index_ddls = [
                text("CREATE INDEX idx_alerts_product ON alerts_log (product)"),
                text("CREATE INDEX idx_alerts_triggered ON alerts_log (triggered_at)"),
                text("CREATE INDEX idx_alerts_dismissed ON alerts_log (dismissed_at)"),
            ]

        for index_ddl in index_ddls:
            try:
                with self.engine.begin() as conn:
                    conn.execute(index_ddl)
            except Exception:
                pass

        # Best-effort migration for existing databases created before context_json existed.
        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE messages ADD COLUMN context_json TEXT"))
        except Exception:
            # Column likely already exists (or backend doesn't support ALTER). Ignore.
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE decision_log ADD COLUMN price_unit VARCHAR(32) NULL"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE generated_reports ADD COLUMN read_at VARCHAR(64) NULL"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE generated_reports ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'draft'"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE generated_reports ADD COLUMN approved_at VARCHAR(64) NULL"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE generated_reports ADD COLUMN approved_by VARCHAR(64) NULL"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE generated_reports ADD COLUMN context_json TEXT NULL"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE generated_reports ADD COLUMN blocks_json TEXT NULL"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE generated_reports ADD COLUMN last_refreshed_at VARCHAR(64) NULL"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE report_templates ADD COLUMN template_title VARCHAR(255) NULL"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE report_templates ADD COLUMN template_markdown TEXT NULL"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE report_templates ADD COLUMN template_blocks_json TEXT NULL"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE report_templates ADD COLUMN category VARCHAR(128) NULL"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE report_templates ADD COLUMN thumbnail VARCHAR(255) NULL"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE report_templates ADD COLUMN estimated_time VARCHAR(64) NULL"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE report_templates ADD COLUMN tags_json TEXT NULL"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE report_templates ADD COLUMN is_system INTEGER NOT NULL DEFAULT 0"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
                    )
                )
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE report_product_catalog ADD COLUMN report_profile_json TEXT NULL"))
        except Exception:
            pass

        for slug, profile in DEFAULT_WEEKLY_REPORT_PROFILES.items():
            try:
                with self.engine.begin() as conn:
                    conn.execute(
                        text(
                            """
                            UPDATE report_product_catalog
                            SET report_profile_json = :profile_json
                            WHERE slug = :slug
                              AND (report_profile_json IS NULL OR TRIM(report_profile_json) = '')
                            """
                        ),
                        {
                            "slug": slug,
                            "profile_json": json.dumps(
                                _normalize_weekly_report_profile(slug, profile),
                                ensure_ascii=False,
                            ),
                        },
                    )
            except Exception:
                pass

        try:
            with self.engine.begin() as conn:
                existing_profile_json = conn.execute(
                    text(
                        "SELECT report_profile_json FROM report_product_catalog WHERE slug = 'isoprene'"
                    )
                ).scalar()
                if existing_profile_json and str(existing_profile_json).strip():
                    try:
                        existing_profile = json.loads(str(existing_profile_json))
                    except Exception:
                        existing_profile = None
                    normalized_profile = _normalize_weekly_report_profile(
                        "isoprene", existing_profile or {}
                    )
                    if existing_profile != normalized_profile:
                        conn.execute(
                            text(
                                """
                                UPDATE report_product_catalog
                                SET report_profile_json = :profile_json
                                WHERE slug = 'isoprene'
                                """
                            ),
                            {
                                "profile_json": json.dumps(
                                    normalized_profile, ensure_ascii=False
                                )
                            },
                        )
        except Exception:
            pass

        self.ensure_default_admin()
        self.ensure_default_inventory_safety_config()
        self.ensure_default_access_model_config()

    def _utcnow(self) -> str:
        return datetime.utcnow().replace(microsecond=0).isoformat()

    def _serialize_user(self, row: Dict[str, Any]) -> Dict[str, Any]:
        user = dict(row)
        user["is_admin"] = bool(int(user.get("is_admin") or 0))
        user["is_active"] = bool(int(user.get("is_active") or 0))
        user["must_change_password"] = bool(int(user.get("must_change_password") or 0))
        return user

    def _public_user(self, user: Dict[str, Any]) -> Dict[str, Any]:
        access_profile = self.get_access_profile_by_username(str(user.get("username", "")))
        return {
            "id": user["id"],
            "username": user["username"],
            "is_admin": bool(user.get("is_admin")),
            "is_active": bool(user.get("is_active", True)),
            "must_change_password": bool(user.get("must_change_password", False)),
            "created_at": user.get("created_at"),
            "created_by": user.get("created_by"),
            "organization_id": access_profile.get("organization_id"),
            "organization_name": access_profile.get("organization_name"),
            "department_id": access_profile.get("department_id"),
            "department_name": access_profile.get("department_name"),
            "role_id": access_profile.get("role_id"),
            "role_name": access_profile.get("role_name"),
            "feature_access": access_profile.get("feature_access"),
        }

    def get_access_profile_by_username(self, username: str) -> Dict[str, Any]:
        normalized_username = normalize_username(username)
        if not normalized_username:
            return {}

        config = self.get_access_model_config()
        access_users = config.get("accessUsers") or []
        organizations = {
            str(item.get("id")): item for item in (config.get("organizations") or [])
        }
        departments = {
            str(item.get("id")): item for item in (config.get("departments") or [])
        }
        roles = {
            str(item.get("id")): item for item in (config.get("roles") or [])
        }

        access_user = next(
            (
                item
                for item in access_users
                if normalize_username(str(item.get("username", ""))) == normalized_username
            ),
            None,
        )
        if not access_user:
            return {}

        organization = organizations.get(str(access_user.get("organizationId") or ""), {})
        department = departments.get(str(access_user.get("departmentId") or ""), {})
        role = roles.get(str(access_user.get("roleId") or ""), {})

        return {
            "organization_id": access_user.get("organizationId"),
            "organization_name": organization.get("name"),
            "department_id": access_user.get("departmentId"),
            "department_name": department.get("name"),
            "role_id": access_user.get("roleId"),
            "role_name": role.get("name"),
            "feature_access": access_user.get("featureAccess") or role.get("featureAccess") or {},
        }

    def ensure_default_admin(self) -> None:
        stmt = text("SELECT COUNT(*) AS count FROM users")
        with self.engine.connect() as conn:
            existing_count = int(conn.execute(stmt).scalar() or 0)

        if existing_count > 0:
            return

        password_hash, password_salt = hash_password("admin")
        admin_id = str(uuid.uuid4())
        created_at = self._utcnow()
        insert_stmt = text(
            """
            INSERT INTO users (
                id,
                username,
                password_hash,
                password_salt,
                is_admin,
                is_active,
                must_change_password,
                created_at,
                created_by
            )
            VALUES (
                :id,
                :username,
                :password_hash,
                :password_salt,
                :is_admin,
                :is_active,
                :must_change_password,
                :created_at,
                :created_by
            )
            """
        )

        with self.engine.begin() as conn:
            conn.execute(
                insert_stmt,
                {
                    "id": admin_id,
                    "username": "admin",
                    "password_hash": password_hash,
                    "password_salt": password_salt,
                    "is_admin": 1,
                    "is_active": 1,
                    "must_change_password": 0,
                    "created_at": created_at,
                    "created_by": "system",
                },
            )

    def ensure_default_inventory_safety_config(self) -> None:
        existing_stmt = text(
            "SELECT COUNT(*) FROM inventory_safety_config WHERE product_id = :product_id"
        )
        insert_stmt = text(
            """
            INSERT INTO inventory_safety_config (
                product_id,
                name_cn,
                safety_min,
                safety_max,
                daily_consumption,
                updated_at,
                updated_by
            )
            VALUES (
                :product_id,
                :name_cn,
                :safety_min,
                :safety_max,
                :daily_consumption,
                :updated_at,
                :updated_by
            )
            """
        )

        with self.engine.begin() as conn:
            for product_id, cfg in DEFAULT_INVENTORY_SAFETY_CONFIG.items():
                exists = int(
                    conn.execute(existing_stmt, {"product_id": product_id}).scalar()
                    or 0
                )
                if exists > 0:
                    continue
                conn.execute(
                    insert_stmt,
                    {
                        "product_id": product_id,
                        "name_cn": cfg["name_cn"],
                        "safety_min": cfg["safety_min"],
                        "safety_max": cfg["safety_max"],
                        "daily_consumption": cfg["daily_consumption"],
                        "updated_at": self._utcnow(),
                        "updated_by": "system_seed",
                    },
                )

    def ensure_default_access_model_config(self) -> None:
        exists_stmt = text(
            "SELECT COUNT(*) FROM access_model_config WHERE id = :id"
        )
        insert_stmt = text(
            """
            INSERT INTO access_model_config (id, payload_json, updated_at, updated_by)
            VALUES (:id, :payload_json, :updated_at, :updated_by)
            """
        )

        with self.engine.begin() as conn:
            existing_count = int(conn.execute(exists_stmt, {"id": "default"}).scalar() or 0)
            if existing_count > 0:
                return
            conn.execute(
                insert_stmt,
                {
                    "id": "default",
                    "payload_json": json.dumps(DEFAULT_ACCESS_MODEL_CONFIG, ensure_ascii=False),
                    "updated_at": self._utcnow(),
                    "updated_by": "system_seed",
                },
            )

    def get_access_model_config(self) -> Dict[str, Any]:
        stmt = text(
            """
            SELECT payload_json, updated_at, updated_by
            FROM access_model_config
            WHERE id = :id
            LIMIT 1
            """
        )

        with self.engine.connect() as conn:
            row = conn.execute(stmt, {"id": "default"}).first()

        if not row:
            return {
                **DEFAULT_ACCESS_MODEL_CONFIG,
                "updated_at": None,
                "updated_by": None,
            }

        mapped = dict(row._mapping)
        payload_raw = mapped.get("payload_json")
        payload: Dict[str, Any]
        if isinstance(payload_raw, str) and payload_raw.strip():
            try:
                payload = json.loads(payload_raw)
            except Exception:
                payload = dict(DEFAULT_ACCESS_MODEL_CONFIG)
        else:
            payload = dict(DEFAULT_ACCESS_MODEL_CONFIG)

        payload.setdefault("organizations", [])
        payload.setdefault("departments", [])
        payload.setdefault("roles", [])
        payload.setdefault("accessUsers", [])
        payload["updated_at"] = mapped.get("updated_at")
        payload["updated_by"] = mapped.get("updated_by")
        return payload

    def save_access_model_config(
        self,
        payload: Dict[str, Any],
        updated_by: Optional[str],
    ) -> Dict[str, Any]:
        sanitized = {
            "organizations": payload.get("organizations") or [],
            "departments": payload.get("departments") or [],
            "roles": payload.get("roles") or [],
            "accessUsers": payload.get("accessUsers") or [],
        }
        if not all(isinstance(sanitized[key], list) for key in sanitized):
            raise ValueError("Access model payload must use list fields")

        now = self._utcnow()
        stmt = text(
            """
            UPDATE access_model_config
            SET payload_json = :payload_json,
                updated_at = :updated_at,
                updated_by = :updated_by
            WHERE id = :id
            """
        )
        with self.engine.begin() as conn:
            result = conn.execute(
                stmt,
                {
                    "id": "default",
                    "payload_json": json.dumps(sanitized, ensure_ascii=False),
                    "updated_at": now,
                    "updated_by": updated_by,
                },
            )
            if result.rowcount == 0:
                conn.execute(
                    text(
                        """
                        INSERT INTO access_model_config (id, payload_json, updated_at, updated_by)
                        VALUES (:id, :payload_json, :updated_at, :updated_by)
                        """
                    ),
                    {
                        "id": "default",
                        "payload_json": json.dumps(sanitized, ensure_ascii=False),
                        "updated_at": now,
                        "updated_by": updated_by,
                    },
                )

        saved = dict(sanitized)
        saved["updated_at"] = now
        saved["updated_by"] = updated_by
        return saved

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        normalized_username = normalize_username(username)
        if not normalized_username:
            return None

        stmt = text(
            """
            SELECT id, username, password_hash, password_salt, is_admin, is_active, must_change_password, created_at, created_by
            FROM users
            WHERE username = :username
            LIMIT 1
            """
        )

        with self.engine.connect() as conn:
            result = conn.execute(stmt, {"username": normalized_username}).first()
            if not result:
                return None
            return self._serialize_user(dict(result._mapping))

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, username, password_hash, password_salt, is_admin, is_active, must_change_password, created_at, created_by
            FROM users
            WHERE id = :user_id
            LIMIT 1
            """
        )

        with self.engine.connect() as conn:
            result = conn.execute(stmt, {"user_id": user_id}).first()
            if not result:
                return None
            return self._serialize_user(dict(result._mapping))

    def list_users(self) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, username, is_admin, is_active, must_change_password, created_at, created_by
            FROM users
            ORDER BY username ASC
            """
        )

        with self.engine.connect() as conn:
            result = conn.execute(stmt)
            return [self._public_user(self._serialize_user(dict(row._mapping))) for row in result]

    def create_user(
        self,
        username: str,
        password: str,
        is_admin: bool = False,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_username = normalize_username(username)
        if not normalized_username:
            raise ValueError("Username is required")
        if len(normalized_username) > 64:
            raise ValueError("Username must be 64 characters or fewer")
        if len(password or "") < 4:
            raise ValueError("Password must be at least 4 characters")
        if self.get_user_by_username(normalized_username):
            raise ValueError("Username already exists")

        password_hash, password_salt = hash_password(password)
        user_id = str(uuid.uuid4())
        created_at = self._utcnow()

        stmt = text(
            """
            INSERT INTO users (
                id,
                username,
                password_hash,
                password_salt,
                is_admin,
                is_active,
                must_change_password,
                created_at,
                created_by
            )
            VALUES (
                :id,
                :username,
                :password_hash,
                :password_salt,
                :is_admin,
                :is_active,
                :must_change_password,
                :created_at,
                :created_by
            )
            """
        )

        with self.engine.begin() as conn:
            conn.execute(
                stmt,
                {
                    "id": user_id,
                    "username": normalized_username,
                    "password_hash": password_hash,
                    "password_salt": password_salt,
                    "is_admin": 1 if is_admin else 0,
                    "is_active": 1,
                    "must_change_password": 1,
                    "created_at": created_at,
                    "created_by": created_by,
                },
            )

        created_user = self.get_user_by_id(user_id)
        if not created_user:
            raise RuntimeError("Failed to create user")
        return self._public_user(created_user)

    def create_auth_token(self, user_id: str, expires_at: str) -> str:
        raw_token = create_session_token()
        token_hash = hash_token(raw_token)
        created_at = self._utcnow()
        stmt = text(
            """
            INSERT INTO auth_tokens (token_hash, user_id, created_at, expires_at)
            VALUES (:token_hash, :user_id, :created_at, :expires_at)
            """
        )

        with self.engine.begin() as conn:
            conn.execute(
                stmt,
                {
                    "token_hash": token_hash,
                    "user_id": user_id,
                    "created_at": created_at,
                    "expires_at": expires_at,
                },
            )

        return raw_token

    def get_user_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None

        stmt = text(
            """
            SELECT u.id, u.username, u.password_hash, u.password_salt, u.is_admin, u.is_active, u.must_change_password, u.created_at, u.created_by
            FROM auth_tokens t
            INNER JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = :token_hash AND t.expires_at > :now
            LIMIT 1
            """
        )

        with self.engine.connect() as conn:
            result = conn.execute(
                stmt,
                {
                    "token_hash": hash_token(token),
                    "now": self._utcnow(),
                },
            ).first()
            if not result:
                return None
            return self._serialize_user(dict(result._mapping))

    def revoke_auth_token(self, token: str) -> None:
        if not token:
            return

        stmt = text("DELETE FROM auth_tokens WHERE token_hash = :token_hash")
        with self.engine.begin() as conn:
            conn.execute(stmt, {"token_hash": hash_token(token)})

    def revoke_all_auth_tokens_for_user(self, user_id: str) -> None:
        stmt = text("DELETE FROM auth_tokens WHERE user_id = :user_id")
        with self.engine.begin() as conn:
            conn.execute(stmt, {"user_id": user_id})

    def update_user_password(
        self,
        user_id: str,
        new_password: str,
        must_change_password: bool = False,
    ) -> None:
        if len(new_password or "") < 4:
            raise ValueError("Password must be at least 4 characters")

        password_hash, password_salt = hash_password(new_password)
        stmt = text(
            """
            UPDATE users
            SET password_hash = :password_hash,
                password_salt = :password_salt,
                must_change_password = :must_change_password
            WHERE id = :user_id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(
                stmt,
                {
                    "user_id": user_id,
                    "password_hash": password_hash,
                    "password_salt": password_salt,
                    "must_change_password": 1 if must_change_password else 0,
                },
            )

    def update_user_active_status(self, user_id: str, is_active: bool) -> None:
        stmt = text(
            """
            UPDATE users
            SET is_active = :is_active
            WHERE id = :user_id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(
                stmt,
                {
                    "user_id": user_id,
                    "is_active": 1 if is_active else 0,
                },
            )

    def create_session(self, user_id: str, title: str = "New Chat") -> str:
        session_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat()

        stmt = text(
            """
            INSERT INTO sessions (id, user_id, title, created_at)
            VALUES (:id, :user_id, :title, :created_at)
            """
        )

        with self.engine.begin() as conn:
            conn.execute(
                stmt,
                {
                    "id": session_id,
                    "user_id": user_id,
                    "title": title,
                    "created_at": created_at,
                },
            )

        return session_id

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        msg_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()

        context_json = None
        if context is not None:
            try:
                context_json = json.dumps(context, ensure_ascii=False)
            except Exception:
                context_json = None

        stmt = text(
            """
            INSERT INTO messages (id, session_id, role, content, context_json, timestamp)
            SELECT :id, :session_id, :role, :content, :context_json, :timestamp
            WHERE EXISTS (
                SELECT 1
                FROM sessions
                WHERE id = :session_id
            )
            """
        )

        try:
            with self.engine.begin() as conn:
                result = conn.execute(
                    stmt,
                    {
                        "id": msg_id,
                        "session_id": session_id,
                        "role": role,
                        "content": content,
                        "context_json": context_json,
                        "timestamp": timestamp,
                    },
                )
                return result.rowcount > 0
        except IntegrityError:
            logging.getLogger(__name__).warning(
                "Skipped message insert because session no longer exists: %s",
                session_id,
            )
            return False

    def get_sessions(self, user_id: str) -> List[Dict]:
        stmt = text(
            """
            SELECT id, user_id, title, created_at
            FROM sessions
            WHERE user_id = :user_id
              AND title NOT LIKE '[System] %'
            ORDER BY created_at DESC
            """
        )

        with self.engine.connect() as conn:
            result = conn.execute(stmt, {"user_id": user_id})
            return [dict(row._mapping) for row in result]

    def list_recent_sessions(self, limit: int = 200) -> List[Dict]:
        stmt = text(
            """
            SELECT id, user_id, title, created_at
            FROM sessions
            WHERE title NOT LIKE '[System] %'
            ORDER BY created_at DESC
            LIMIT :limit
            """
        )

        with self.engine.connect() as conn:
            result = conn.execute(stmt, {"limit": limit})
            return [dict(row._mapping) for row in result]

    def rename_session(self, session_id: str, user_id: str, title: str) -> bool:
        stmt = text(
            """
            UPDATE sessions
            SET title = :title
            WHERE id = :session_id AND user_id = :user_id
            """
        )

        with self.engine.begin() as conn:
            result = conn.execute(
                stmt,
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "title": title,
                },
            )
            return result.rowcount > 0

    def delete_session(self, session_id: str, user_id: str) -> bool:
        check_owner_stmt = text(
            """
            SELECT 1
            FROM sessions
            WHERE id = :session_id AND user_id = :user_id
            LIMIT 1
            """
        )
        delete_messages_stmt = text(
            """
            DELETE FROM messages
            WHERE session_id = :session_id
            """
        )
        delete_session_stmt = text(
            """
            DELETE FROM sessions
            WHERE id = :session_id AND user_id = :user_id
            """
        )

        with self.engine.begin() as conn:
            owner_row = conn.execute(
                check_owner_stmt,
                {
                    "session_id": session_id,
                    "user_id": user_id,
                },
            ).first()
            if not owner_row:
                return False

            conn.execute(delete_messages_stmt, {"session_id": session_id})
            conn.execute(
                delete_session_stmt,
                {
                    "session_id": session_id,
                    "user_id": user_id,
                },
            )
            return True

    def session_belongs_to_user(self, session_id: str, user_id: str) -> bool:
        stmt = text(
            """
            SELECT 1
            FROM sessions
            WHERE id = :session_id AND user_id = :user_id
            LIMIT 1
            """
        )

        with self.engine.connect() as conn:
            result = conn.execute(
                stmt,
                {"session_id": session_id, "user_id": user_id},
            )
            return result.first() is not None

    def get_messages(self, session_id: str) -> List[Dict]:
        stmt = text(
            """
            SELECT id, session_id, role, content, context_json, timestamp
            FROM messages
            WHERE session_id = :session_id
            ORDER BY timestamp ASC
            """
        )

        with self.engine.connect() as conn:
            result = conn.execute(stmt, {"session_id": session_id})
            rows = [dict(row._mapping) for row in result]

        # Parse stored context_json into a `context` object for API consumers.
        for row in rows:
            ctx_raw = row.pop("context_json", None)
            if isinstance(ctx_raw, str) and ctx_raw.strip():
                try:
                    row["context"] = json.loads(ctx_raw)
                except Exception:
                    row["context"] = None
            else:
                row["context"] = None
        return rows

    def create_generated_report(
        self,
        user_id: str,
        session_id: str,
        title: str,
        summary: str,
        markdown: str,
        blocks: Optional[List[Dict[str, Any]]] = None,
        context: Optional[Dict[str, Any]] = None,
        report_type: str = "conversation_summary",
        status: str = "draft",
        approved_at: Optional[str] = None,
        approved_by: Optional[str] = None,
        read_at: Optional[str] = None,
        last_refreshed_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        report_id = str(uuid.uuid4())
        created_at = self._utcnow()
        stmt = text(
            """
            INSERT INTO generated_reports (
                id,
                user_id,
                session_id,
                report_type,
                title,
                summary,
                markdown,
                blocks_json,
                context_json,
                status,
                approved_at,
                approved_by,
                read_at,
                last_refreshed_at,
                created_at
            )
            VALUES (
                :id,
                :user_id,
                :session_id,
                :report_type,
                :title,
                :summary,
                :markdown,
                :blocks_json,
                :context_json,
                :status,
                :approved_at,
                :approved_by,
                :read_at,
                :last_refreshed_at,
                :created_at
            )
            """
        )

        with self.engine.begin() as conn:
            conn.execute(
                stmt,
                {
                    "id": report_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "report_type": report_type,
                    "title": title,
                    "summary": summary,
                    "markdown": markdown,
                    "blocks_json": json.dumps(blocks or [], ensure_ascii=False),
                    "context_json": json.dumps(context or {}, ensure_ascii=False),
                    "status": status,
                    "approved_at": approved_at,
                    "approved_by": approved_by,
                    "read_at": read_at,
                    "last_refreshed_at": last_refreshed_at,
                    "created_at": created_at,
                },
            )

        return {
            "id": report_id,
            "user_id": user_id,
            "session_id": session_id,
            "report_type": report_type,
            "title": title,
            "summary": summary,
            "markdown": markdown,
            "blocks": blocks or [],
            "context": context or {},
            "status": status,
            "approved_at": approved_at,
            "approved_by": approved_by,
            "read_at": read_at,
            "last_refreshed_at": last_refreshed_at,
            "created_at": created_at,
        }

    def list_generated_reports(self, user_id: str) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, user_id, session_id, report_type, title, summary, status, approved_at, approved_by, read_at, last_refreshed_at, created_at
            FROM generated_reports
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            """
        )

        with self.engine.connect() as conn:
            result = conn.execute(stmt, {"user_id": user_id})
            rows = [dict(row._mapping) for row in result]

        for row in rows:
            row["is_unread"] = not bool(row.get("read_at"))
        return rows

    def get_generated_report(self, report_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, user_id, session_id, report_type, title, summary, markdown, blocks_json, context_json, status, approved_at, approved_by, read_at, last_refreshed_at, created_at
            FROM generated_reports
            WHERE id = :report_id AND user_id = :user_id
            LIMIT 1
            """
        )

        with self.engine.connect() as conn:
            row = conn.execute(
                stmt,
                {
                    "report_id": report_id,
                    "user_id": user_id,
                },
            ).first()
            if not row:
                return None
            report = dict(row._mapping)
            blocks_raw = report.pop("blocks_json", None)
            ctx_raw = report.pop("context_json", None)
            if isinstance(blocks_raw, str) and blocks_raw.strip():
                try:
                    report["blocks"] = json.loads(blocks_raw)
                except Exception:
                    report["blocks"] = []
            else:
                report["blocks"] = []
            if isinstance(ctx_raw, str) and ctx_raw.strip():
                try:
                    report["context"] = json.loads(ctx_raw)
                except Exception:
                    report["context"] = None
            else:
                report["context"] = None
            return report

    def find_generated_report_by_type_and_title(
        self,
        user_id: str,
        report_type: str,
        title: str,
    ) -> Optional[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, user_id, session_id, report_type, title, summary, markdown, blocks_json, context_json, status, approved_at, approved_by, read_at, last_refreshed_at, created_at
            FROM generated_reports
            WHERE user_id = :user_id AND report_type = :report_type AND title = :title
            ORDER BY created_at DESC
            LIMIT 1
            """
        )

        with self.engine.connect() as conn:
            row = conn.execute(
                stmt,
                {
                    "user_id": user_id,
                    "report_type": report_type,
                    "title": title,
                },
            ).first()
            if not row:
                return None
            report = dict(row._mapping)
            blocks_raw = report.pop("blocks_json", None)
            ctx_raw = report.pop("context_json", None)
            if isinstance(blocks_raw, str) and blocks_raw.strip():
                try:
                    report["blocks"] = json.loads(blocks_raw)
                except Exception:
                    report["blocks"] = []
            else:
                report["blocks"] = []
            if isinstance(ctx_raw, str) and ctx_raw.strip():
                try:
                    report["context"] = json.loads(ctx_raw)
                except Exception:
                    report["context"] = None
            else:
                report["context"] = None
            return report

    def update_generated_report(
        self,
        report_id: str,
        user_id: str,
        title: str,
        summary: str,
        markdown: str,
        blocks: Optional[List[Dict[str, Any]]] = None,
        context: Optional[Dict[str, Any]] = None,
        read_at: Optional[str] = None,
        last_refreshed_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        effective_blocks = blocks
        if effective_blocks is None:
            existing = self.get_generated_report(report_id, user_id)
            if existing and isinstance(existing.get("blocks"), list):
                effective_blocks = existing.get("blocks")
            else:
                effective_blocks = []

        stmt = text(
            """
            UPDATE generated_reports
            SET title = :title,
                summary = :summary,
                markdown = :markdown,
                blocks_json = :blocks_json,
                context_json = :context_json,
                status = 'draft',
                approved_at = NULL,
                approved_by = NULL,
                read_at = :read_at,
                last_refreshed_at = :last_refreshed_at
            WHERE id = :report_id AND user_id = :user_id
            """
        )

        with self.engine.begin() as conn:
            conn.execute(
                stmt,
                {
                    "report_id": report_id,
                    "user_id": user_id,
                    "title": title,
                    "summary": summary,
                    "markdown": markdown,
                    "blocks_json": json.dumps(effective_blocks or [], ensure_ascii=False),
                    "context_json": json.dumps(context or {}, ensure_ascii=False),
                    "read_at": read_at,
                    "last_refreshed_at": last_refreshed_at,
                },
            )

        return self.get_generated_report(report_id, user_id)

    def set_generated_report_status(
        self,
        report_id: str,
        user_id: str,
        status: str,
        approved_by: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized_status = "approved" if status == "approved" else "draft"
        approved_at = self._utcnow() if normalized_status == "approved" else None
        stmt = text(
            """
            UPDATE generated_reports
            SET status = :status,
                approved_at = :approved_at,
                approved_by = :approved_by
            WHERE id = :report_id AND user_id = :user_id
            """
        )

        with self.engine.begin() as conn:
            conn.execute(
                stmt,
                {
                    "report_id": report_id,
                    "user_id": user_id,
                    "status": normalized_status,
                    "approved_at": approved_at,
                    "approved_by": approved_by if normalized_status == "approved" else None,
                },
            )

        return self.get_generated_report(report_id, user_id)

    def mark_generated_report_read(self, report_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        now = self._utcnow()
        stmt = text(
            """
            UPDATE generated_reports
            SET read_at = COALESCE(read_at, :read_at)
            WHERE id = :report_id AND user_id = :user_id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(
                stmt,
                {
                    "report_id": report_id,
                    "user_id": user_id,
                    "read_at": now,
                },
            )
        return self.get_generated_report(report_id, user_id)

    def get_unread_generated_report_count(self, user_id: str) -> int:
        stmt = text(
            """
            SELECT COUNT(*)
            FROM generated_reports
            WHERE user_id = :user_id
              AND read_at IS NULL
              AND report_type LIKE 'scheduled_%'
            """
        )
        with self.engine.connect() as conn:
            return int(conn.execute(stmt, {"user_id": user_id}).scalar() or 0)
    
    def delete_generated_report(self, report_id: str, user_id: str) -> bool:
        """Delete a generated report. Returns True if the report was deleted."""
        stmt = text(
            """
            DELETE FROM generated_reports
            WHERE id = :id AND user_id = :user_id
            """
        )
        with self.engine.begin() as conn:
            result = conn.execute(stmt, {"id": report_id, "user_id": user_id})
        return bool(result.rowcount)

    def create_report_delivery(
        self,
        report_id: str,
        sender_user_id: str,
        delivery_mode: str,
        recipient_ids: List[str],
        recipient_labels: List[str],
        message: Optional[str],
        status: str,
        provider: Optional[str],
    ) -> Dict[str, Any]:
        delivery_id = str(uuid.uuid4())
        delivered_at = self._utcnow()
        stmt = text(
            """
            INSERT INTO report_deliveries (
                id, report_id, sender_user_id, delivery_mode, recipient_ids_json, recipient_labels_json,
                message, status, provider, delivered_at
            ) VALUES (
                :id, :report_id, :sender_user_id, :delivery_mode, :recipient_ids_json, :recipient_labels_json,
                :message, :status, :provider, :delivered_at
            )
            """
        )

        payload = {
            "id": delivery_id,
            "report_id": report_id,
            "sender_user_id": sender_user_id,
            "delivery_mode": delivery_mode,
            "recipient_ids_json": json.dumps(recipient_ids, ensure_ascii=False),
            "recipient_labels_json": json.dumps(recipient_labels, ensure_ascii=False),
            "message": message,
            "status": status,
            "provider": provider,
            "delivered_at": delivered_at,
        }
        with self.engine.begin() as conn:
            conn.execute(stmt, payload)

        return {
            "id": delivery_id,
            "report_id": report_id,
            "sender_user_id": sender_user_id,
            "delivery_mode": delivery_mode,
            "recipient_ids": recipient_ids,
            "recipient_labels": recipient_labels,
            "message": message,
            "status": status,
            "provider": provider,
            "delivered_at": delivered_at,
        }

    def list_report_deliveries(self, report_id: str) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, report_id, sender_user_id, delivery_mode, recipient_ids_json, recipient_labels_json,
                   message, status, provider, delivered_at
            FROM report_deliveries
            WHERE report_id = :report_id
            ORDER BY delivered_at DESC
            """
        )

        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(stmt, {"report_id": report_id})]

        for row in rows:
            row["recipient_ids"] = json.loads(str(row.pop("recipient_ids_json") or "[]"))
            row["recipient_labels"] = json.loads(str(row.pop("recipient_labels_json") or "[]"))
        return rows

    def list_report_templates(self) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, name, description, time_period, audience, schedule_hint,
                   template_title, template_markdown, template_blocks_json,
                   sections_json, created_at, updated_at, updated_by
            FROM report_templates
            ORDER BY updated_at DESC, name ASC
            """
        )
        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(stmt)]

        for row in rows:
            row["sections"] = json.loads(str(row.pop("sections_json") or "[]"))
            row["template_blocks"] = json.loads(
                str(row.pop("template_blocks_json") or "[]")
            )
        return rows

    def get_report_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, name, description, time_period, audience, schedule_hint,
                   template_title, template_markdown, template_blocks_json,
                   sections_json, created_at, updated_at, updated_by
            FROM report_templates
            WHERE id = :template_id
            LIMIT 1
            """
        )
        with self.engine.connect() as conn:
            row = conn.execute(stmt, {"template_id": template_id}).first()
        if not row:
            return None
        payload = dict(row._mapping)
        payload["sections"] = json.loads(str(payload.pop("sections_json") or "[]"))
        payload["template_blocks"] = json.loads(
            str(payload.pop("template_blocks_json") or "[]")
        )
        return payload

    def save_report_template(
        self,
        template_id: Optional[str],
        name: str,
        description: Optional[str],
        time_period: str,
        audience: Optional[str],
        schedule_hint: Optional[str],
        sections: List[str],
        template_title: Optional[str],
        template_markdown: Optional[str],
        template_blocks: Optional[List[Dict[str, Any]]],
        updated_by: Optional[str],
    ) -> Dict[str, Any]:
        existing = self.get_report_template(str(template_id)) if template_id else None
        now = self._utcnow()
        normalized_time_period = str(time_period or "weekly").strip() or "weekly"
        payload = {
            "id": str(template_id or uuid.uuid4()),
            "name": name,
            "description": description,
            "time_period": normalized_time_period,
            "audience": audience,
            "schedule_hint": schedule_hint,
            "template_title": template_title,
            "template_markdown": template_markdown,
            "template_blocks_json": json.dumps(template_blocks or [], ensure_ascii=False),
            "sections_json": json.dumps(sections, ensure_ascii=False),
            "category": "saved_templates",
            "thumbnail": "/logo2.png",
            "estimated_time": "1 minute",
            "tags_json": json.dumps([normalized_time_period], ensure_ascii=False),
            "is_system": 0,
            "updated_at": now,
            "updated_by": updated_by,
        }

        if existing:
            stmt = text(
                """
                UPDATE report_templates
                SET name = :name,
                    description = :description,
                    time_period = :time_period,
                    audience = :audience,
                    schedule_hint = :schedule_hint,
                    template_title = :template_title,
                    template_markdown = :template_markdown,
                    template_blocks_json = :template_blocks_json,
                    sections_json = :sections_json,
                    category = :category,
                    thumbnail = :thumbnail,
                    estimated_time = :estimated_time,
                    tags_json = :tags_json,
                    is_system = :is_system,
                    updated_at = :updated_at,
                    updated_by = :updated_by
                WHERE id = :id
                """
            )
        else:
            stmt = text(
                """
                INSERT INTO report_templates (
                    id, name, description, time_period, audience, schedule_hint,
                    template_title, template_markdown, template_blocks_json,
                    sections_json, category, thumbnail, estimated_time, tags_json,
                    is_system, created_at, updated_at, updated_by
                ) VALUES (
                    :id, :name, :description, :time_period, :audience, :schedule_hint,
                    :template_title, :template_markdown, :template_blocks_json,
                    :sections_json, :category, :thumbnail, :estimated_time, :tags_json,
                    :is_system, :created_at, :updated_at, :updated_by
                )
                """
            )
            payload["created_at"] = now

        with self.engine.begin() as conn:
            conn.execute(stmt, payload)

        saved = self.get_report_template(payload["id"])
        if not saved:
            raise RuntimeError("Failed to save report template")
        return saved

    def ensure_system_report_templates(self, templates: List[Dict[str, Any]]) -> None:
        now = self._utcnow()
        select_stmt = text(
            "SELECT id FROM report_templates WHERE id = :template_id LIMIT 1"
        )
        update_stmt = text(
            """
            UPDATE report_templates
            SET name = :name,
                description = :description,
                time_period = :time_period,
                audience = :audience,
                schedule_hint = :schedule_hint,
                template_title = :template_title,
                template_markdown = :template_markdown,
                template_blocks_json = :template_blocks_json,
                sections_json = :sections_json,
                category = :category,
                thumbnail = :thumbnail,
                estimated_time = :estimated_time,
                tags_json = :tags_json,
                is_system = 1,
                updated_at = :updated_at,
                updated_by = :updated_by
            WHERE id = :id
            """
        )
        insert_stmt = text(
            """
            INSERT INTO report_templates (
                id, name, description, time_period, audience, schedule_hint,
                template_title, template_markdown, template_blocks_json,
                sections_json, category, thumbnail, estimated_time, tags_json,
                is_system, created_at, updated_at, updated_by
            ) VALUES (
                :id, :name, :description, :time_period, :audience, :schedule_hint,
                :template_title, :template_markdown, :template_blocks_json,
                :sections_json, :category, :thumbnail, :estimated_time, :tags_json,
                :is_system, :created_at, :updated_at, :updated_by
            )
            """
        )

        def normalize_seed_blocks(seed_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            normalized = []
            for index, block in enumerate(seed_blocks or []):
                if not isinstance(block, dict):
                    continue
                normalized.append(
                    {
                        "id": str(block.get("id") or f"system_block_{index}"),
                        "order": index,
                        "type": str(block.get("type") or "text"),
                        "title": str(block.get("title") or ""),
                        "definition": dict(block.get("config") or {}),
                        "rendered": {},
                    }
                )
            return normalized

        with self.engine.begin() as conn:
            for template in templates or []:
                template_id = str(template.get("id") or "").strip()
                if not template_id:
                    continue
                sections = [
                    str(block.get("title") or "").strip()
                    for block in (template.get("blocks") or [])
                    if isinstance(block, dict) and str(block.get("title") or "").strip()
                ]
                payload = {
                    "id": template_id,
                    "name": str(template.get("name") or "").strip() or template_id,
                    "description": str(template.get("description") or "").strip() or None,
                    "time_period": str((template.get("tags") or ["weekly"])[0] or "weekly"),
                    "audience": None,
                    "schedule_hint": None,
                    "template_title": str(template.get("name") or "").strip() or None,
                    "template_markdown": None,
                    "template_blocks_json": json.dumps(
                        normalize_seed_blocks(template.get("blocks") or []),
                        ensure_ascii=False,
                    ),
                    "sections_json": json.dumps(sections, ensure_ascii=False),
                    "category": str(template.get("category") or "general").strip() or "general",
                    "thumbnail": str(template.get("thumbnail") or "").strip() or None,
                    "estimated_time": str(template.get("estimated_time") or "1 minute").strip() or "1 minute",
                    "tags_json": json.dumps(template.get("tags") or [], ensure_ascii=False),
                    "is_system": 1,
                    "created_at": now,
                    "updated_at": now,
                    "updated_by": "system",
                }
                exists = conn.execute(select_stmt, {"template_id": template_id}).first()
                if exists:
                    conn.execute(update_stmt, payload)
                else:
                    conn.execute(insert_stmt, payload)

    def list_report_gallery_templates(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, name, description, time_period, audience, schedule_hint,
                   template_title, template_markdown, template_blocks_json,
                   sections_json, category, thumbnail, estimated_time, tags_json,
                   is_system, created_at, updated_at, updated_by
            FROM report_templates
            ORDER BY is_system DESC, updated_at DESC, name ASC
            """
        )
        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(stmt)]

        hidden_ids = set(self.get_hidden_gallery_templates(user_id)) if user_id else set()
        pinned_ids = set(self.get_pinned_report_templates(user_id)) if user_id else set()
        templates: List[Dict[str, Any]] = []
        for row in rows:
            row["is_system"] = bool(int(row.get("is_system") or 0))
            if row["is_system"] and str(row.get("id") or "") in hidden_ids:
                continue
            row["sections"] = json.loads(str(row.pop("sections_json") or "[]"))
            row["template_blocks"] = json.loads(
                str(row.pop("template_blocks_json") or "[]")
            )
            row["tags"] = json.loads(str(row.pop("tags_json") or "[]"))
            row["is_pinned"] = str(row.get("id") or "") in pinned_ids
            templates.append(row)
        return templates

    def delete_report_template(self, template_id: str) -> bool:
        stmt = text("DELETE FROM report_templates WHERE id = :template_id")
        with self.engine.begin() as conn:
            result = conn.execute(stmt, {"template_id": template_id})
        return bool(result.rowcount)

    def list_report_categories(self, org_id: str) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, org_id, parent_id, name, icon, position, permissions_json,
                   created_at, updated_at, created_by
            FROM report_categories
            WHERE org_id = :org_id
            ORDER BY position ASC, name ASC
            """
        )
        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(stmt, {"org_id": org_id})]

        for row in rows:
            row["permissions"] = json.loads(str(row.pop("permissions_json") or "{}"))
        return rows

    def save_report_category(
        self,
        category_id: Optional[str],
        org_id: str,
        name: str,
        parent_id: Optional[str],
        icon: Optional[str],
        position: int,
        permissions: Optional[Dict[str, Any]],
        created_by: Optional[str],
    ) -> Dict[str, Any]:
        now = self._utcnow()
        payload = {
            "id": str(category_id or uuid.uuid4()),
            "org_id": org_id,
            "parent_id": parent_id,
            "name": name,
            "icon": icon,
            "position": int(position),
            "permissions_json": json.dumps(permissions or {}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now,
            "created_by": created_by,
        }

        exists_stmt = text(
            "SELECT id FROM report_categories WHERE id = :id AND org_id = :org_id LIMIT 1"
        )
        with self.engine.connect() as conn:
            exists = conn.execute(
                exists_stmt, {"id": payload["id"], "org_id": org_id}
            ).first()

        if exists:
            stmt = text(
                """
                UPDATE report_categories
                SET parent_id = :parent_id,
                    name = :name,
                    icon = :icon,
                    position = :position,
                    permissions_json = :permissions_json,
                    updated_at = :updated_at
                WHERE id = :id AND org_id = :org_id
                """
            )
        else:
            stmt = text(
                """
                INSERT INTO report_categories (
                    id, org_id, parent_id, name, icon, position, permissions_json,
                    created_at, updated_at, created_by
                ) VALUES (
                    :id, :org_id, :parent_id, :name, :icon, :position, :permissions_json,
                    :created_at, :updated_at, :created_by
                )
                """
            )

        with self.engine.begin() as conn:
            conn.execute(stmt, payload)

        rows = self.list_report_categories(org_id)
        for item in rows:
            if str(item.get("id")) == payload["id"]:
                return item
        raise RuntimeError("Failed to save report category")

    def delete_report_category(self, category_id: str, org_id: str) -> bool:
        stmt = text(
            "DELETE FROM report_categories WHERE id = :id AND org_id = :org_id"
        )
        with self.engine.begin() as conn:
            result = conn.execute(stmt, {"id": category_id, "org_id": org_id})
        return bool(result.rowcount)

    def list_cockpit_definitions(self, org_id: str) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, name, org_id, department_id, layout_json, status, version,
                   is_default, permissions_json, created_by, created_at, updated_at, published_at
            FROM cockpit_definitions
            WHERE org_id = :org_id
            ORDER BY updated_at DESC
            """
        )
        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(stmt, {"org_id": org_id})]

        for row in rows:
            row["layout"] = json.loads(str(row.pop("layout_json") or "{}"))
            row["permissions"] = json.loads(str(row.pop("permissions_json") or "{}"))
            row["is_default"] = bool(int(row.get("is_default") or 0))
        return rows

    def get_cockpit_definition(self, cockpit_id: str, org_id: str) -> Optional[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, name, org_id, department_id, layout_json, status, version,
                   is_default, permissions_json, created_by, created_at, updated_at, published_at
            FROM cockpit_definitions
            WHERE id = :id AND org_id = :org_id
            LIMIT 1
            """
        )
        with self.engine.connect() as conn:
            row = conn.execute(stmt, {"id": cockpit_id, "org_id": org_id}).first()
        if not row:
            return None

        payload = dict(row._mapping)
        payload["layout"] = json.loads(str(payload.pop("layout_json") or "{}"))
        payload["permissions"] = json.loads(str(payload.pop("permissions_json") or "{}"))
        payload["is_default"] = bool(int(payload.get("is_default") or 0))
        return payload

    def save_cockpit_definition(
        self,
        cockpit_id: Optional[str],
        org_id: str,
        name: str,
        department_id: Optional[str],
        layout: Dict[str, Any],
        status: str,
        version: int,
        is_default: bool,
        permissions: Optional[Dict[str, Any]],
        created_by: Optional[str],
    ) -> Dict[str, Any]:
        now = self._utcnow()
        payload = {
            "id": str(cockpit_id or uuid.uuid4()),
            "name": name,
            "org_id": org_id,
            "department_id": department_id,
            "layout_json": json.dumps(layout or {}, ensure_ascii=False),
            "status": status,
            "version": int(version),
            "is_default": int(bool(is_default)),
            "permissions_json": json.dumps(permissions or {}, ensure_ascii=False),
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
            "published_at": now if status == "published" else None,
        }

        existing = self.get_cockpit_definition(payload["id"], org_id)
        if existing:
            stmt = text(
                """
                UPDATE cockpit_definitions
                SET name = :name,
                    department_id = :department_id,
                    layout_json = :layout_json,
                    status = :status,
                    version = :version,
                    is_default = :is_default,
                    permissions_json = :permissions_json,
                    updated_at = :updated_at,
                    published_at = :published_at
                WHERE id = :id AND org_id = :org_id
                """
            )
        else:
            stmt = text(
                """
                INSERT INTO cockpit_definitions (
                    id, name, org_id, department_id, layout_json, status, version,
                    is_default, permissions_json, created_by, created_at, updated_at, published_at
                ) VALUES (
                    :id, :name, :org_id, :department_id, :layout_json, :status, :version,
                    :is_default, :permissions_json, :created_by, :created_at, :updated_at, :published_at
                )
                """
            )

        with self.engine.begin() as conn:
            conn.execute(stmt, payload)

        saved = self.get_cockpit_definition(payload["id"], org_id)
        if not saved:
            raise RuntimeError("Failed to save cockpit definition")
        return saved

    def delete_cockpit_definition(self, cockpit_id: str, org_id: str) -> bool:
        stmt = text(
            "DELETE FROM cockpit_definitions WHERE id = :id AND org_id = :org_id"
        )
        with self.engine.begin() as conn:
            result = conn.execute(stmt, {"id": cockpit_id, "org_id": org_id})
        return bool(result.rowcount)

    def list_alert_rules(self, org_id: str) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, name, org_id, created_by, metric_expression, condition_type,
                   condition_params_json, severity, category, notification_channels_json,
                   default_owner_id, enabled, evaluation_interval_min, created_at, updated_at
            FROM alert_rules
            WHERE org_id = :org_id
            ORDER BY updated_at DESC
            """
        )
        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(stmt, {"org_id": org_id})]

        for row in rows:
            row["condition_params"] = json.loads(str(row.pop("condition_params_json") or "{}"))
            row["notification_channels"] = json.loads(
                str(row.pop("notification_channels_json") or "[]")
            )
            row["enabled"] = bool(int(row.get("enabled") or 0))
        return rows

    def save_alert_rule(
        self,
        rule_id: Optional[str],
        org_id: str,
        name: str,
        metric_expression: str,
        condition_type: str,
        condition_params: Dict[str, Any],
        severity: str,
        category: str,
        notification_channels: List[Dict[str, Any]],
        default_owner_id: Optional[str],
        enabled: bool,
        evaluation_interval_min: int,
        created_by: Optional[str],
    ) -> Dict[str, Any]:
        now = self._utcnow()
        payload = {
            "id": str(rule_id or uuid.uuid4()),
            "name": name,
            "org_id": org_id,
            "created_by": created_by,
            "metric_expression": metric_expression,
            "condition_type": condition_type,
            "condition_params_json": json.dumps(condition_params or {}, ensure_ascii=False),
            "severity": severity,
            "category": category,
            "notification_channels_json": json.dumps(notification_channels or [], ensure_ascii=False),
            "default_owner_id": default_owner_id,
            "enabled": int(bool(enabled)),
            "evaluation_interval_min": max(1, int(evaluation_interval_min)),
            "created_at": now,
            "updated_at": now,
        }

        exists_stmt = text("SELECT id FROM alert_rules WHERE id = :id AND org_id = :org_id LIMIT 1")
        with self.engine.connect() as conn:
            exists = conn.execute(
                exists_stmt, {"id": payload["id"], "org_id": org_id}
            ).first()

        if exists:
            stmt = text(
                """
                UPDATE alert_rules
                SET name = :name,
                    metric_expression = :metric_expression,
                    condition_type = :condition_type,
                    condition_params_json = :condition_params_json,
                    severity = :severity,
                    category = :category,
                    notification_channels_json = :notification_channels_json,
                    default_owner_id = :default_owner_id,
                    enabled = :enabled,
                    evaluation_interval_min = :evaluation_interval_min,
                    updated_at = :updated_at
                WHERE id = :id AND org_id = :org_id
                """
            )
        else:
            stmt = text(
                """
                INSERT INTO alert_rules (
                    id, name, org_id, created_by, metric_expression, condition_type,
                    condition_params_json, severity, category, notification_channels_json,
                    default_owner_id, enabled, evaluation_interval_min, created_at, updated_at
                ) VALUES (
                    :id, :name, :org_id, :created_by, :metric_expression, :condition_type,
                    :condition_params_json, :severity, :category, :notification_channels_json,
                    :default_owner_id, :enabled, :evaluation_interval_min, :created_at, :updated_at
                )
                """
            )

        with self.engine.begin() as conn:
            conn.execute(stmt, payload)

        for item in self.list_alert_rules(org_id):
            if str(item.get("id")) == payload["id"]:
                return item
        raise RuntimeError("Failed to save alert rule")

    def delete_alert_rule(self, rule_id: str, org_id: str) -> bool:
        stmt = text("DELETE FROM alert_rules WHERE id = :id AND org_id = :org_id")
        with self.engine.begin() as conn:
            result = conn.execute(stmt, {"id": rule_id, "org_id": org_id})
        return bool(result.rowcount)

    def list_data_sources(self, org_id: str) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, org_id, name, connector_type, connection_config_json, status,
                   last_tested_at, created_by, created_at, updated_at
            FROM data_sources
            WHERE org_id = :org_id
            ORDER BY updated_at DESC
            """
        )
        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(stmt, {"org_id": org_id})]

        for row in rows:
            row["connection_config"] = json.loads(
                str(row.pop("connection_config_json") or "{}")
            )
        return rows

    def get_data_source(self, source_id: str, org_id: str) -> Optional[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, org_id, name, connector_type, connection_config_json, status,
                   last_tested_at, created_by, created_at, updated_at
            FROM data_sources
            WHERE id = :id AND org_id = :org_id
            LIMIT 1
            """
        )
        with self.engine.connect() as conn:
            row = conn.execute(stmt, {"id": source_id, "org_id": org_id}).first()
        if not row:
            return None
        payload = dict(row._mapping)
        payload["connection_config"] = json.loads(
            str(payload.pop("connection_config_json") or "{}")
        )
        return payload

    def save_data_source(
        self,
        source_id: Optional[str],
        org_id: str,
        name: str,
        connector_type: str,
        connection_config: Dict[str, Any],
        status: str,
        last_tested_at: Optional[str],
        created_by: Optional[str],
    ) -> Dict[str, Any]:
        now = self._utcnow()
        payload = {
            "id": str(source_id or uuid.uuid4()),
            "org_id": org_id,
            "name": name,
            "connector_type": connector_type,
            "connection_config_json": json.dumps(connection_config or {}, ensure_ascii=False),
            "status": status,
            "last_tested_at": last_tested_at,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }

        existing = self.get_data_source(payload["id"], org_id)
        if existing:
            stmt = text(
                """
                UPDATE data_sources
                SET name = :name,
                    connector_type = :connector_type,
                    connection_config_json = :connection_config_json,
                    status = :status,
                    last_tested_at = :last_tested_at,
                    updated_at = :updated_at
                WHERE id = :id AND org_id = :org_id
                """
            )
        else:
            stmt = text(
                """
                INSERT INTO data_sources (
                    id, org_id, name, connector_type, connection_config_json, status,
                    last_tested_at, created_by, created_at, updated_at
                ) VALUES (
                    :id, :org_id, :name, :connector_type, :connection_config_json, :status,
                    :last_tested_at, :created_by, :created_at, :updated_at
                )
                """
            )

        with self.engine.begin() as conn:
            conn.execute(stmt, payload)

        saved = self.get_data_source(payload["id"], org_id)
        if not saved:
            raise RuntimeError("Failed to save data source")
        return saved

    def delete_data_source(self, source_id: str, org_id: str) -> bool:
        delete_datasets_stmt = text(
            "DELETE FROM datasets WHERE source_id = :source_id AND org_id = :org_id"
        )
        stmt = text("DELETE FROM data_sources WHERE id = :id AND org_id = :org_id")
        with self.engine.begin() as conn:
            conn.execute(delete_datasets_stmt, {"source_id": source_id, "org_id": org_id})
            result = conn.execute(stmt, {"id": source_id, "org_id": org_id})
        return bool(result.rowcount)

    def list_datasets(self, org_id: str, source_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if source_id:
            stmt = text(
                """
                SELECT id, source_id, org_id, friendly_name, physical_reference, description,
                       tags_json, schema_snapshot_json, status, created_at, updated_at
                FROM datasets
                WHERE org_id = :org_id AND source_id = :source_id
                ORDER BY updated_at DESC
                """
            )
            params = {"org_id": org_id, "source_id": source_id}
        else:
            stmt = text(
                """
                SELECT id, source_id, org_id, friendly_name, physical_reference, description,
                       tags_json, schema_snapshot_json, status, created_at, updated_at
                FROM datasets
                WHERE org_id = :org_id
                ORDER BY updated_at DESC
                """
            )
            params = {"org_id": org_id}

        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(stmt, params)]

        for row in rows:
            row["tags"] = json.loads(str(row.pop("tags_json") or "[]"))
            row["schema_snapshot"] = json.loads(
                str(row.pop("schema_snapshot_json") or "{}")
            )
        return rows

    def get_dataset(self, dataset_id: str, org_id: str) -> Optional[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, source_id, org_id, friendly_name, physical_reference, description,
                   tags_json, schema_snapshot_json, status, created_at, updated_at
            FROM datasets
            WHERE id = :id AND org_id = :org_id
            LIMIT 1
            """
        )
        with self.engine.connect() as conn:
            row = conn.execute(stmt, {"id": dataset_id, "org_id": org_id}).first()
        if not row:
            return None
        payload = dict(row._mapping)
        payload["tags"] = json.loads(str(payload.pop("tags_json") or "[]"))
        payload["schema_snapshot"] = json.loads(
            str(payload.pop("schema_snapshot_json") or "{}")
        )
        return payload

    def save_dataset(
        self,
        dataset_id: Optional[str],
        org_id: str,
        source_id: str,
        friendly_name: str,
        physical_reference: str,
        description: Optional[str],
        tags: List[str],
        schema_snapshot: Dict[str, Any],
        status: str,
    ) -> Dict[str, Any]:
        now = self._utcnow()
        payload = {
            "id": str(dataset_id or uuid.uuid4()),
            "source_id": source_id,
            "org_id": org_id,
            "friendly_name": friendly_name,
            "physical_reference": physical_reference,
            "description": description,
            "tags_json": json.dumps(tags or [], ensure_ascii=False),
            "schema_snapshot_json": json.dumps(schema_snapshot or {}, ensure_ascii=False),
            "status": status,
            "created_at": now,
            "updated_at": now,
        }

        existing = self.get_dataset(payload["id"], org_id)
        if existing:
            stmt = text(
                """
                UPDATE datasets
                SET source_id = :source_id,
                    friendly_name = :friendly_name,
                    physical_reference = :physical_reference,
                    description = :description,
                    tags_json = :tags_json,
                    schema_snapshot_json = :schema_snapshot_json,
                    status = :status,
                    updated_at = :updated_at
                WHERE id = :id AND org_id = :org_id
                """
            )
        else:
            stmt = text(
                """
                INSERT INTO datasets (
                    id, source_id, org_id, friendly_name, physical_reference, description,
                    tags_json, schema_snapshot_json, status, created_at, updated_at
                ) VALUES (
                    :id, :source_id, :org_id, :friendly_name, :physical_reference, :description,
                    :tags_json, :schema_snapshot_json, :status, :created_at, :updated_at
                )
                """
            )

        with self.engine.begin() as conn:
            conn.execute(stmt, payload)

        saved = self.get_dataset(payload["id"], org_id)
        if not saved:
            raise RuntimeError("Failed to save dataset")
        return saved

    def delete_dataset(self, dataset_id: str, org_id: str) -> bool:
        delete_mappings = text("DELETE FROM dataset_column_mappings WHERE dataset_id = :dataset_id")
        delete_permissions = text("DELETE FROM dataset_permissions WHERE dataset_id = :dataset_id")
        stmt = text("DELETE FROM datasets WHERE id = :id AND org_id = :org_id")
        with self.engine.begin() as conn:
            conn.execute(delete_mappings, {"dataset_id": dataset_id})
            conn.execute(delete_permissions, {"dataset_id": dataset_id})
            result = conn.execute(stmt, {"id": dataset_id, "org_id": org_id})
        return bool(result.rowcount)

    def list_dataset_column_mappings(self, dataset_id: str) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT dataset_id, physical_column, ontology_concept, transform
            FROM dataset_column_mappings
            WHERE dataset_id = :dataset_id
            ORDER BY physical_column ASC
            """
        )
        with self.engine.connect() as conn:
            return [dict(row._mapping) for row in conn.execute(stmt, {"dataset_id": dataset_id})]

    def replace_dataset_column_mappings(
        self, dataset_id: str, mappings: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        delete_stmt = text("DELETE FROM dataset_column_mappings WHERE dataset_id = :dataset_id")
        insert_stmt = text(
            """
            INSERT INTO dataset_column_mappings (
                dataset_id, physical_column, ontology_concept, transform
            ) VALUES (
                :dataset_id, :physical_column, :ontology_concept, :transform
            )
            """
        )

        with self.engine.begin() as conn:
            conn.execute(delete_stmt, {"dataset_id": dataset_id})
            for item in mappings:
                conn.execute(
                    insert_stmt,
                    {
                        "dataset_id": dataset_id,
                        "physical_column": str(item.get("physical_column") or "").strip(),
                        "ontology_concept": str(item.get("ontology_concept") or "").strip(),
                        "transform": (
                            str(item.get("transform")).strip()
                            if item.get("transform") is not None
                            else None
                        ),
                    },
                )
        return self.list_dataset_column_mappings(dataset_id)

    def list_dataset_permissions(self, dataset_id: str) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT dataset_id, role_id, can_query, can_export, can_chat
            FROM dataset_permissions
            WHERE dataset_id = :dataset_id
            ORDER BY role_id ASC
            """
        )
        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(stmt, {"dataset_id": dataset_id})]

        for row in rows:
            row["can_query"] = bool(int(row.get("can_query") or 0))
            row["can_export"] = bool(int(row.get("can_export") or 0))
            row["can_chat"] = bool(int(row.get("can_chat") or 0))
        return rows

    def replace_dataset_permissions(
        self, dataset_id: str, permissions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        delete_stmt = text("DELETE FROM dataset_permissions WHERE dataset_id = :dataset_id")
        insert_stmt = text(
            """
            INSERT INTO dataset_permissions (
                dataset_id, role_id, can_query, can_export, can_chat
            ) VALUES (
                :dataset_id, :role_id, :can_query, :can_export, :can_chat
            )
            """
        )

        with self.engine.begin() as conn:
            conn.execute(delete_stmt, {"dataset_id": dataset_id})
            for item in permissions:
                conn.execute(
                    insert_stmt,
                    {
                        "dataset_id": dataset_id,
                        "role_id": str(item.get("role_id") or "").strip(),
                        "can_query": int(bool(item.get("can_query"))),
                        "can_export": int(bool(item.get("can_export"))),
                        "can_chat": int(bool(item.get("can_chat"))),
                    },
                )
        return self.list_dataset_permissions(dataset_id)

    # ── Platform Products ─────────────────────────────────────────────────────

    def _deserialize_platform_product(self, row: Dict[str, Any]) -> Dict[str, Any]:
        row["aliases"] = json.loads(str(row.pop("aliases_json") or "[]"))
        row["display_in"] = json.loads(str(row.pop("display_in_json") or "[]"))
        return row

    def list_platform_products(self, org_id: str) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, org_id, name_en, name_cn, product_type, unit, description,
                   aliases_json, data_table, data_key, display_in_json,
                   created_by, created_at, updated_at
            FROM platform_products
            WHERE org_id = :org_id
            ORDER BY updated_at DESC
            """
        )
        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(stmt, {"org_id": org_id})]
        return [self._deserialize_platform_product(row) for row in rows]

    def list_all_platform_products(self) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, org_id, name_en, name_cn, product_type, unit, description,
                   aliases_json, data_table, data_key, display_in_json,
                   created_by, created_at, updated_at
            FROM platform_products
            ORDER BY org_id ASC, updated_at DESC
            """
        )
        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(stmt)]
        return [self._deserialize_platform_product(row) for row in rows]

    def get_platform_product(self, product_id: str, org_id: str) -> Optional[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, org_id, name_en, name_cn, product_type, unit, description,
                   aliases_json, data_table, data_key, display_in_json,
                   created_by, created_at, updated_at
            FROM platform_products
            WHERE id = :id AND org_id = :org_id
            LIMIT 1
            """
        )
        with self.engine.connect() as conn:
            row = conn.execute(stmt, {"id": product_id, "org_id": org_id}).first()
        if not row:
            return None
        return self._deserialize_platform_product(dict(row._mapping))

    def save_platform_product(
        self,
        product_id: Optional[str],
        org_id: str,
        name_en: str,
        name_cn: str,
        product_type: str,
        unit: str,
        description: Optional[str],
        aliases: List[str],
        data_table: Optional[str],
        data_key: Optional[str],
        display_in: List[str],
        created_by: Optional[str],
    ) -> Dict[str, Any]:
        now = self._utcnow()
        pid = str(product_id or uuid.uuid4())
        payload = {
            "id": pid,
            "org_id": org_id,
            "name_en": name_en,
            "name_cn": name_cn,
            "product_type": product_type,
            "unit": unit,
            "description": description,
            "aliases_json": json.dumps(aliases or [], ensure_ascii=False),
            "data_table": data_table or None,
            "data_key": data_key or None,
            "display_in_json": json.dumps(display_in or [], ensure_ascii=False),
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }

        existing = self.get_platform_product(pid, org_id)
        if existing:
            stmt = text(
                """
                UPDATE platform_products
                SET name_en = :name_en,
                    name_cn = :name_cn,
                    product_type = :product_type,
                    unit = :unit,
                    description = :description,
                    aliases_json = :aliases_json,
                    data_table = :data_table,
                    data_key = :data_key,
                    display_in_json = :display_in_json,
                    updated_at = :updated_at
                WHERE id = :id AND org_id = :org_id
                """
            )
        else:
            stmt = text(
                """
                INSERT INTO platform_products (
                    id, org_id, name_en, name_cn, product_type, unit, description,
                    aliases_json, data_table, data_key, display_in_json,
                    created_by, created_at, updated_at
                ) VALUES (
                    :id, :org_id, :name_en, :name_cn, :product_type, :unit, :description,
                    :aliases_json, :data_table, :data_key, :display_in_json,
                    :created_by, :created_at, :updated_at
                )
                """
            )

        with self.engine.begin() as conn:
            conn.execute(stmt, payload)

        saved = self.get_platform_product(pid, org_id)
        if not saved:
            raise RuntimeError("Failed to save platform product")
        return saved

    def delete_platform_product(self, product_id: str, org_id: str) -> bool:
        stmt = text("DELETE FROM platform_products WHERE id = :id AND org_id = :org_id")
        with self.engine.begin() as conn:
            result = conn.execute(stmt, {"id": product_id, "org_id": org_id})
        return bool(result.rowcount)

    def get_or_create_system_session(self, user_id: str, title: str) -> str:
        select_stmt = text(
            """
            SELECT id
            FROM sessions
            WHERE user_id = :user_id AND title = :title
            LIMIT 1
            """
        )

        with self.engine.connect() as conn:
            row = conn.execute(
                select_stmt,
                {
                    "user_id": user_id,
                    "title": title,
                },
            ).first()
            if row:
                return str(row[0])

        return self.create_session(user_id, title)

    def get_report_schedule(self, user_id: str, report_type: str) -> Dict[str, Any]:
        stmt = text(
            """
            SELECT id, user_id, report_type, enabled, weekday, hour, minute, timezone, last_run_at, created_at, updated_at
            FROM report_schedules
            WHERE user_id = :user_id AND report_type = :report_type
            LIMIT 1
            """
        )

        with self.engine.connect() as conn:
            row = conn.execute(stmt, {"user_id": user_id, "report_type": report_type}).first()

        if not row:
            return {
                "id": "",
                "user_id": user_id,
                "report_type": report_type,
                "enabled": False,
                "weekday": 0,
                "hour": 8,
                "minute": 0,
                "timezone": "Asia/Shanghai",
                "last_run_at": None,
                "created_at": None,
                "updated_at": None,
            }

        payload = dict(row._mapping)
        payload["enabled"] = bool(int(payload.get("enabled") or 0))
        return payload

    def list_report_schedules(self, user_id: str) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, user_id, report_type, enabled, weekday, hour, minute, timezone, last_run_at, created_at, updated_at
            FROM report_schedules
            WHERE user_id = :user_id
            ORDER BY report_type ASC
            """
        )

        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(stmt, {"user_id": user_id})]

        for row in rows:
            row["enabled"] = bool(int(row.get("enabled") or 0))
        return rows

    def upsert_report_schedule(
        self,
        user_id: str,
        report_type: str,
        enabled: bool,
        hour: int,
        minute: int,
        weekday: int = 0,
        timezone: str = "Asia/Shanghai",
    ) -> Dict[str, Any]:
        existing = self.get_report_schedule(user_id, report_type)
        schedule_id = str(existing.get("id") or uuid.uuid4())
        now = self._utcnow()

        if existing.get("id"):
            stmt = text(
                """
                UPDATE report_schedules
                SET enabled = :enabled,
                    weekday = :weekday,
                    hour = :hour,
                    minute = :minute,
                    timezone = :timezone,
                    updated_at = :updated_at
                WHERE id = :id
                """
            )
            with self.engine.begin() as conn:
                conn.execute(
                    stmt,
                    {
                        "id": schedule_id,
                        "enabled": int(bool(enabled)),
                        "weekday": weekday,
                        "hour": hour,
                        "minute": minute,
                        "timezone": timezone,
                        "updated_at": now,
                    },
                )
        else:
            stmt = text(
                """
                INSERT INTO report_schedules (
                    id, user_id, report_type, enabled, weekday, hour, minute, timezone, last_run_at, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :report_type, :enabled, :weekday, :hour, :minute, :timezone, NULL, :created_at, :updated_at
                )
                """
            )
            with self.engine.begin() as conn:
                conn.execute(
                    stmt,
                    {
                        "id": schedule_id,
                        "user_id": user_id,
                        "report_type": report_type,
                        "enabled": int(bool(enabled)),
                        "weekday": weekday,
                        "hour": hour,
                        "minute": minute,
                        "timezone": timezone,
                        "created_at": now,
                        "updated_at": now,
                    },
                )

        return self.get_report_schedule(user_id, report_type)

    def list_enabled_report_schedules(self) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT id, user_id, report_type, enabled, weekday, hour, minute, timezone, last_run_at, created_at, updated_at
            FROM report_schedules
            WHERE enabled = 1
            """
        )

        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(stmt)]

        for row in rows:
            row["enabled"] = bool(int(row.get("enabled") or 0))
        return rows

    def mark_weekly_report_schedule_run(self, schedule_id: str, run_at: str) -> None:
        stmt = text(
            """
            UPDATE report_schedules
            SET last_run_at = :last_run_at,
                updated_at = :updated_at
            WHERE id = :id
            """
        )

        with self.engine.begin() as conn:
            conn.execute(
                stmt,
                {
                    "id": schedule_id,
                    "last_run_at": run_at,
                    "updated_at": run_at,
                },
            )

    def delete_schedules_by_report_type(self, report_type: str) -> int:
        """Delete all schedules for a given report_type. Returns number of deleted schedules."""
        stmt = text("DELETE FROM report_schedules WHERE report_type = :report_type")
        with self.engine.begin() as conn:
            result = conn.execute(stmt, {"report_type": report_type})
        return result.rowcount

    def hide_gallery_template(self, user_id: str, template_id: str) -> None:
        """Hide a gallery template for a specific user."""
        from datetime import datetime
        # Use DELETE + INSERT for cross-database compatibility (works in both SQLite and MySQL)
        delete_stmt = text(
            "DELETE FROM hidden_gallery_templates WHERE user_id = :user_id AND template_id = :template_id"
        )
        insert_stmt = text(
            """
            INSERT INTO hidden_gallery_templates (user_id, template_id, hidden_at)
            VALUES (:user_id, :template_id, :hidden_at)
            """
        )
        with self.engine.begin() as conn:
            # First delete if exists
            conn.execute(delete_stmt, {"user_id": user_id, "template_id": template_id})
            # Then insert fresh
            conn.execute(
                insert_stmt,
                {
                    "user_id": user_id,
                    "template_id": template_id,
                    "hidden_at": datetime.utcnow().isoformat(),
                },
            )

    def unhide_gallery_template(self, user_id: str, template_id: str) -> bool:
        """Unhide a gallery template for a specific user. Returns True if template was hidden."""
        stmt = text(
            "DELETE FROM hidden_gallery_templates WHERE user_id = :user_id AND template_id = :template_id"
        )
        with self.engine.begin() as conn:
            result = conn.execute(stmt, {"user_id": user_id, "template_id": template_id})
        return result.rowcount > 0

    def get_hidden_gallery_templates(self, user_id: str) -> List[str]:
        """Get list of hidden gallery template IDs for a specific user."""
        stmt = text(
            "SELECT template_id FROM hidden_gallery_templates WHERE user_id = :user_id"
        )
        with self.engine.connect() as conn:
            rows = conn.execute(stmt, {"user_id": user_id})
            return [row[0] for row in rows]

    def pin_report_template(self, user_id: str, template_id: str) -> None:
        delete_stmt = text(
            "DELETE FROM pinned_report_templates WHERE user_id = :user_id AND template_id = :template_id"
        )
        insert_stmt = text(
            """
            INSERT INTO pinned_report_templates (user_id, template_id, pinned_at)
            VALUES (:user_id, :template_id, :pinned_at)
            """
        )
        with self.engine.begin() as conn:
            conn.execute(delete_stmt, {"user_id": user_id, "template_id": template_id})
            conn.execute(
                insert_stmt,
                {
                    "user_id": user_id,
                    "template_id": template_id,
                    "pinned_at": self._utcnow(),
                },
            )

    def unpin_report_template(self, user_id: str, template_id: str) -> bool:
        stmt = text(
            "DELETE FROM pinned_report_templates WHERE user_id = :user_id AND template_id = :template_id"
        )
        with self.engine.begin() as conn:
            result = conn.execute(stmt, {"user_id": user_id, "template_id": template_id})
        return result.rowcount > 0

    def get_pinned_report_templates(self, user_id: str) -> List[str]:
        stmt = text(
            "SELECT template_id FROM pinned_report_templates WHERE user_id = :user_id"
        )
        with self.engine.connect() as conn:
            rows = conn.execute(stmt, {"user_id": user_id})
            return [row[0] for row in rows]

    def save_report_image_asset(
        self,
        user_id: str,
        original_filename: str,
        mime_type: str,
        file_size: int,
        file_path: str,
        public_url: str,
    ) -> Dict[str, Any]:
        asset_id = str(uuid.uuid4())
        payload = {
            "id": asset_id,
            "user_id": user_id,
            "original_filename": original_filename,
            "mime_type": mime_type,
            "file_size": int(file_size),
            "file_path": file_path,
            "public_url": public_url,
            "created_at": self._utcnow(),
        }
        stmt = text(
            """
            INSERT INTO report_image_assets (
                id, user_id, original_filename, mime_type, file_size,
                file_path, public_url, created_at
            ) VALUES (
                :id, :user_id, :original_filename, :mime_type, :file_size,
                :file_path, :public_url, :created_at
            )
            """
        )
        with self.engine.begin() as conn:
            conn.execute(stmt, payload)
        return payload

    # ------------------------------------------------------------------
    # Weekly Report helpers
    # ------------------------------------------------------------------

    def list_report_products(self) -> List[Dict[str, Any]]:
        """Return all in-scope products from the catalog, ordered by sort_order."""
        stmt = text(
            """
            SELECT slug, name_zh, name_en, category, in_scope, lz_view, erp_sales_view, report_profile_json, forecast_key, sort_order
            FROM report_product_catalog
            WHERE in_scope = 1
            ORDER BY sort_order ASC
            """
        )
        with self.engine.connect() as conn:
            result = conn.execute(stmt)
            rows = [dict(row._mapping) for row in result]
        for row in rows:
            raw_profile = row.get("report_profile_json")
            if isinstance(raw_profile, str) and raw_profile.strip():
                try:
                    row["report_profile"] = json.loads(raw_profile)
                except Exception:
                    row["report_profile"] = {}
            else:
                row["report_profile"] = {}
        return rows

    def list_weekly_reports(self, user_id: str) -> List[Dict[str, Any]]:
        """Return all weekly reports for the user, newest first."""
        stmt = text(
            """
            SELECT id, user_id, session_id, report_type, title, summary, markdown, status,
                   approved_at, approved_by, read_at, last_refreshed_at, created_at,
                   context_json
            FROM generated_reports
            WHERE user_id = :user_id AND report_type = 'weekly'
            ORDER BY created_at DESC
            """
        )
        with self.engine.connect() as conn:
            result = conn.execute(stmt, {"user_id": user_id})
            rows = [dict(row._mapping) for row in result]
        for row in rows:
            row["is_unread"] = not bool(row.get("read_at"))
            ctx_raw = row.pop("context_json", None)
            if isinstance(ctx_raw, str) and ctx_raw.strip():
                try:
                    row["context"] = json.loads(ctx_raw)
                except Exception:
                    row["context"] = {}
            else:
                row["context"] = {}
        return rows


# Singleton
db = Database()
