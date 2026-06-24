"""Bulk integration test engine for NexusAI search quality.

Run this script against a local or remote instance of the FastAPI server.

Example:
    python test_engine.py
    python test_engine.py --url https://nexusai-backend-production-b8c5.up.railway.app

The report is printed to the terminal and also written to test_engine_report.json.
"""

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any


sys.stdout.reconfigure(encoding="utf-8", errors="replace")


DEFAULT_QUERIES = [
    # Hebrew
    "פנס ראש",
    "נעלי אדידס",
    "עיפרון",
    "מכונת כביסה",
    "מקלדת bluetooth",
    "אוזניות",
    "חולצה",
    "מכונת קפה",
    "מטען לאייפון",
    "שעון יד",
    "מזרן",
    "מחשב נייד",
    "מסך מחשב",
    "דיסק קשיח",
    "מצלמה",
    "מסרק חשמלי",
    "מגבת",
    "כוס תרמית",
    # English
    "iphone",
    "t-shirt",
    "headphones",
    "wireless mouse",
    "sneakers",
    "laptop",
    "running shoes",
    "coffee maker",
    "backpack",
    "blender",
    "watch",
    "keyboard",
    "monitor",
    "SSD",
    "camera",
    "adidas shoes",
    "flashlight",
    "pencil",
    "toothbrush",
    "water bottle",
]


@dataclass
class ProductReport:
    query: str
    got_results: bool
    result_count: int
    providers: list[str] = field(default_factory=list)
    providers_with_images: dict[str, int] = field(default_factory=dict)
    relevance_score: float = 0.0
    avg_image_score: float = 0.0
    sample_titles: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _query_tokens(query: str) -> set[str]:
    """Extract meaningful tokens from a query (Hebrew + ASCII)."""
    import re
    normalized = _normalize(query)
    # Keep Hebrew words and ASCII words; ignore very short tokens
    tokens = set(re.findall(r"[\u0590-\u05ff]+|[a-zA-Z0-9]{2,}", normalized))
    # Remove common noise words
    noise = {"the", "and", "for", "with", "of", "in", "on", "at", "to", "a", "an"}
    return {t for t in tokens if t.lower() not in noise}


def _relevance_score(query: str, title: str) -> float:
    """Return a score between 0.0 and 1.0 based on token overlap."""
    import re
    if not title:
        return 0.0
    q_tokens = _query_tokens(query)
    if not q_tokens:
        return 1.0
    normalized_title = _normalize(title)
    title_tokens = set(re.findall(r"[\w\u0590-\u05ff'-]{2,}", normalized_title))
    matched = 0
    for t in q_tokens:
        is_hebrew = any("\u0590" <= c <= "\u05ff" for c in t)
        if is_hebrew:
            prefixes = "לבוכמשהו"
            if t in title_tokens or any(tok.startswith(p) and tok[len(p):] == t for tok in title_tokens for p in prefixes):
                matched += 1
        else:
            if t in title_tokens or t in normalized_title:
                matched += 1
    return matched / len(q_tokens)


def _image_score(image_url: str | None) -> int:
    if not image_url:
        return 0
    url = image_url.lower()
    if any(p in url for p in ("placeholder", "spinner", "blank", "loading", "default", "nophoto", "noimage")):
        return 0
    if url.startswith("http") or url.startswith("//"):
        return 1
    return 0


async def _search_one(base_url: str, query: str, limit: int, timeout: float) -> dict[str, Any]:
    """Submit a search and poll until completion."""
    data = json.dumps({"query": query, "limit": limit}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/amazon/search",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    loop = asyncio.get_event_loop()
    try:
        resp = await asyncio.wait_for(
            loop.run_in_executor(None, urllib.request.urlopen, req),
            timeout=timeout,
        )
        body = json.loads(resp.read())
    except asyncio.TimeoutError:
        return {"error": "search request timed out", "results": []}
    except Exception as exc:
        return {"error": f"search request failed: {exc}", "results": []}

    task_id = body.get("task_id")
    if not task_id:
        return {"error": "no task_id in response", "results": []}

    deadline = time.time() + timeout
    while time.time() < deadline:
        await asyncio.sleep(1)
        status_req = urllib.request.Request(f"{base_url}/amazon/status/{task_id}")
        try:
            status_resp = await asyncio.wait_for(
                loop.run_in_executor(None, urllib.request.urlopen, status_req),
                timeout=15,
            )
            status_body = json.loads(status_resp.read())
        except asyncio.TimeoutError:
            continue
        except Exception as exc:
            return {"error": f"status poll failed: {exc}", "results": []}

        if status_body.get("status") == "completed":
            return status_body

    return {"error": f"status poll timed out after {timeout}s", "results": []}


async def _run_query(base_url: str, query: str, limit: int, timeout: float) -> ProductReport:
    """Run a single query and build a report entry."""
    start = time.time()
    response = await _search_one(base_url, query, limit, timeout)
    elapsed = time.time() - start

    results = response.get("results") or []
    error = response.get("error")
    report = ProductReport(
        query=query,
        got_results=bool(results),
        result_count=len(results),
    )

    if error:
        report.notes.append(error)

    if not results:
        report.notes.append(f"completed in {elapsed:.1f}s with no results")
        return report

    providers: set[str] = set()
    provider_counts: dict[str, int] = {}
    provider_images: dict[str, int] = {}
    total_relevance = 0.0
    total_image_score = 0
    samples: list[str] = []

    for result in results:
        site = result.get("site", "unknown")
        title = result.get("title", "")
        image = result.get("image")

        providers.add(site)
        provider_counts[site] = provider_counts.get(site, 0) + 1
        provider_images[site] = provider_images.get(site, 0) + _image_score(image)

        total_relevance += _relevance_score(query, title)
        total_image_score += _image_score(image)
        if len(samples) < 5:
            samples.append(title)

    report.providers = sorted(providers)
    report.providers_with_images = {
        site: provider_images[site]
        for site in sorted(providers)
    }
    report.relevance_score = round(total_relevance / len(results), 2)
    report.avg_image_score = round(total_image_score / len(results), 2)
    report.sample_titles = samples
    report.notes.append(f"completed in {elapsed:.1f}s")

    # Flag suspicious low relevance
    if report.relevance_score < 0.3 and report.result_count > 0:
        report.notes.append("LOW RELEVANCE: titles may be unrelated to query")

    return report


async def _run_bulk(base_url: str, queries: list[str], limit: int, timeout: float) -> list[ProductReport]:
    semaphore = asyncio.Semaphore(2)

    async def _bounded(query: str) -> ProductReport:
        async with semaphore:
            return await _run_query(base_url, query, limit, timeout)

    tasks = [_bounded(q) for q in queries]
    return await asyncio.gather(*tasks)


def _print_report(reports: list[ProductReport]) -> None:
    total = len(reports)
    with_results = sum(1 for r in reports if r.got_results)
    with_images = sum(1 for r in reports if r.avg_image_score >= 0.5)
    low_relevance = sum(1 for r in reports if r.relevance_score < 0.3 and r.got_results)

    print("\n" + "=" * 80)
    print("NEXUSAI BULK SEARCH TEST REPORT")
    print("=" * 80)
    print(f"Total queries: {total}")
    print(f"Queries with results: {with_results} ({with_results / total * 100:.1f}%)")
    print(f"Queries with images: {with_images} ({with_images / total * 100:.1f}%)")
    print(f"Queries with low relevance: {low_relevance}")
    print("=" * 80)

    print("\nPer-query breakdown:")
    for r in reports:
        status = "✅" if r.got_results and r.relevance_score >= 0.3 else ("⚠️" if r.got_results else "❌")
        print(
            f"{status} {r.query!r:25s} | results={r.result_count:>2} | "
            f"relevance={r.relevance_score:>4.2f} | images={r.avg_image_score:>4.2f} | "
            f"providers={', '.join(r.providers) or 'none'}"
        )
        if r.sample_titles:
            for title in r.sample_titles:
                print(f"      → {title[:70]}")
        for note in r.notes:
            print(f"      NOTE: {note}")

    print("\n" + "=" * 80)
    print("Problematic queries (empty or low relevance):")
    for r in reports:
        if not r.got_results or r.relevance_score < 0.3:
            print(f"  - {r.query!r}: results={r.result_count}, relevance={r.relevance_score:.2f}")
    print("=" * 80 + "\n")


def _write_json_report(reports: list[ProductReport], path: str = "test_engine_report.json") -> None:
    data = [asdict(r) for r in reports]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Full JSON report written to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="NexusAI search quality test engine")
    parser.add_argument("--url", default="https://nexusai-backend-production-b8c5.up.railway.app", help="Base URL of the server")
    parser.add_argument("--limit", type=int, default=5, help="Results per provider")
    parser.add_argument("--timeout", type=float, default=25.0, help="Max seconds per query")
    parser.add_argument("--queries", nargs="+", help="Override the default query list")
    args = parser.parse_args()

    queries = args.queries or DEFAULT_QUERIES
    print(f"Running bulk test against {args.url} with {len(queries)} queries...")
    print(f"Concurrency=2, limit={args.limit}, timeout={args.timeout}s")
    reports = asyncio.run(_run_bulk(args.url, queries, args.limit, args.timeout))
    _print_report(reports)
    _write_json_report(reports)


if __name__ == "__main__":
    main()
