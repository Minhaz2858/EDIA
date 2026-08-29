#!/usr/bin/env python3
import argparse
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


PRODUCTS = [
    "cracked_c5",
    "mixed_c5",
    "cracked_c9",
    "mixed_c9",
    "isoprene",
    "piperylene",
    "dcpd",
    "raffinate_c5",
    "sis",
    "blowing_agent",
    "naphtha",
    "styrene",
]

# UI has 5 options: auto/chat/analysis/report/deep_research.
# For backend API, auto is represented by omitting chat_mode.
CHAT_MODES: List[Tuple[str, Optional[str]]] = [
    ("auto", None),
    ("chat", "chat"),
    ("analysis", "analysis"),
    ("report", "report"),
    ("deep_research", "deep_research"),
]


@dataclass
class CaseResult:
    product: str
    mode_label: str
    route_target: str
    status: str
    task_type: str
    llm_route: str
    resolved_chat_mode: str
    elapsed_ms: int
    markdown_len: int
    has_erp: bool
    has_forecast: bool
    has_recommendation: bool
    has_action_trigger: bool
    section_count: int
    score: int
    ok: bool
    error: str = ""


def build_prompt(product: str, mode_label: str) -> str:
    # Single unified requirement: ERP + recommendation + future forecasting + triggers.
    # Keep prompt concise to reduce latency while preserving evaluation signal.
    return (
        f"请基于{product}做市场+ERP联合分析："
        "必须包含最近7天ERP销售/库存变化、当前价格与7日变化、"
        "未来1-2周价格预测（看跌/基准/看涨）及其概率、"
        "并给出可执行的定价与销售动作及触发条件。"
        f"请按{mode_label}风格输出。"
    )


def bool_text(x: bool) -> str:
    return "1" if x else "0"


def compute_metrics(markdown: str) -> Dict[str, object]:
    text = (markdown or "").lower()
    has_erp = any(k in text for k in ["erp", "库存", "销量", "sales", "inventory", "订单"])
    has_forecast = any(k in text for k in ["预测", "forecast", "看跌", "看涨", "基准", "scenario"]) 
    has_recommendation = any(k in text for k in ["建议", "recommend", "定价", "pricing", "action", "行动"]) 
    has_action_trigger = any(k in text for k in ["触发", "if ", "trigger", "若", "条件"]) 
    section_count = len(re.findall(r"(?:##\s*0?[1-6]|0[1-6]\s*[·\.])", markdown or ""))

    score = 0
    score += 25 if has_erp else 0
    score += 25 if has_forecast else 0
    score += 25 if has_recommendation else 0
    score += 15 if has_action_trigger else 0
    score += 10 if section_count >= 4 else 0

    ok = score >= 70 and len(markdown or "") >= 300

    return {
        "has_erp": has_erp,
        "has_forecast": has_forecast,
        "has_recommendation": has_recommendation,
        "has_action_trigger": has_action_trigger,
        "section_count": section_count,
        "score": score,
        "ok": ok,
    }


def call_chat(
    base_url: str,
    auth_header: str,
    message: str,
    chat_mode: Optional[str],
    route_target: str,
    graph_route: Optional[str],
    timeout_sec: float,
) -> Dict[str, object]:
    prefs: Dict[str, object] = {
        "language": "zh",
        "llm_route": route_target,
        "final_decision_llm": route_target,
        "use_external_reasoning": route_target == "external",
        # allow external route without sanitation blocker for benchmark comparability
        "external_payload_sanitized": True,
    }
    if graph_route:
        prefs["graph_llm_route"] = graph_route
        prefs["use_external_graph_reasoning"] = graph_route == "external"
    payload: Dict[str, object] = {
        "message": message,
        "prefs": prefs,
        "attachment_contexts": [],
    }
    if chat_mode:
        payload["chat_mode"] = chat_mode

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": auth_header,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_chat_stream(
    base_url: str,
    auth_header: str,
    message: str,
    chat_mode: Optional[str],
    route_target: str,
    graph_route: Optional[str],
    timeout_sec: float,
) -> Dict[str, object]:
    prefs: Dict[str, object] = {
        "language": "zh",
        "llm_route": route_target,
        "final_decision_llm": route_target,
        "use_external_reasoning": route_target == "external",
        "external_payload_sanitized": True,
    }
    if graph_route:
        prefs["graph_llm_route"] = graph_route
        prefs["use_external_graph_reasoning"] = graph_route == "external"
    payload: Dict[str, object] = {
        "message": message,
        "prefs": prefs,
        "attachment_contexts": [],
    }
    if chat_mode:
        payload["chat_mode"] = chat_mode

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat/stream",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": auth_header,
        },
        method="POST",
    )

    started = time.time()
    deltas: List[str] = []
    current_event = "message"
    done_payload: Optional[Dict[str, object]] = None

    def consume_event(event_name: str, raw_data: str) -> None:
        nonlocal done_payload
        if not raw_data:
            return
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            return
        if event_name == "delta":
            deltas.append(str(data.get("delta") or ""))
        elif event_name == "done":
            done_payload = dict(data)
        elif event_name == "error":
            raise RuntimeError(str(data.get("message") or "stream error"))

    with urllib.request.urlopen(req, timeout=min(max(timeout_sec, 10.0), 30.0)) as resp:
        data_lines: List[str] = []
        while True:
            if time.time() - started >= timeout_sec:
                break
            raw_line = resp.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith(":"):
                continue
            if not line:
                if data_lines:
                    consume_event(current_event, "\n".join(data_lines))
                    if done_payload is not None:
                        break
                current_event = "message"
                data_lines = []
                continue
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip() or "message"
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())

    if done_payload is not None:
        if not str(done_payload.get("markdown") or "").strip() and deltas:
            done_payload["markdown"] = "".join(deltas)
        return done_payload

    return {
        "markdown": "".join(deltas),
        "status": "client_partial" if deltas else "error",
        "task_type": "unknown",
        "llm_route": route_target,
        "resolved_chat_mode": chat_mode or "chat",
        "error_message": "stream wall-clock timeout before done event",
    }


def run_matrix(args: argparse.Namespace) -> Dict[str, object]:
    started = time.time()
    rows: List[CaseResult] = []
    total = 0

    cases: List[Tuple[str, str, Optional[str]]] = []
    for product in PRODUCTS:
        for mode_label, mode_value in CHAT_MODES:
            cases.append((product, mode_label, mode_value))

    if args.max_cases > 0:
        cases = cases[: args.max_cases]

    for product, mode_label, mode_value in cases:
        total += 1
        prompt = build_prompt(product, mode_label)
        t0 = time.time()
        try:
            if args.transport == "stream":
                resp = call_chat_stream(
                    base_url=args.base_url,
                    auth_header=args.auth,
                    message=prompt,
                    chat_mode=mode_value,
                    route_target=args.route,
                    graph_route=args.graph_route,
                    timeout_sec=args.timeout,
                )
            else:
                resp = call_chat(
                    base_url=args.base_url,
                    auth_header=args.auth,
                    message=prompt,
                    chat_mode=mode_value,
                    route_target=args.route,
                    graph_route=args.graph_route,
                    timeout_sec=args.timeout,
                )
            elapsed_ms = int((time.time() - t0) * 1000)
            markdown = str(resp.get("markdown") or "")
            m = compute_metrics(markdown)
            row = CaseResult(
                product=product,
                mode_label=mode_label,
                route_target=args.route,
                status=str(resp.get("status") or ""),
                task_type=str(resp.get("task_type") or ""),
                llm_route=str(resp.get("llm_route") or ""),
                resolved_chat_mode=str(resp.get("resolved_chat_mode") or ""),
                elapsed_ms=elapsed_ms,
                markdown_len=len(markdown),
                has_erp=bool(m["has_erp"]),
                has_forecast=bool(m["has_forecast"]),
                has_recommendation=bool(m["has_recommendation"]),
                has_action_trigger=bool(m["has_action_trigger"]),
                section_count=int(m["section_count"]),
                score=int(m["score"]),
                ok=bool(m["ok"]),
            )
        except urllib.error.HTTPError as exc:
            elapsed_ms = int((time.time() - t0) * 1000)
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                detail = str(exc)
            row = CaseResult(
                product=product,
                mode_label=mode_label,
                route_target=args.route,
                status="http_error",
                task_type="",
                llm_route="",
                resolved_chat_mode="",
                elapsed_ms=elapsed_ms,
                markdown_len=0,
                has_erp=False,
                has_forecast=False,
                has_recommendation=False,
                has_action_trigger=False,
                section_count=0,
                score=0,
                ok=False,
                error=f"HTTP {exc.code}: {detail[:240]}",
            )
        except Exception as exc:
            elapsed_ms = int((time.time() - t0) * 1000)
            row = CaseResult(
                product=product,
                mode_label=mode_label,
                route_target=args.route,
                status="error",
                task_type="",
                llm_route="",
                resolved_chat_mode="",
                elapsed_ms=elapsed_ms,
                markdown_len=0,
                has_erp=False,
                has_forecast=False,
                has_recommendation=False,
                has_action_trigger=False,
                section_count=0,
                score=0,
                ok=False,
                error=str(exc)[:240],
            )

        rows.append(row)
        print(
            f"[{total:02d}/{len(cases):02d}] {row.product} | {row.mode_label:<13} | "
            f"status={row.status:<10} route={row.llm_route or '-':<12} "
            f"score={row.score:>3} ok={bool_text(row.ok)} len={row.markdown_len:>4} t={row.elapsed_ms}ms"
        )

    ok_count = sum(1 for r in rows if r.ok)
    avg_score = round(sum(r.score for r in rows) / max(1, len(rows)), 2)
    avg_len = round(sum(r.markdown_len for r in rows) / max(1, len(rows)), 1)
    avg_ms = round(sum(r.elapsed_ms for r in rows) / max(1, len(rows)), 1)
    elapsed_total_ms = int((time.time() - started) * 1000)

    by_mode: Dict[str, Dict[str, object]] = {}
    for mode_label, _ in CHAT_MODES:
        mode_rows = [r for r in rows if r.mode_label == mode_label]
        if not mode_rows:
            continue
        by_mode[mode_label] = {
            "count": len(mode_rows),
            "ok": sum(1 for r in mode_rows if r.ok),
            "avg_score": round(sum(r.score for r in mode_rows) / len(mode_rows), 2),
            "avg_len": round(sum(r.markdown_len for r in mode_rows) / len(mode_rows), 1),
            "avg_ms": round(sum(r.elapsed_ms for r in mode_rows) / len(mode_rows), 1),
        }

    result = {
        "route_target": args.route,
        "base_url": args.base_url,
        "count": len(rows),
        "ok_count": ok_count,
        "ok_rate": round(ok_count * 100.0 / max(1, len(rows)), 2),
        "avg_score": avg_score,
        "avg_len": avg_len,
        "avg_ms": avg_ms,
        "elapsed_total_ms": elapsed_total_ms,
        "by_mode": by_mode,
        "rows": [r.__dict__ for r in rows],
    }
    return result


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="EDIA local/external LLM quality matrix benchmark")
    p.add_argument("--base-url", default="http://127.0.0.1:3000")
    p.add_argument("--auth", required=True, help="Authorization header value, e.g. 'Bearer <token>'")
    p.add_argument("--route", choices=["local", "external"], required=True)
    p.add_argument("--graph-route", choices=["local", "external"], default="")
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--transport", choices=["chat", "stream"], default="chat")
    p.add_argument("--max-cases", type=int, default=0, help="0 means full 12x5 matrix")
    p.add_argument("--out", required=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    out = run_matrix(args)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("---")
    print(
        f"saved={out_path} route={out['route_target']} count={out['count']} "
        f"ok_rate={out['ok_rate']} avg_score={out['avg_score']} avg_len={out['avg_len']} avg_ms={out['avg_ms']}"
    )


if __name__ == "__main__":
    main()
