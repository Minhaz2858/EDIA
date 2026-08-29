"""Ontology Integrity Monitor.

Background skill that audits product ontology consistency and alias drift.

Goals:
- Ensure resolver canonical IDs remain aligned with knowledge-graph nodes.
- Detect re-introduction of local alias maps outside the shared resolver.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import BackgroundSkill, SkillResult


class OntologyIntegrityMonitor(BackgroundSkill):
    name = "ontology_integrity_monitor"
    interval_minutes = 360  # every 6 hours

    def run(self) -> SkillResult:
        try:
            from backend.src.domain.knowledge_graph import domain_graph
            from backend.src.domain.product_resolver import (
                get_alias_mapping,
                list_supported_product_ids,
            )
        except Exception as exc:
            return SkillResult(
                skill_name=self.name,
                status="error",
                summary=f"import failed: {exc}",
            )

        alerts: list[str] = []

        graph_ids = {
            str(getattr(node, "id", "") or "").strip().lower()
            for node in domain_graph.nodes
            if str(getattr(node, "id", "") or "").strip()
        }
        resolver_ids = set(list_supported_product_ids(include_out_of_scope=False))

        missing_in_graph = sorted(resolver_ids - graph_ids)
        uncovered_graph_ids = sorted(graph_ids - resolver_ids)

        if missing_in_graph:
            alerts.append(
                "[ONTOLOGY] Resolver contains IDs missing from knowledge graph: "
                + ", ".join(missing_in_graph)
            )
        if uncovered_graph_ids:
            alerts.append(
                "[ONTOLOGY] Knowledge graph IDs not reachable from resolver aliases: "
                + ", ".join(uncovered_graph_ids)
            )

        duplicate_alias_maps = self._scan_duplicate_alias_maps()
        if duplicate_alias_maps:
            alerts.append(
                f"[ONTOLOGY] Found {len(duplicate_alias_maps)} potential duplicate alias-map definitions outside shared resolver"
            )

        total_aliases = len(get_alias_mapping())
        status = "success" if not missing_in_graph else "error"

        return SkillResult(
            skill_name=self.name,
            status=status,
            summary=(
                f"ontology audit: graph_ids={len(graph_ids)}, resolver_ids={len(resolver_ids)}, "
                f"aliases={total_aliases}, duplicate_maps={len(duplicate_alias_maps)}"
            ),
            data={
                "graph_ids_count": len(graph_ids),
                "resolver_ids_count": len(resolver_ids),
                "aliases_count": total_aliases,
                "missing_in_graph": missing_in_graph,
                "uncovered_graph_ids": uncovered_graph_ids,
                "duplicate_alias_maps": duplicate_alias_maps,
            },
            alerts=alerts,
        )

    def _scan_duplicate_alias_maps(self) -> list[dict[str, Any]]:
        backend_src = Path(__file__).resolve().parents[2]
        allowlist = {
            str((backend_src / "domain" / "product_resolver.py").resolve()),
        }
        pattern = re.compile(
            r"alias_to_id\s*=\s*\{|alias_map\s*=\s*\{|_PRODUCT_NAME_MAP\s*=\s*\{|_PRODUCT_KEYWORDS\s*=\s*\{|_PRODUCT_ALIASES\s*=\s*\{"
        )

        findings: list[dict[str, Any]] = []
        for file in backend_src.rglob("*.py"):
            file_path = str(file.resolve())
            if file_path in allowlist:
                continue
            try:
                text = file.read_text(encoding="utf-8")
            except Exception:
                continue

            for m in pattern.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                findings.append(
                    {
                        "path": str(file.relative_to(backend_src.parent)),
                        "line": line,
                        "pattern": m.group(0).strip(),
                    }
                )

        return findings
