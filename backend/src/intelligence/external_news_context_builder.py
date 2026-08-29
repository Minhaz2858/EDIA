from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from urllib.parse import urlparse

from backend.src.domain.knowledge_graph import domain_graph
from backend.src.intelligence.web_search_service import SearchResult, get_web_search


_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

_SOURCE_AUTHORITY: dict[str, tuple[str, float]] = {
    "longzhong.com": ("tier1", 1.0),
    "mysteel.com": ("tier1", 0.95),
    "sci99.com": ("tier1", 0.92),
    "baiinfo.com": ("tier1", 0.9),
    "cpcif.org.cn": ("tier2", 0.78),
    "jshg.com": ("tier2", 0.72),
    "chemol.com": ("tier2", 0.72),
    "chem-info.com.cn": ("tier2", 0.72),
    "xinhuanet.com": ("tier3", 0.65),
    "reuters.com": ("tier3", 0.65),
    "jin10.com": ("tier3", 0.6),
    "wind.com.cn": ("tier3", 0.6),
}

_PRODUCT_QUERY_TEMPLATES: dict[str, list[str]] = {
    "cracked_c5": [
        "裂解C5 市场价格 本周",
        "石脑油 价格 亚洲 本周",
        "C5石油树脂 开工率 市场",
        "中石化 裂解C5 挂牌价 调整",
    ],
    "isoprene": [
        "异戊二烯 市场价格 华东",
        "裂解C5 石脑油 成本传导",
        "SIS 开工率 需求 本周",
        "上海石化 异戊二烯 挂牌价",
    ],
    "piperylene": [
        "间戊二烯 市场价格 华东",
        "裂解C5 成本 间戊二烯",
        "固化剂 MTHPA C5树脂 开工率",
        "上海石化 间戊二烯 挂牌价",
    ],
    "sis": [
        "SIS 市场价格 巴陵",
        "异戊二烯 苯乙烯 原料成本",
        "SIS 开工率 产能 装置",
        "巴陵石化 金山巴陵 SIS 出厂价",
    ],
    "dcpd": [
        "双环戊二烯 DCPD 市场价格",
        "裂解C5 成本 DCPD",
        "UPR 不饱和聚酯树脂 开工率",
        "上海石化 DCPD 挂牌价",
    ],
    "c5_resin": [
        "C5石油树脂 胶粘剂 市场价格",
        "间戊二烯 原料 C5树脂成本",
        "金海晨光 鲁华 天利 C5树脂 报价",
        "加氢树脂 C9树脂 竞争 市场",
    ],
    "raffinate_c5": [
        "抽余C5 市场价格 华南",
        "油品市场 汽油 行情",
        "C5轻组分 消费税 价格",
        "珠海稳定轻烃 价格",
    ],
    "blowing_agent": [
        "戊烷发泡剂 市场价格 华东",
        "EPS 下游需求 发泡剂",
        "正戊烷 异戊烷 环戊烷 价格",
        "青岛炼化 发泡剂 报价",
    ],
}

_PORTFOLIO_QUERIES = [
    "C5产业链 市场一周前瞻",
    "石脑油 原油 化工 市场周报",
    "C5C9 产品 价格走势 本周",
]

_RISK_QUERY_MARKERS = (
    "reversal risk",
    "reversal",
    "cost shock",
    "cost push",
    "supply shock",
    "event risk",
    "geopolitical",
    "shipping",
    "disruption",
    "why",
    "driver",
    "原因",
    "驱动",
    "回撤",
    "回调",
    "风险",
    "成本冲击",
    "成本推动",
    "上游冲击",
    "地缘",
    "航运",
    "扰动",
)

_EXPLICIT_NEWS_MARKERS = (
    "news",
    "headline",
    "event",
    "events",
    "latest news",
    "breaking",
    "what happened",
    "why",
    "cause",
    "driver",
    "drivers",
    "geopolit",
    "war",
    "sanction",
    "shipping",
    "hormuz",
    "red sea",
    "opec",
    "新闻",
    "事件",
    "最新消息",
    "发生了什么",
    "为什么",
    "原因",
    "驱动",
    "地缘",
    "制裁",
    "航运",
    "战争",
    "霍尔木兹",
    "红海",
)

_URL_BLACKLIST = (
    "wikipedia.org",
    "pubchem.ncbi.nlm.nih.gov",
    "slideshare.net",
    "researchgate.net",
    "patents.google.com",
    ".pdf",
)


@dataclass(slots=True)
class RankedNewsResult:
    title: str
    url: str
    snippet: str
    source: str
    published_date: str
    score: float
    tier: str
    domain: str
    reason: str


class ExternalNewsContextBuilder:
    def __init__(self, web_search=None):
        self.web_search = web_search or get_web_search()

    def build_context(
        self,
        *,
        user_query: str,
        user_context: Any,
        graph_product_ids: Optional[list[str]] = None,
        portfolio_mode: bool = False,
        perception_snapshot: Optional[dict[str, Any]] = None,
        language: str = "zh",
        mode: str = "auto",
    ) -> dict[str, Any]:
        query = (user_query or "").strip()
        if not query:
            return {"formatted_text": "", "results": [], "queries": []}

        mode = (mode or "auto").strip().lower()
        if mode in {"0", "false", "disable", "disabled"}:
            mode = "off"
        if mode == "off":
            return {"formatted_text": "", "results": [], "queries": []}

        product_ids = list(graph_product_ids or [])
        product_text = str(getattr(user_context, "product", "") or "").strip()
        if mode == "auto" and not self._should_run(query, product_ids, product_text):
            return {"formatted_text": "", "results": [], "queries": []}

        timelimit = self._infer_timelimit(query)
        cache_key = self._cache_key(
            query=query,
            language=language,
            product_ids=product_ids,
            product_text=product_text,
            timelimit=timelimit,
            portfolio_mode=portfolio_mode,
        )
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        queries = self._build_queries(
            user_query=query,
            graph_product_ids=product_ids,
            portfolio_mode=portfolio_mode,
            perception_snapshot=perception_snapshot or {},
        )
        raw_results = self._collect_results(queries, timelimit=timelimit)
        ranked = self._rank_results(
            raw_results,
            user_query=query,
            graph_product_ids=product_ids,
            product_text=product_text,
        )
        formatted = self._format_results(
            results=ranked,
            queries=queries,
            timelimit=timelimit,
            product_ids=product_ids,
        )
        payload = {"formatted_text": formatted, "results": ranked, "queries": queries}
        self._cache_set(cache_key, payload)
        return payload

    def _should_run(
        self,
        user_query: str,
        graph_product_ids: list[str],
        product_text: str,
    ) -> bool:
        q = user_query.lower()
        if any(marker in q for marker in _EXPLICIT_NEWS_MARKERS):
            return True

        if any(marker in q for marker in _RISK_QUERY_MARKERS):
            return True

        # Default auto mode should stay fast for routine market-price questions.
        # Operators can opt back into the previous broader behavior via env.
        broad_auto = (os.getenv("WEB_SEARCH_AUTO_INCLUDE_ROUTINE_MARKET_QUERIES", "false") or "false").strip().lower()
        if broad_auto in {"1", "true", "yes", "on"}:
            market_markers = [
                "this week",
                "weekly",
                "market",
                "price",
                "prices",
                "analysis",
                "trend",
                "本周",
                "市场",
                "价格",
                "行情",
                "走势",
                "分析",
            ]
            return bool(
                graph_product_ids
                or product_text
                or any(marker in q for marker in market_markers)
            )

        return False

    def _infer_timelimit(self, user_query: str) -> str:
        q = (user_query or "").lower()
        if any(
            marker in q
            for marker in ["this month", "month", "mom", "本月", "月度", "月环比"]
        ):
            return "m"
        if any(marker in q for marker in ["today", "latest", "今日", "今天", "最新"]):
            return "d"
        return "w"

    def _build_queries(
        self,
        *,
        user_query: str,
        graph_product_ids: list[str],
        portfolio_mode: bool,
        perception_snapshot: dict[str, Any],
    ) -> list[str]:
        queries: list[str] = []
        if portfolio_mode:
            queries.extend(_PORTFOLIO_QUERIES)
        else:
            for product_id in graph_product_ids[:2]:
                queries.extend(_PRODUCT_QUERY_TEMPLATES.get(product_id, []))

        queries.extend(
            self._intent_queries(user_query, graph_product_ids, perception_snapshot)
        )
        queries.extend(self._adaptive_queries(graph_product_ids, perception_snapshot))
        queries.append(user_query)

        deduped: list[str] = []
        seen: set[str] = set()
        for item in queries:
            query = str(item or "").strip()
            if not query or query in seen:
                continue
            seen.add(query)
            deduped.append(query)
            if len(deduped) >= 10:
                break
        return deduped

    def _intent_queries(
        self,
        user_query: str,
        graph_product_ids: list[str],
        perception_snapshot: dict[str, Any],
    ) -> list[str]:
        q = (user_query or "").lower()
        products = perception_snapshot.get("products") or {}
        crude = products.get("crude_oil") or {}
        naphtha = products.get("naphtha") or {}
        crude_7d = self._to_float(crude.get("change_7d_pct"))
        naphtha_7d = self._to_float(naphtha.get("change_7d_pct"))

        strong_cost_move = bool(
            (crude_7d is not None and abs(crude_7d) >= 8.0)
            or (naphtha_7d is not None and abs(naphtha_7d) >= 10.0)
            or (
                crude_7d is not None
                and naphtha_7d is not None
                and abs(naphtha_7d - crude_7d) >= 5.0
            )
        )
        risk_focused = any(marker in q for marker in _RISK_QUERY_MARKERS)
        if not risk_focused and not strong_cost_move:
            return []

        queries: list[str] = [
            "原油 暴涨 原因 地缘政治 航运",
            "石脑油 上涨 原因 亚洲 供应 航运",
            "霍尔木兹 红海 航运 亚洲 石脑油",
            "OPEC 原油 亚洲 石脑油 本周",
        ]

        for product_id in graph_product_ids[:2]:
            name = self._product_search_name(product_id)
            if not name:
                continue
            queries.append(f"{name} 成本推动 原因 石脑油 本周")
            queries.append(f"{name} 回调 风险 下游需求 本周")

        return queries

    def _adaptive_queries(
        self,
        graph_product_ids: list[str],
        perception_snapshot: dict[str, Any],
    ) -> list[str]:
        products = perception_snapshot.get("products") or {}
        adaptive: list[str] = []

        crude = products.get("crude_oil") or {}
        naphtha = products.get("naphtha") or {}
        crude_7d = self._to_float(crude.get("change_7d_pct"))
        naphtha_7d = self._to_float(naphtha.get("change_7d_pct"))
        if crude_7d is not None and abs(crude_7d) >= 10.0:
            adaptive.append("原油暴涨 原因 地缘政治")
            adaptive.append("原油 航运 扰动 亚洲 化工链")
        if naphtha_7d is not None and abs(naphtha_7d) >= 10.0:
            adaptive.append("石脑油 暴涨 原因 亚洲 供应 航运")
        if (
            crude_7d is not None
            and naphtha_7d is not None
            and abs(naphtha_7d - crude_7d) > 5.0
        ):
            adaptive.append("石脑油 供应 裂解装置 检修")
            adaptive.append("石脑油 涨幅 高于 原油 原因 亚洲")

        for product_id in graph_product_ids[:2]:
            pdata = products.get(product_id) or {}
            product_change = self._to_float(pdata.get("change_7d_pct"))
            if product_change is not None and abs(product_change) >= 15.0:
                name = self._product_search_name(product_id)
                if name:
                    adaptive.append(f"{name} 价格暴涨 原因 分析")
        return adaptive

    def _collect_results(
        self, queries: list[str], timelimit: str
    ) -> list[SearchResult]:
        region = "cn-zh"
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for query in queries:
            collected: list[SearchResult] = []
            try:
                collected = self.web_search.search_news(
                    query,
                    max_results=6,
                    region=region,
                    timelimit=timelimit,
                )
            except Exception:
                collected = []
            if len(collected) < 3:
                try:
                    collected.extend(
                        self.web_search.search(query, max_results=4, region=region)
                    )
                except Exception:
                    pass

            for result in collected:
                url = str(getattr(result, "url", "") or "").strip()
                if not url or not url.startswith("http"):
                    continue
                if any(blocked in url.lower() for blocked in _URL_BLACKLIST):
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append(result)
                if len(results) >= 24:
                    return results
        return results

    def _rank_results(
        self,
        results: list[SearchResult],
        *,
        user_query: str,
        graph_product_ids: list[str],
        product_text: str,
    ) -> list[RankedNewsResult]:
        product_terms = self._product_terms(graph_product_ids, product_text)
        ranked: list[RankedNewsResult] = []
        for result in results:
            title = str(getattr(result, "title", "") or "").strip()
            snippet = str(getattr(result, "snippet", "") or "").strip()
            url = str(getattr(result, "url", "") or "").strip()
            if not title or not url:
                continue
            product_score = self._product_match_score(
                title, snippet, user_query, product_terms
            )
            freshness = self._freshness_score(
                str(getattr(result, "published_date", "") or "")
            )
            tier, authority = self._authority_score(url)
            depth = self._content_depth_score(title, snippet)
            score = round(
                product_score * 0.4 + freshness * 0.3 + authority * 0.2 + depth * 0.1, 3
            )
            if score < 0.5:
                continue
            domain = self._domain(url)
            reason = (
                f"product={product_score:.2f}; fresh={freshness:.2f}; "
                f"authority={authority:.2f}; depth={depth:.2f}"
            )
            ranked.append(
                RankedNewsResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    source=str(getattr(result, "source", "") or domain),
                    published_date=str(getattr(result, "published_date", "") or ""),
                    score=score,
                    tier=tier,
                    domain=domain,
                    reason=reason,
                )
            )

        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[:6]

    def _format_results(
        self,
        *,
        results: list[RankedNewsResult],
        queries: list[str],
        timelimit: str,
        product_ids: list[str],
    ) -> str:
        if not results:
            return ""

        scope = (
            ", ".join(
                self._product_search_name(pid)
                for pid in product_ids
                if self._product_search_name(pid)
            )
            or "broad_market"
        )
        lines = [
            "WEB_SEARCH (QUERY-TIME MARKET INTELLIGENCE):",
            f"- timelimit={timelimit}; queries={len(queries)}; scope={scope}",
            "- Usage rule: Prefer Tier-1/Tier-2 sources and explicit price/rate/company facts. If a claim is supported only by snippet text, phrase it as external signal rather than verified internal fact.",
        ]
        for result in results:
            header = f"- [score={result.score:.2f} | {result.tier}] {result.title}"
            if result.published_date:
                header += f" ({result.published_date})"
            header += f" — {result.url}"
            lines.append(header)
            lines.append(f"  source={result.source or result.domain}; {result.reason}")
            if result.snippet:
                compact = " ".join(result.snippet.split())
                if len(compact) > 240:
                    compact = compact[:240] + "..."
                lines.append(f"  {compact}")
        return "\n".join(lines).strip() + "\n"

    def _product_terms(
        self, graph_product_ids: list[str], product_text: str
    ) -> list[str]:
        terms: list[str] = []
        for product_id in graph_product_ids:
            node = self._node(product_id)
            if not node:
                continue
            terms.extend(
                [
                    str(getattr(node, "name_cn", "") or "").lower(),
                    str(getattr(node, "name_en", "") or "").lower(),
                    str(getattr(node, "id", "") or "").lower(),
                ]
            )
        if product_text:
            parts = re.split(
                r"\s*(?:,|，|/|&|and|和|及|与)\s*", product_text, flags=re.IGNORECASE
            )
            terms.extend(
                str(part).strip().lower() for part in parts if str(part).strip()
            )
        extras = ["石脑油", "中石化", "挂牌价", "开工率", "华东", "华南", "中国"]
        seen: set[str] = set()
        deduped: list[str] = []
        for term in terms + extras:
            if term and term not in seen:
                seen.add(term)
                deduped.append(term)
        return deduped

    def _product_match_score(
        self,
        title: str,
        snippet: str,
        user_query: str,
        product_terms: list[str],
    ) -> float:
        hay = f"{title}\n{snippet}".lower()
        exact_hits = sum(1 for term in product_terms if term and term in hay)
        if exact_hits >= 2:
            return 1.0
        if exact_hits == 1:
            return 0.8
        if any(
            term in hay
            for term in ["石脑油", "原油", "裂解", "sinopec", "naphtha", "crude"]
        ):
            return 0.55
        if any(token in hay for token in user_query.lower().split() if len(token) > 3):
            return 0.4
        return 0.0

    def _freshness_score(self, published_date: str) -> float:
        dt = self._parse_datetime(published_date)
        if not dt:
            return 0.35
        now = datetime.now(timezone.utc)
        age = now - dt
        if age < timedelta(days=0):
            age = timedelta(0)
        if age <= timedelta(days=1):
            return 1.0
        if age <= timedelta(days=2):
            return 0.9
        if age <= timedelta(days=4):
            return 0.7
        if age <= timedelta(days=7):
            return 0.5
        if age <= timedelta(days=14):
            return 0.3
        return 0.0

    def _authority_score(self, url: str) -> tuple[str, float]:
        domain = self._domain(url)
        for known, value in _SOURCE_AUTHORITY.items():
            if domain.endswith(known):
                return value
        return ("tier3", 0.45)

    def _content_depth_score(self, title: str, snippet: str) -> float:
        hay = f"{title}\n{snippet}"
        if re.search(
            r"\d{2,}(?:[.,]\d+)?\s*(?:元/吨|CNY|%|吨|bbl|美元)", hay, re.IGNORECASE
        ):
            return 1.0
        if re.search(
            r"开工率|挂牌价|检修|产能|price|operating rate|maintenance|capacity",
            hay,
            re.IGNORECASE,
        ):
            return 0.75
        if len(snippet.strip()) >= 80:
            return 0.5
        return 0.3

    def _domain(self, url: str) -> str:
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return ""

    def _parse_datetime(self, value: str) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
        cleaned = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _product_search_name(self, product_id: str) -> str:
        node = self._node(product_id)
        if not node:
            return product_id
        return str(
            getattr(node, "name_cn", "") or getattr(node, "name_en", "") or product_id
        )

    def _node(self, product_id: str):
        for node in getattr(domain_graph, "nodes", []) or []:
            if str(getattr(node, "id", "") or "") == product_id:
                return node
        return None

    def _cache_key(
        self,
        *,
        query: str,
        language: str,
        product_ids: list[str],
        product_text: str,
        timelimit: str,
        portfolio_mode: bool,
    ) -> str:
        return "::".join(
            [
                language,
                timelimit,
                "portfolio" if portfolio_mode else "focused",
                ",".join(product_ids),
                product_text,
                query,
            ]
        )

    def _cache_get(self, key: str) -> Optional[dict[str, Any]]:
        item = _CACHE.get(key)
        if not item:
            return None
        expires_at, payload = item
        if expires_at <= time.time():
            _CACHE.pop(key, None)
            return None
        return payload

    def _cache_set(self, key: str, payload: dict[str, Any]) -> None:
        ttl_seconds = int(
            os.getenv("EXTERNAL_NEWS_CONTEXT_TTL_SECONDS", "7200") or "7200"
        )
        if ttl_seconds <= 0:
            return
        _CACHE[key] = (time.time() + ttl_seconds, payload)

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return None


_external_news_context_builder: Optional[ExternalNewsContextBuilder] = None


def get_external_news_context_builder() -> ExternalNewsContextBuilder:
    global _external_news_context_builder
    if _external_news_context_builder is None:
        _external_news_context_builder = ExternalNewsContextBuilder()
    return _external_news_context_builder
