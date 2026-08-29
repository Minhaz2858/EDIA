"""Proactive Alert System — Event-Based with MySQL Persistence

Alerts are written to the MySQL `alerts_log` table the FIRST TIME a price
threshold is crossed for a given product+direction combination.  Subsequent
scans do NOT re-insert the same event — deduplication window is 7 days per
product per alert type.

Lifecycle
---------
* triggered_at   — when the alert was first detected and persisted
* read_at        — set when the user opens the alert panel (/api/alerts/read)
* dismissed_at   — set when the user clicks the X button (/api/alerts/dismiss/<id>)

Badge count = alerts WHERE dismissed_at IS NULL AND read_at IS NULL.
Panel shows  = alerts WHERE dismissed_at IS NULL (both read and unread).
"""

from __future__ import annotations

import math
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from backend.src.db.sqlite_db import Database
from backend.src.service.perception_service import perception_service
from backend.src.service.market_data_service import (
    LZ_EXTERNAL_NODE_IDS,
    REAL_DATA_MAP,
    market_data,
)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
_SPIKE_PCT = 3.0  # 7-day change ≥ +3 % → price_spike alert
_DROP_PCT = -3.0  # 7-day change ≤ −3 % → price_drop  alert
_DEDUP_DAYS = 7  # don't re-alert same product+type within this window

# Regional arbitrage thresholds (CNY/ton)
# If regional spread exceeds estimated freight cost → arbitrage opportunity
_FREIGHT_COSTS: Dict[str, int] = {
    "east_china_south_china": 200,
    "east_china_wuhan": 150,
    "wuhan_south_china": 180,
    "south_china_east_china": 200,
    "wuhan_east_china": 150,
    "south_china_wuhan": 180,
}
_ARBITRAGE_PRODUCTS = ["isoprene", "piperylene", "dcpd", "cracked_c5"]


# ---------------------------------------------------------------------------
# DB helper — thin layer over the shared Database engine
# ---------------------------------------------------------------------------


class AlertDB:
    """CRUD operations against the `alerts_log` MySQL table."""

    def __init__(self) -> None:
        self._db = Database()  # reuses edia_db MySQL connection
        self._engine = self._db.engine

    @staticmethod
    def _sanitize_alert_record(alert: Dict[str, Any]) -> Dict[str, Any]:
        record = dict(alert)

        for field_name in ("change_7d_pct", "price"):
            raw_value = record.get(field_name)
            try:
                numeric_value = float(raw_value)
            except (TypeError, ValueError):
                numeric_value = 0.0

            if not math.isfinite(numeric_value):
                numeric_value = 0.0

            record[field_name] = round(numeric_value, 2)

        return record

    # -- write -------------------------------------------------------------

    def insert_alert(self, alert: Dict[str, Any]) -> None:
        payload = self._sanitize_alert_record(alert)
        stmt = text(
            """
            INSERT INTO alerts_log
                (id, product, product_name, alert_type, severity,
                 change_7d_pct, price, message, triggered_at,
                 read_at, dismissed_at)
            VALUES
                (:id, :product, :product_name, :alert_type, :severity,
                 :change_7d_pct, :price, :message, :triggered_at,
                 NULL, NULL)
            """
        )
        with self._engine.begin() as conn:
            conn.execute(stmt, payload)
    
    def insert_notification_alert(self, product: str, product_name: str, message: str, severity: str = "medium") -> None:
        """Create a notification-type alert (e.g., for report completion)."""
        alert = {
            "id": str(uuid.uuid4()),
            "product": product,
            "product_name": product_name,
            "alert_type": "notification",
            "severity": severity,
            "change_7d_pct": 0.0,
            "price": 0.0,
            "message": message,
            "triggered_at": datetime.now().isoformat(),
        }
        self.insert_alert(alert)

    def mark_all_read(self) -> int:
        """Set read_at = NOW() on every unread, non-dismissed alert.
        Returns the number of rows updated.
        """
        stmt = text(
            """
            UPDATE alerts_log
               SET read_at = :now
             WHERE read_at IS NULL
               AND dismissed_at IS NULL
            """
        )
        with self._engine.begin() as conn:
            result = conn.execute(stmt, {"now": datetime.now().isoformat()})
            return result.rowcount

    def dismiss_alert(self, alert_id: str) -> bool:
        """Set dismissed_at on a single alert. Returns True if found."""
        stmt = text(
            """
            UPDATE alerts_log
               SET dismissed_at = :now
             WHERE id = :id
               AND dismissed_at IS NULL
            """
        )
        with self._engine.begin() as conn:
            result = conn.execute(
                stmt, {"now": datetime.now().isoformat(), "id": alert_id}
            )
            return result.rowcount > 0

    # -- read --------------------------------------------------------------

    def get_active_alerts(self) -> List[Dict[str, Any]]:
        """Return all non-dismissed alerts, newest first."""
        stmt = text(
            """
            SELECT id, product, product_name, alert_type, severity,
                   change_7d_pct, price, message, triggered_at,
                   read_at, dismissed_at
              FROM alerts_log
             WHERE dismissed_at IS NULL
             ORDER BY
                   CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                   triggered_at DESC
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().fetchall()
        return [dict(r) for r in rows]

    def count_unread(self) -> int:
        stmt = text(
            """
            SELECT COUNT(*) AS n
              FROM alerts_log
             WHERE read_at IS NULL
               AND dismissed_at IS NULL
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).fetchone()
            return row[0] if row else 0

    def recent_alert_exists(
        self, product: str, alert_type: str, window_days: int = _DEDUP_DAYS
    ) -> bool:
        """Return True if an alert for product+type was triggered within the
        deduplication window — regardless of whether it was dismissed.
        This prevents the same market event from re-firing just because
        the user dismissed the notification.
        """
        cutoff = (datetime.now() - timedelta(days=window_days)).isoformat()
        stmt = text(
            """
            SELECT 1
              FROM alerts_log
             WHERE product    = :product
               AND alert_type = :alert_type
               AND triggered_at >= :cutoff
             LIMIT 1
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(
                stmt,
                {"product": product, "alert_type": alert_type, "cutoff": cutoff},
            ).fetchone()
        return row is not None


def _build_alert_source_info(product_id: str, alert_type: str) -> Dict[str, Any]:
    mapped_product, mapped_price_type = REAL_DATA_MAP.get(
        product_id, (product_id, "unknown")
    )
    history = market_data.get_price_history(product_id)
    as_of_date = history[-1].get("date") if history else None

    # LZ external product views and market_prices table both roll into market_data.
    # Expose the channel so users can see where this alert is grounded.
    if product_id in LZ_EXTERNAL_NODE_IDS or mapped_product in LZ_EXTERNAL_NODE_IDS:
        channel = "lz_view"
    else:
        channel = "market_prices"

    source: Dict[str, Any] = {
        "system": "market_data",
        "table": "market_prices",
        "channel": channel,
        "product_id": mapped_product,
        "price_type": mapped_price_type,
        "as_of_date": as_of_date,
        "alert_detector": "perception_worker_threshold_scan",
    }

    if alert_type == "regional_arbitrage":
        source["alert_detector"] = "regional_spread_arbitrage_scan"

    return source


def _format_source_text(source: Dict[str, Any]) -> str:
    channel = str(source.get("channel") or "market_prices")
    product_id = str(source.get("product_id") or "unknown")
    price_type = str(source.get("price_type") or "unknown")
    as_of_date = str(source.get("as_of_date") or "unknown")
    return (
        f"{channel} -> market_prices.{product_id}.{price_type} "
        f"(as of {as_of_date})"
    )


# ---------------------------------------------------------------------------
# Detection — compare snapshot against thresholds and deduplicate vs DB
# ---------------------------------------------------------------------------


def _detect_and_persist(
    snapshot: Dict[str, Any], alert_db: AlertDB
) -> List[Dict[str, Any]]:
    """Scan the perception snapshot; persist NEW alert events to MySQL;
    return the list of newly created alerts (may be empty if all deduped).
    """
    new_alerts: List[Dict[str, Any]] = []
    products = snapshot.get("products", {})
    ref_date = snapshot.get("reference_date", datetime.now().date().isoformat())

    for pid, pdata in products.items():
        change = pdata.get("change_7d_pct", 0.0)
        price = pdata.get("latest_price", 0)
        name_en = pdata.get("name_en", pid)
        name_cn = pdata.get("name_cn", "")

        if change >= _SPIKE_PCT:
            alert_type = "price_spike"
            severity = "high" if change >= _SPIKE_PCT * 2 else "medium"
        elif change <= _DROP_PCT:
            alert_type = "price_drop"
            severity = "high" if change <= _DROP_PCT * 2 else "medium"
        else:
            continue  # No threshold crossed — skip

        # Deduplication: skip if we already have an active alert for this
        # product+type within the window
        if alert_db.recent_alert_exists(pid, alert_type):
            continue

        # Build and persist the new event
        alert_id = str(uuid.uuid4())
        message = (
            f"{name_cn or name_en} {'+' if change >= 0 else ''}{change:.1f}%"
            f"（7日）, 当前价格 ¥{price:,.0f}/吨"
        )
        record: Dict[str, Any] = {
            "id": alert_id,
            "product": pid,
            "product_name": name_en,
            "alert_type": alert_type,
            "severity": severity,
            "change_7d_pct": round(change, 2),
            "price": round(float(price), 2),
            "message": message,
            "triggered_at": datetime.now().isoformat(),
        }
        alert_db.insert_alert(record)
        new_alerts.append(record)

    return new_alerts


def _detect_arbitrage_alerts(alert_db: AlertDB) -> List[Dict[str, Any]]:
    """Scan regional price spreads; generate arbitrage alerts when spread > freight."""
    new_alerts: List[Dict[str, Any]] = []
    try:
        from backend.src.service.market_data_service import market_data

        for product_id in _ARBITRAGE_PRODUCTS:
            regional = market_data.get_regional_prices(product_id)
            if len(regional) < 2:
                continue

            # Only compare the main regional types
            key_regions = {
                k: v for k, v in regional.items()
                if k in ("east_china", "south_china", "wuhan")
            }
            if len(key_regions) < 2:
                continue

            spread = max(key_regions.values()) - min(key_regions.values())
            max_freight = max(_FREIGHT_COSTS.values(), default=200)

            if spread <= max_freight:
                continue

            alert_type = "regional_arbitrage"
            if alert_db.recent_alert_exists(product_id, alert_type, window_days=3):
                continue

            high_region = max(key_regions, key=lambda k: key_regions[k])
            low_region = min(key_regions, key=lambda k: key_regions[k])
            high_price = key_regions[high_region]
            low_price = key_regions[low_region]

            _label = {"east_china": "华东", "south_china": "华南", "wuhan": "华中"}
            message = (
                f"{product_id}: 区域价差 ¥{spread:,.0f}/吨 "
                f"({_label.get(high_region, high_region)} ¥{high_price:,.0f} vs "
                f"{_label.get(low_region, low_region)} ¥{low_price:,.0f}) "
                f"超出运费阈值 ¥{max_freight}/吨 — 存在套利空间"
            )

            record: Dict[str, Any] = {
                "id": str(uuid.uuid4()),
                "product": product_id,
                "product_name": product_id,
                "alert_type": alert_type,
                "severity": "high" if spread > max_freight * 1.5 else "medium",
                "change_7d_pct": 0.0,
                "price": round(float(high_price), 2),
                "message": message,
                "triggered_at": datetime.now().isoformat(),
            }
            alert_db.insert_alert(record)
            new_alerts.append(record)

    except Exception as e:
        print(f"[AlertService] Arbitrage check error: {e}")

    return new_alerts


# ---------------------------------------------------------------------------
# PerceptionWorker — background scanner with MySQL persistence
# ---------------------------------------------------------------------------


class PerceptionWorker:
    """Runs ``perception_service.build_snapshot()`` every ``interval_sec``.

    On each scan:
    1. Build a fresh market snapshot.
    2. Detect threshold crossings.
    3. Write only NEW events to MySQL (deduplicated).
    4. ``get_alerts()`` always reads the live state from MySQL.
    """

    def __init__(self, interval_sec: int = 1800) -> None:
        self._interval = interval_sec
        self._lock = threading.Lock()
        self._last_scan: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._alert_db: Optional[AlertDB] = None

    def _get_alert_db(self) -> AlertDB:
        if self._alert_db is None:
            self._alert_db = AlertDB()
        return self._alert_db

    # -- public API --------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[AlertService] Perception worker started (interval={self._interval}s)")

    def stop(self) -> None:
        self._stop_event.set()

    def scan(self) -> List[Dict[str, Any]]:
        """Run a single scan, persist new events, return newly created alerts."""
        try:
            snapshot = perception_service.build_snapshot()
            new_alerts = _detect_and_persist(snapshot, self._get_alert_db())
            # Regional arbitrage check
            arb_alerts = _detect_arbitrage_alerts(self._get_alert_db())
            new_alerts.extend(arb_alerts)
            with self._lock:
                self._last_scan = datetime.now().isoformat()
            if new_alerts:
                print(
                    f"[AlertService] {len(new_alerts)} new alert(s) persisted to MySQL"
                )
            return new_alerts
        except Exception as e:
            print(f"[AlertService] Scan error: {e}")
            return []

    def get_alerts(self) -> Dict[str, Any]:
        """Read current non-dismissed alerts from MySQL (live read)."""
        try:
            alert_db = self._get_alert_db()
            alerts = alert_db.get_active_alerts()
            unread = alert_db.count_unread()
            # Normalise datetime objects → ISO strings for JSON serialisation
            for a in alerts:
                for key in ("triggered_at", "read_at", "dismissed_at"):
                    if isinstance(a.get(key), datetime):
                        a[key] = a[key].isoformat()
                # Rename alert_type → type for frontend compatibility
                a["type"] = a.pop("alert_type", a.get("type"))
                # Add timestamp alias (triggered_at date portion) for frontend display
                raw_ts = a.get("triggered_at", "")
                a["timestamp"] = raw_ts[:10] if raw_ts else ""
                source = _build_alert_source_info(
                    str(a.get("product") or ""), str(a.get("type") or "")
                )
                a["source"] = source
                a["source_text"] = _format_source_text(source)
            with self._lock:
                last_scan = self._last_scan
            return {
                "alerts": alerts,
                "count": len(alerts),
                "unread_count": unread,
                "last_scan": last_scan,
            }
        except Exception as e:
            print(f"[AlertService] get_alerts error: {e}")
            return {"alerts": [], "count": 0, "unread_count": 0, "last_scan": None}

    def mark_all_read(self) -> int:
        return self._get_alert_db().mark_all_read()

    def dismiss_alert(self, alert_id: str) -> bool:
        return self._get_alert_db().dismiss_alert(alert_id)

    # -- internal ----------------------------------------------------------

    def _run(self) -> None:
        self.scan()
        while not self._stop_event.is_set():
            self._stop_event.wait(self._interval)
            if not self._stop_event.is_set():
                self.scan()


# Singleton
alert_worker = PerceptionWorker(interval_sec=1800)  # 30 min
