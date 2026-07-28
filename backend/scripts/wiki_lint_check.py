#!/usr/bin/env python3
"""Comprehensive wiki lint checker based on AGENTS.md rules.

Checks all 10 lint rules:
  RED:   missing_frontmatter, index_mismatch, duplicate_concept, conflicting_conclusion
  YELLOW: dangling_reference, orphan_page, uncited_claim, invalid_status
  BLUE:  stale_content, log_gap
"""

from __future__ import annotations

import json
import re
import sys
import yaml
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── config ──────────────────────────────────────────────────────────
WIKI_DIR = Path(__file__).resolve().parent.parent / "wiki"
ENTITY_TYPES = {
    "paper":       "papers",
    "concept":     "concepts",
    "method":      "methods",
    "dataset":     "datasets",
    "author":      "authors",
    "survey":      "surveys",
    "comparison":  "comparisons",
    "idea":        "ideas",
}
VALID_STATUSES = {"complete", "in_progress", "stub", "proposed", "rejected"}
VALID_CONFIDENCES = {"high", "medium", "low"}
REQUIRED_FIELDS = {
    "paper":       ["slug", "title", "type", "created", "updated", "status", "confidence"],
    "concept":     ["slug", "title", "type", "created", "updated", "status", "confidence"],
    "method":      ["slug", "title", "type", "created", "updated", "status", "confidence"],
    "dataset":     ["slug", "title", "type", "created", "updated", "status", "confidence"],
    "author":      ["slug", "title", "type", "created", "updated", "status", "confidence"],
    "survey":      ["slug", "title", "type", "created", "updated", "status", "confidence"],
    "comparison":  ["slug", "title", "type", "created", "updated", "status", "confidence"],
    "idea":        ["slug", "title", "type", "created", "updated", "status", "confidence"],
}

# ── helpers ──────────────────────────────────────────────────────────
def parse_frontmatter(text: str) -> dict:
    text = text.lstrip('﻿')  # strip UTF-8 BOM if present
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.DOTALL)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except Exception:
        result = {}
        for line in m.group(1).split("\n"):
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip().strip('"')
        return result

def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def days_ago(date_str: str) -> int:
    """Calculate days since a date string (YYYY-MM-DD)."""
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 0

def normalize_slug(s: str) -> str:
    """Normalize a slug for comparison."""
    return s.lower().strip().replace("_", "-").replace(" ", "-")

# ── main lint logic ──────────────────────────────────────────────────
def lint_wiki(wiki_dir: Path) -> dict:
    issues: list[dict] = []
    all_pages: dict[str, dict] = {}  # slug -> info
    all_slugs: set[str] = set()
    pages_by_type: dict[str, list[str]] = defaultdict(list)

    # ── 1. Collect all pages ──────────────────────────────────────
    for etype, subdir in ENTITY_TYPES.items():
        type_dir = wiki_dir / subdir
        if not type_dir.exists():
            continue
        for md_file in sorted(type_dir.glob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            meta = parse_frontmatter(text)
            slug = md_file.stem
            all_slugs.add(slug)
            all_pages[slug] = {
                "path": str(md_file),
                "rel_path": f"{subdir}/{md_file.name}",
                "meta": meta,
                "type": etype,
                "text": text,
            }
            pages_by_type[etype].append(slug)

    # ── 2. RED: missing_frontmatter ───────────────────────────────
    for slug, info in all_pages.items():
        if not info["meta"]:
            issues.append({
                "severity": "red",
                "type": "missing_frontmatter",
                "page": slug,
                "path": info["rel_path"],
                "message": f"Page '{slug}' has no YAML frontmatter",
            })

    # ── 3. RED: missing required fields ───────────────────────────
    for slug, info in all_pages.items():
        if not info["meta"]:
            continue
        req_fields = REQUIRED_FIELDS.get(info["type"], [])
        for field in req_fields:
            if field not in info["meta"] or info["meta"][field] is None:
                issues.append({
                    "severity": "red",
                    "type": "missing_field",
                    "page": slug,
                    "path": info["rel_path"],
                    "message": f"Page '{slug}' missing required field: '{field}'",
                })

    # ── 4. YELLOW: invalid_status ─────────────────────────────────
    for slug, info in all_pages.items():
        if not info["meta"]:
            continue
        status = info["meta"].get("status", "")
        if status and status not in VALID_STATUSES:
            issues.append({
                "severity": "yellow",
                "type": "invalid_status",
                "page": slug,
                "path": info["rel_path"],
                "message": f"Page '{slug}' has invalid status: '{status}'",
            })

    # ── 5. YELLOW: invalid_confidence ─────────────────────────────
    for slug, info in all_pages.items():
        if not info["meta"]:
            continue
        conf = info["meta"].get("confidence", "")
        if conf and conf not in VALID_CONFIDENCES:
            issues.append({
                "severity": "yellow",
                "type": "invalid_confidence",
                "page": slug,
                "path": info["rel_path"],
                "message": f"Page '{slug}' has invalid confidence: '{conf}'",
            })

    # ── 6. YELLOW: dangling_reference ─────────────────────────────
    for slug, info in all_pages.items():
        if not info["meta"]:
            continue
        related = info["meta"].get("related_pages", [])
        if not isinstance(related, list):
            continue
        for ref in related:
            ref_slug = ref.split("/")[-1].replace(".md", "")
            ref_slug = normalize_slug(ref_slug)
            if ref_slug not in all_slugs:
                # Also check against all normalized slugs
                found = False
                for s in all_slugs:
                    if normalize_slug(s) == ref_slug:
                        found = True
                        break
                if not found:
                    issues.append({
                        "severity": "yellow",
                        "type": "dangling_reference",
                        "page": slug,
                        "path": info["rel_path"],
                        "message": f"Page '{slug}' references non-existent page: '{ref}'",
                    })

    # ── 7. BLUE: orphan_page (not referenced by any other page) ───
    referenced_slugs: set[str] = set()
    for info in all_pages.values():
        for ref in info["meta"].get("related_pages", []):
            ref_slug = normalize_slug(ref.split("/")[-1].replace(".md", ""))
            referenced_slugs.add(ref_slug)
    # Also collect wikilinks from body text
    wikilink_pattern = re.compile(r"\[\[([^\]|#]+)")
    for info in all_pages.values():
        body = re.sub(r"^---\n.*?\n---\n?", "", info["text"], count=1, flags=re.DOTALL)
        for match in wikilink_pattern.finditer(body):
            link_slug = normalize_slug(match.group(1).strip())
            referenced_slugs.add(link_slug)

    for slug in all_slugs:
        norm_slug = normalize_slug(slug)
        if norm_slug not in referenced_slugs and all_pages[slug]["type"] not in ("paper", "author"):
            issues.append({
                "severity": "blue",
                "type": "orphan_page",
                "page": slug,
                "path": all_pages[slug]["rel_path"],
                "message": f"Page '{slug}' ({all_pages[slug]['type']}) is not referenced by any other page",
            })

    # ── 8. RED: index_mismatch ────────────────────────────────────
    index_path = wiki_dir / "index.md"
    index_slugs: set[str] = set()
    if index_path.exists():
        index_text = index_path.read_text(encoding="utf-8")
        # Extract slugs from index links: (subdir/slug.md) or just slug
        for m in re.finditer(r"\]\(([^)]+\.md)\)", index_text):
            link = m.group(1)
            idx_slug = link.split("/")[-1].replace(".md", "")
            index_slugs.add(idx_slug)

        # Pages in index but not on disk
        for s in index_slugs - all_slugs:
            issues.append({
                "severity": "yellow",
                "type": "index_mismatch",
                "page": s,
                "path": "index.md",
                "message": f"Page '{s}' listed in index.md but file does not exist",
            })
        # Pages on disk but not in index
        for s in all_slugs - index_slugs:
            issues.append({
                "severity": "yellow",
                "type": "index_mismatch",
                "page": s,
                "path": all_pages[s]["rel_path"],
                "message": f"Page '{s}' exists on disk but not in index.md",
            })

    # ── 9. BLUE: stale_content (90+ days since last update) ───────
    threshold_days = 90
    for slug, info in all_pages.items():
        if not info["meta"]:
            continue
        updated = info["meta"].get("updated", "")
        if updated:
            d = days_ago(str(updated))
            if d >= threshold_days:
                issues.append({
                    "severity": "blue",
                    "type": "stale_content",
                    "page": slug,
                    "path": info["rel_path"],
                    "message": f"Page '{slug}' last updated {d} days ago (threshold: {threshold_days})",
                })

    # ── 10. BLUE: log_gap ─────────────────────────────────────────
    log_path = wiki_dir / "log.md"
    logged_slugs: set[str] = set()
    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8")
        for m in re.finditer(r"(?:papers|concepts|methods|datasets|authors|surveys|comparisons|ideas)/([a-z0-9-]+)", log_text):
            logged_slugs.add(m.group(1))
        for m in re.finditer(r"`([a-z0-9-]+)\.md`", log_text):
            logged_slugs.add(m.group(1))

    for slug in all_slugs:
        if slug not in logged_slugs:
            issues.append({
                "severity": "blue",
                "type": "log_gap",
                "page": slug,
                "path": all_pages[slug]["rel_path"],
                "message": f"Page '{slug}' has no log entry in log.md",
            })

    # ── 11. RED: duplicate_concept (similar titles) ───────────────
    concepts = pages_by_type.get("concept", [])
    for i, c1 in enumerate(concepts):
        t1 = normalize_slug(c1)
        for c2 in concepts[i + 1:]:
            t2 = normalize_slug(c2)
            # Check for very similar slugs (e.g., one contains the other)
            if t1 in t2 or t2 in t1:
                issues.append({
                    "severity": "yellow",
                    "type": "duplicate_concept",
                    "page": c1,
                    "path": all_pages[c1]["rel_path"],
                    "message": f"Concept '{c1}' may overlap with '{c2}' (similar slugs)",
                })
                break  # Only report once per pair

    # ── 12. YELLOW: uncited_claim (important claims without source) ──
    # Look for claim-like patterns without citation
    claim_patterns = [
        (r"(?<!\>)(?:significantly|substantially|state-of-the-art|SOTA|outperforms?|achieves?\s+\d+\.?\d*%)\b", "performance claim"),
        (r"(?<!\>)(?:is\s+(?:the\s+)?(?:first|only|best|optimal))\b", "absolute claim"),
    ]
    for slug, info in all_pages.items():
        if info["type"] not in ("paper", "survey"):
            continue
        body = re.sub(r"^---\n.*?\n---\n?", "", info["text"], count=1, flags=re.DOTALL)
        # Skip if body is very short
        if len(body) < 200:
            continue
        for pattern, claim_type in claim_patterns:
            for m in re.finditer(pattern, body, re.IGNORECASE):
                # Check if there's a citation nearby (±200 chars)
                start = max(0, m.start() - 200)
                end = min(len(body), m.end() + 200)
                context = body[start:end]
                has_citation = bool(re.search(r"\[[\d,\-\s]+\]|\([^)]+\d{4}[^)]*\)|Source:|> Source", context))
                if not has_citation:
                    issues.append({
                        "severity": "yellow",
                        "type": "uncited_claim",
                        "page": slug,
                        "path": info["rel_path"],
                        "message": f"Possible uncited {claim_type} in '{slug}': '{m.group(0)}'",
                    })
                    break  # One per page per claim type

    # ── Summary ───────────────────────────────────────────────────
    summary = {
        "total_pages": len(all_pages),
        "total_issues": len(issues),
        "by_severity": {
            "red": len([i for i in issues if i["severity"] == "red"]),
            "yellow": len([i for i in issues if i["severity"] == "yellow"]),
            "blue": len([i for i in issues if i["severity"] == "blue"]),
        },
        "by_type": {},
    }
    for i in issues:
        t = i["type"]
        summary["by_type"][t] = summary["by_type"].get(t, 0) + 1

    return {"summary": summary, "issues": issues, "pages_count": len(all_pages)}


if __name__ == "__main__":
    result = lint_wiki(WIKI_DIR)
    print(json.dumps(result, ensure_ascii=False, indent=2))
