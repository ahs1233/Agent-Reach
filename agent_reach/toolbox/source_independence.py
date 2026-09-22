"""Conservative source-lineage and independence assessment.

The engine separates URL count from source-lineage count. It only calls
multiple lineages "verified independent" when every lineage pair has an
explicit INDEPENDENT_OF relationship. Different URLs are never treated as
proof of independence.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any


DEPENDENCY_RELATIONSHIPS = {
    "DERIVED_FROM",
    "SYNDICATED_FROM",
    "MIRRORS",
}

SOURCE_RELATIONSHIP_TYPES = DEPENDENCY_RELATIONSHIPS | {
    "INDEPENDENT_OF",
    "CITES",
    "QUOTES",
}


def normalize_publisher(value: str | None) -> str:
    return " ".join(str(value or "").split()).casefold()


class _DisjointSet:
    def __init__(self, items: set[str]) -> None:
        self.parent = {item: item for item in items}

    def find(self, item: str) -> str:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: str, right: str) -> None:
        a = self.find(left)
        b = self.find(right)
        if a != b:
            if a < b:
                self.parent[b] = a
            else:
                self.parent[a] = b


def assess_source_independence(
    *,
    supporting_source_ids: list[str],
    sources: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return conservative lineage grouping and verified-independence status."""
    support_ids = list(dict.fromkeys(str(item) for item in supporting_source_ids))
    source_by_id = {
        str(source["source_id"]): source
        for source in sources
        if source.get("source_id")
    }
    missing = [source_id for source_id in support_ids if source_id not in source_by_id]
    if missing:
        raise ValueError(
            "source independence assessment cannot resolve sources: "
            + ", ".join(missing)
        )

    all_ids = set(source_by_id)
    dsu = _DisjointSet(all_ids)
    grouping_reasons: list[dict[str, Any]] = []

    def union_with_reason(left: str, right: str, reason: str) -> None:
        if left == right:
            return
        dsu.union(left, right)
        grouping_reasons.append(
            {"source_ids": sorted([left, right]), "reason": reason}
        )

    def collapse_by(field: str, reason: str, transform=None) -> None:
        buckets: dict[str, list[str]] = {}
        for source_id, source in source_by_id.items():
            raw = source.get(field)
            value = transform(raw) if transform else str(raw or "").strip()
            if not value:
                continue
            buckets.setdefault(value, []).append(source_id)
        for members in buckets.values():
            if len(members) < 2:
                continue
            first = members[0]
            for other in members[1:]:
                union_with_reason(first, other, reason)

    collapse_by("canonical_url", "SAME_CANONICAL_URL")
    collapse_by("representation_hash", "IDENTICAL_REPRESENTATION_HASH")
    collapse_by("source_family_id", "SAME_SOURCE_FAMILY")
    collapse_by("publisher", "SAME_PUBLISHER", normalize_publisher)

    explicit_independence: list[tuple[str, str]] = []
    relationship_conflicts: list[dict[str, Any]] = []
    for relationship in relationships:
        left = str(relationship.get("source_id") or "")
        right = str(relationship.get("related_source_id") or "")
        rel_type = str(relationship.get("relationship_type") or "").upper()
        if left not in all_ids or right not in all_ids:
            continue
        if rel_type in DEPENDENCY_RELATIONSHIPS:
            union_with_reason(left, right, f"EXPLICIT_{rel_type}")
        elif rel_type == "INDEPENDENT_OF":
            explicit_independence.append((left, right))

    groups: dict[str, list[str]] = {}
    for source_id in support_ids:
        groups.setdefault(dsu.find(source_id), []).append(source_id)

    ordered_groups = sorted(
        (sorted(members) for members in groups.values()),
        key=lambda members: members[0],
    )
    group_id_by_source: dict[str, str] = {}
    rendered_groups: list[dict[str, Any]] = []
    for index, members in enumerate(ordered_groups, start=1):
        group_id = f"L{index}"
        for source_id in members:
            group_id_by_source[source_id] = group_id
        member_set = set(members)
        reasons = sorted(
            {
                item["reason"]
                for item in grouping_reasons
                if set(item["source_ids"]) <= member_set
            }
        )
        rendered_groups.append(
            {
                "lineage_id": group_id,
                "source_ids": members,
                "canonical_urls": sorted(
                    {
                        str(source_by_id[item].get("canonical_url") or "")
                        for item in members
                        if source_by_id[item].get("canonical_url")
                    }
                ),
                "publishers": sorted(
                    {
                        str(source_by_id[item].get("publisher") or "")
                        for item in members
                        if source_by_id[item].get("publisher")
                    }
                ),
                "grouping_reasons": reasons,
            }
        )

    root_to_lineage = {
        root: f"L{index}"
        for index, root in enumerate(
            sorted(groups, key=lambda item: min(groups[item])),
            start=1,
        )
    }
    verified_pairs: set[tuple[str, str]] = set()
    for left, right in explicit_independence:
        left_root = dsu.find(left)
        right_root = dsu.find(right)
        if left_root == right_root:
            relationship_conflicts.append(
                {
                    "source_ids": sorted([left, right]),
                    "reason": "INDEPENDENT_OF_WITHIN_SAME_LINEAGE",
                }
            )
            continue
        if left_root not in groups or right_root not in groups:
            continue
        pair = tuple(
            sorted(
                [
                    root_to_lineage[left_root],
                    root_to_lineage[right_root],
                ]
            )
        )
        verified_pairs.add(pair)

    lineage_count = len(rendered_groups)
    total_pairs = lineage_count * (lineage_count - 1) // 2
    verified_pair_count = len(verified_pairs)

    if lineage_count <= 1:
        status = "SINGLE_LINEAGE"
    elif verified_pair_count == total_pairs and not relationship_conflicts:
        status = "VERIFIED_INDEPENDENT_LINEAGES"
    elif verified_pair_count > 0:
        status = "PARTIALLY_VERIFIED_INDEPENDENCE"
    else:
        status = "MULTIPLE_LINEAGES_UNVERIFIED"

    raw_urls = {
        str(source_by_id[item].get("canonical_url") or "")
        for item in support_ids
        if source_by_id[item].get("canonical_url")
    }

    strict_confidence_basis = (
        lineage_count
        if status == "VERIFIED_INDEPENDENT_LINEAGES"
        else (1 if support_ids else 0)
    )

    return {
        "status": status,
        "supporting_source_count": len(support_ids),
        "supporting_url_count": len(raw_urls),
        "effective_lineage_count": lineage_count,
        "duplicate_or_dependency_reduction": max(0, len(support_ids) - lineage_count),
        "verified_independent_pair_count": verified_pair_count,
        "total_lineage_pair_count": total_pairs,
        "strict_confidence_basis_source_count": strict_confidence_basis,
        "lineages": rendered_groups,
        "verified_independent_pairs": [
            list(pair) for pair in sorted(verified_pairs)
        ],
        "relationship_conflicts": relationship_conflicts,
        "method": "CONSERVATIVE_LINEAGE_GROUPING",
        "notes": [
            "Different URLs are not proof of source independence.",
            "Same publisher, canonical URL, source family, identical representation hash, "
            "or explicit dependency collapses into one lineage.",
            "Multiple lineages are called verified independent only when every lineage "
            "pair has an explicit INDEPENDENT_OF relationship.",
        ],
    }
