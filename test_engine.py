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
import re as _re
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any


sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# Category tags for reporting
QUERY_CATEGORIES: dict[str, str] = {}

def _tag(cat: str, queries: list[str]) -> list[str]:
    for q in queries:
        QUERY_CATEGORIES[q] = cat
    return queries

DEFAULT_QUERIES = [
    # ── אלקטרוניקה עברית (5) ──────────────────────────────────────────────
    *_tag("אלקטרוניקה", [
        "אוזניות בלוטות",
        "מחשב נייד",
        "טלוויזיה חכמה",
        "מטען אלחוטי",
        "מצלמת אבטחה",
    ]),
    # ── אלקטרוניקה אנגלית (5) ─────────────────────────────────────────────
    *_tag("אלקטרוניקה", [
        "AirPods Pro",
        "Samsung Galaxy S24",
        "laptop i7",
        "Xiaomi smart watch",
        "USB-C charger",
    ]),
    # ── אופנה עברית (5) ───────────────────────────────────────────────────
    *_tag("אופנה", [
        "נעלי נייקי ריצה",
        "ג'ינס סקיני",
        "שמלת קיץ",
        "חולצת טריקו",
        "מעיל חורף",
    ]),
    # ── אופנה אנגלית (5) ──────────────────────────────────────────────────
    *_tag("אופנה", [
        "Adidas Superstar sneakers",
        "slim fit jeans",
        "summer dress",
        "Nike running shoes",
        "leather jacket",
    ]),
    # ── פארם ויופי עברית (5) ──────────────────────────────────────────────
    *_tag("פארם/יופי", [
        "בושם לגבר",
        "שמפו לשיער יבש",
        "קרם פנים לחות",
        "ויטמין C",
        "מברשת שיניים חשמלית",
    ]),
    # ── פארם ויופי אנגלית (5) ─────────────────────────────────────────────
    *_tag("פארם/יופי", [
        "men perfume",
        "face moisturizer",
        "vitamin D supplement",
        "electric toothbrush",
        "hair conditioner",
    ]),
    # ── בית וסופר עברית (5) ───────────────────────────────────────────────
    *_tag("בית/סופר", [
        "מקרר LG",
        "שולחן כתיבה",
        "מכונת קפה",
        "מחבת טפלון",
        "כרית שינה",
    ]),
    # ── בית וסופר אנגלית (5) ──────────────────────────────────────────────
    *_tag("בית/סופר", [
        "IKEA desk chair",
        "coffee maker",
        "non-stick pan",
        "blender",
        "mattress topper",
    ]),
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


_SYNONYMS: dict[str, set[str]] = {
    "נעל": {"נעל", "נעלי", "נעליים", "סניקרס", "מגפיים", "כפכפים", "סנדלים", "shoe", "shoes", "sneaker", "sneakers", "boots", "sandals"},
    "נעלי": {"נעל", "נעלי", "נעליים", "סניקרס", "shoe", "shoes", "sneaker", "sneakers"},
    "נעליים": {"נעל", "נעלי", "נעליים", "סניקרס", "shoe", "shoes", "sneaker", "sneakers"},
    "shoe": {"נעל", "נעלי", "נעליים", "סניקרס", "shoe", "shoes", "sneaker", "sneakers", "boots"},
    "shoes": {"נעל", "נעלי", "נעליים", "סניקרס", "shoe", "shoes", "sneaker", "sneakers", "boots"},
    "running": {"ריצה", "ריצת", "running", "runner", "jogging", "sport", "athletic"},
    "ריצה": {"ריצה", "running", "runner", "jogging", "sport"},
    "חולצה": {"חולצה", "חולצות", "shirt", "shirts", "tee", "t-shirt"},
    "shirt": {"חולצה", "חולצות", "shirt", "shirts", "tee", "t-shirt"},
    "שעון": {"שעון", "שעונים", "watch", "watches", "clock", "smartwatch"},
    "watch": {"שעון", "שעונים", "watch", "watches", "wrist", "smartwatch"},
    "אוזניות": {"אוזניות", "אוזנייה", "headphones", "headphone", "earphones", "earbuds", "headset"},
    "headphones": {"אוזניות", "אוזנייה", "headphones", "headphone", "earphones", "earbuds"},
    "כוס": {"כוס", "כוסות", "cup", "mug", "tumbler", "bottle", "thermos"},
    "תרמית": {"תרמית", "תרמוס", "thermos", "insulated", "vacuum", "tumbler", "thermal"},
    "thermos": {"תרמית", "תרמוס", "thermos", "insulated", "tumbler"},
    "מחשב": {"מחשב", "מחשבים", "computer", "laptop", "pc", "notebook", "desktop"},
    "laptop": {"מחשב", "מחשבים", "laptop", "notebook", "computer", "pc"},
    "מקלדת": {"מקלדת", "מקלדות", "keyboard", "keyboards"},
    "keyboard": {"מקלדת", "מקלדות", "keyboard", "keyboards"},
    "מסך": {"מסך", "מסכים", "monitor", "screen", "display"},
    "monitor": {"מסך", "מסכים", "monitor", "screen", "display"},
    "מצלמה": {"מצלמה", "מצלמות", "camera", "cameras", "webcam"},
    "camera": {"מצלמה", "מצלמות", "camera", "cameras", "webcam"},
    "מטען": {"מטען", "מטענים", "charger", "charging", "adapter", "cable"},
    "charger": {"מטען", "מטענים", "charger", "charging", "adapter"},
    "מזרן": {"מזרן", "מזרנים", "mattress", "mattresses"},
    "mattress": {"מזרן", "מזרנים", "mattress", "mattresses"},
    "מגבת": {"מגבת", "מגבות", "towel", "towels"},
    "towel": {"מגבת", "מגבות", "towel", "towels"},
    "תיק": {"תיק", "תיקים", "bag", "bags", "backpack", "purse"},
    "backpack": {"תיק", "תיקים", "backpack", "bag", "rucksack"},
    "קפה": {"קפה", "coffee", "espresso", "cappuccino"},
    "coffee": {"קפה", "coffee", "espresso"},
    "כביסה": {"כביסה", "washing", "laundry", "washer"},
    "washing": {"כביסה", "washing", "laundry", "washer"},
    "דיסק": {"דיסק", "דיסקים", "disk", "drive", "ssd", "hdd", "storage"},
    "ssd": {"דיסק", "דיסקים", "ssd", "solid", "drive", "storage"},
    "פנס": {"פנס", "פנסים", "flashlight", "torch", "lantern", "light", "lamp"},
    "flashlight": {"פנס", "פנסים", "flashlight", "torch", "light"},
    "עיפרון": {"עיפרון", "עפרון", "עפרונות", "pencil", "pen"},
    "pencil": {"עיפרון", "עפרון", "עפרונות", "pencil", "pen"},
    "מברשת": {"מברשת", "מברשות", "brush", "toothbrush"},
    "toothbrush": {"מברשת", "מברשות", "toothbrush", "brush"},
    "בקבוק": {"בקבוק", "בקבוקים", "bottle", "flask"},
    "bottle": {"בקבוק", "בקבוקים", "bottle", "flask"},
    "מקרר": {"מקרר", "מקררים", "fridge", "refrigerator", "freezer"},
    "fridge": {"מקרר", "מקררים", "fridge", "refrigerator"},
    "עכבר": {"עכבר", "עכברים", "mouse", "mice"},
    "mouse": {"עכבר", "עכברים", "mouse", "mice"},
    "מכנסיים": {"מכנסיים", "מכנס", "pants", "trousers", "jeans", "shorts"},
    "pants": {"מכנסיים", "מכנס", "pants", "trousers", "jeans", "shorts"},
    "מקרר": {"מקרר", "מקררים", "fridge", "refrigerator", "freezer", "cooling"},
    "airpods": {"אוזניות", "headphones", "earbuds", "earphones"},
    "samsung": {"סמסונג", "galaxy", "phone", "smartphone", "טלפון"},
    "xiaomi": {"שיאומי", "smartwatch", "שעון", "watch", "phone"},
    "מטען": {"מטען", "מטענים", "charger", "charging", "wireless", "cable"},
    "מצלמה": {"מצלמה", "מצלמות", "camera", "cameras", "security", "cctv"},
    "שולחן": {"שולחן", "שולחנות", "table", "desk", "writing desk"},
    "כרית": {"כרית", "כריות", "pillow", "cushion", "sleep"},
    "מחבת": {"מחבת", "pan", "frying", "non-stick", "teflon"},
    "בושם": {"בושם", "בשמים", "perfume", "cologne", "fragrance"},
    "שמפו": {"שמפו", "שמפואים", "shampoo", "conditioner", "hair"},
    "קרם": {"קרם", "cream", "moisturizer", "lotion", "face"},
    "ויטמין": {"ויטמין", "vitamin", "supplement", "capsule"},
    "vitamin": {"ויטמין", "vitamin", "supplement", "capsule"},
    "ג'ינס": {"ג'ינס", "ג'ינסים", "jeans", "denim", "pants"},
    "jeans": {"ג'ינס", "ג'ינסים", "jeans", "denim", "pants", "slim"},
    "שמלה": {"שמלה", "שמלות", "dress", "skirt", "gown"},
    "dress": {"שמלה", "שמלות", "dress", "skirt", "summer"},
    "מעיל": {"מעיל", "מעילים", "jacket", "coat", "hoodie", "winter"},
    "jacket": {"מעיל", "מעילים", "jacket", "coat", "leather", "winter"},
    "blender": {"בלנדר", "מיקסר", "blender", "mixer", "smoothie"},
    "pegasus": {"נעל", "נעלי", "ריצה", "shoe", "running", "sneakers"},
    "macbook": {"מחשב", "לפטופ", "laptop", "computer"},
}
_PREFIXES = "לבוכמשהו"


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _query_tokens(query: str) -> set[str]:
    """Extract meaningful tokens from a query (Hebrew + ASCII)."""
    normalized = _normalize(query)
    tokens = set(_re.findall(r"[\u0590-\u05ff]+|[a-zA-Z0-9]{2,}", normalized))
    noise = {"the", "and", "for", "with", "of", "in", "on", "at", "to", "a", "an"}
    return {t for t in tokens if t.lower() not in noise}


def _token_matches_title(token: str, title_tokens: set[str], normalized_title: str) -> bool:
    """Check if token or any synonym matches a word in the title."""
    candidates = {token} | _SYNONYMS.get(token, set())
    for c in candidates:
        c = c.lower()
        is_heb = any("\u0590" <= ch <= "\u05ff" for ch in c)
        if is_heb:
            if c in title_tokens:
                return True
            if any(t.startswith(p) and t[len(p):] == c for t in title_tokens for p in _PREFIXES):
                return True
        else:
            if c in title_tokens:
                return True
            if _re.search(r"(?<!\w)" + _re.escape(c) + r"(?!\w)", normalized_title):
                return True
    return False


def _relevance_score(query: str, title: str) -> float:
    """Return a score between 0.0 and 1.0 based on token overlap with synonym expansion."""
    if not title:
        return 0.0
    q_tokens = _query_tokens(query)
    if not q_tokens:
        return 1.0
    normalized_title = _normalize(title)
    title_tokens = set(_re.findall(r"[\w\u0590-\u05ff'-]{2,}", normalized_title))
    matched = sum(1 for t in q_tokens if _token_matches_title(t, title_tokens, normalized_title))
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
    last_err: str = ""
    for attempt in range(2):
        try:
            resp = await asyncio.wait_for(
                loop.run_in_executor(None, urllib.request.urlopen, req),
                timeout=timeout,
            )
            body = json.loads(resp.read())
            break
        except asyncio.TimeoutError:
            return {"error": "search request timed out", "results": []}
        except Exception as exc:
            last_err = str(exc)
            await asyncio.sleep(2)
    else:
        return {"error": f"search request failed: {last_err}", "results": []}

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
            err_str = str(exc)
            if "404" in err_str:
                # Task lost (server restart). Re-submit and continue polling.
                try:
                    resp2 = await asyncio.wait_for(
                        loop.run_in_executor(None, urllib.request.urlopen, req),
                        timeout=timeout,
                    )
                    body2 = json.loads(resp2.read())
                    task_id = body2.get("task_id", task_id)
                except Exception:
                    pass
                await asyncio.sleep(2)
                continue
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


async def _run_bulk(base_url: str, queries: list[str], limit: int, timeout: float, concurrency: int = 1) -> list[ProductReport]:
    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded(query: str) -> ProductReport:
        async with semaphore:
            result = await _run_query(base_url, query, limit, timeout)
            await asyncio.sleep(1.0)
            return result

    tasks = [_bounded(q) for q in queries]
    return await asyncio.gather(*tasks)


def _print_report(reports: list[ProductReport]) -> None:
    total = len(reports)
    with_results = sum(1 for r in reports if r.got_results)
    with_images = sum(1 for r in reports if r.avg_image_score >= 0.5)
    low_relevance = sum(1 for r in reports if r.relevance_score < 0.3 and r.got_results)

    print("\n" + "=" * 80)
    print("NEXUSAI BULK SEARCH TEST REPORT — 40 QUERIES")
    print("=" * 80)
    print(f"סה\"כ שאילתות: {total}")
    print(f"החזירו תוצאות: {with_results}/{total} ({with_results / total * 100:.1f}%)")
    print(f"עם תמונות:     {with_images}/{total}")
    print(f"רלוונטיות נמוכה: {low_relevance}")
    print("=" * 80)

    # Per-category breakdown
    cats: dict[str, list] = {}
    for r in reports:
        cat = QUERY_CATEGORIES.get(r.query, "אחר")
        cats.setdefault(cat, []).append(r)

    print("\n📊 דוח לפי קטגוריה:")
    print(f"  {'קטגוריה':<15} {'עם תוצאות':>12} {'% הצלחה':>10} {'ממוצע רלוונטיות':>18} {'ממוצע מהירות':>14}")
    print("  " + "-" * 72)
    fastest_cat = None
    fastest_time = float("inf")
    for cat, reps in sorted(cats.items()):
        ok = sum(1 for r in reps if r.got_results)
        pct = ok / len(reps) * 100
        avg_rel = sum(r.relevance_score for r in reps) / len(reps)
        # Extract timing from notes
        times = []
        for r in reps:
            for n in r.notes:
                m = __import__("re").search(r"(\d+\.\d+)s", n)
                if m:
                    times.append(float(m.group(1)))
                    break
        avg_t = sum(times) / len(times) if times else 0
        if avg_t and avg_t < fastest_time and ok > 0:
            fastest_time = avg_t
            fastest_cat = cat
        print(f"  {cat:<15} {ok}/{len(reps):>10} {pct:>9.1f}% {avg_rel:>17.2f} {avg_t:>13.1f}s")
    if fastest_cat:
        print(f"\n  🏆 הקטגוריה המהירה ביותר: {fastest_cat} (ממוצע {fastest_time:.1f}s)")

    print("\n" + "=" * 80)
    print("פירוט לכל שאילתה:")
    for r in reports:
        cat = QUERY_CATEGORIES.get(r.query, "אחר")
        status = "✅" if r.got_results and r.relevance_score >= 0.3 else ("⚠️" if r.got_results else "❌")
        print(
            f"{status} [{cat:<10}] {r.query!r:30s} | results={r.result_count:>2} | "
            f"rel={r.relevance_score:>4.2f} | providers={', '.join(r.providers) or 'none'}"
        )
        if r.sample_titles:
            for title in r.sample_titles[:2]:
                print(f"      → {title[:70]}")
        for note in r.notes:
            print(f"      NOTE: {note}")

    print("\n" + "=" * 80)
    print("⚠️  שאילתות ללא תוצאות / רלוונטיות נמוכה:")
    found_problems = False
    for r in reports:
        if not r.got_results or r.relevance_score < 0.3:
            found_problems = True
            print(f"  - {r.query!r}: results={r.result_count}, relevance={r.relevance_score:.2f}")
    if not found_problems:
        print("  🎉 אין בעיות — כל השאילתות החזירו תוצאות רלוונטיות!")
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
    parser.add_argument("--timeout", type=float, default=35.0, help="Max seconds per query")
    parser.add_argument("--queries", nargs="+", help="Override the default query list")
    parser.add_argument("--concurrency", type=int, default=1, help="Parallel queries (default 1 to avoid Railway overload)")
    args = parser.parse_args()

    queries = args.queries or DEFAULT_QUERIES
    print(f"Running bulk test against {args.url} with {len(queries)} queries...")
    print(f"Concurrency={args.concurrency}, limit={args.limit}, timeout={args.timeout}s")
    reports = asyncio.run(_run_bulk(args.url, queries, args.limit, args.timeout, args.concurrency))
    _print_report(reports)
    _write_json_report(reports)


if __name__ == "__main__":
    main()
