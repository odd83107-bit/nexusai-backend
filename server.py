import asyncio
import builtins
import html
import json
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests
import urllib3
from cachetools import TTLCache
from deep_translator import GoogleTranslator
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator

from agent import PROVIDER_NAMES, AmazonAgent, ProductResult, VariationResult, _detect_intent, _has_negative_match

BUILD_TAG = "2026-06-26-shopping-graph-il"

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_original_print = builtins.print


def print(*args: Any, **kwargs: Any) -> None:
    safe_args = [
        str(arg).encode("utf-8", errors="ignore").decode("utf-8", errors="replace")
        for arg in args
    ]
    try:
        _original_print(*safe_args, **kwargs)
    except UnicodeEncodeError:
        fallback_args = [
            str(arg).encode("ascii", errors="backslashreplace").decode("ascii")
            for arg in safe_args
        ]
        try:
            _original_print(*fallback_args, **kwargs)
        except Exception:
            return

FAST_SEARCH_LIMIT_PER_SITE = 3
SEARCH_CACHE_TTL_SECONDS = 60 * 60
SEARCH_CACHE_PATH = Path("search_cache.json")
INLINE_SEARCH_TIMEOUT_SECONDS = 5.5
PER_PROVIDER_TIMEOUT_SECONDS = 8.0
AMAZON_PROVIDER_TIMEOUT_SECONDS = 15.0
SEARCH_MEMORY_CACHE = TTLCache(maxsize=1000, ttl=7200)
HTTP_PROVIDER_SITES = (
    "nike",
    "adidas",
    "super_pharm",
    "ksp",
    "machsanei_hashmal",
    "max_stock",
    "zol_stock",
    "ikea",
    "ivory",
    "terminal_x",
    "be_pharm",
    "shufersal",
    "shein",
)

# Fashion sites handle English queries better; Israeli retail sites need Hebrew translation.
FASHION_HTTP_SITES = {"nike", "adidas", "terminal_x", "shein"}


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="before")
    @classmethod
    def normalize_lovable_payload(cls, data):
        if isinstance(data, str):
            return {"query": data}
        if not isinstance(data, dict):
            return data

        if "query" in data:
            return data

        for key in ("productName", "product_name", "product", "name", "search", "text", "message"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return {**data, "query": value}

        return data


class DetailsRequest(BaseModel):
    nexus_id: str = Field(min_length=1)


class VariationResponse(BaseModel):
    type: str
    label: str
    in_stock: bool
    price: str | None


class ProductResponse(BaseModel):
    nexus_id: str
    site: str
    provider_name: str
    title: str
    price: str | None
    image: str | None


class SearchResponse(BaseModel):
    results: list[ProductResponse]


class SearchTaskResponse(BaseModel):
    task_id: str
    status: str


class SearchStatusResponse(BaseModel):
    task_id: str
    status: str
    results: list[ProductResponse] | None = None
    error: str | None = None


class DetailsResponse(ProductResponse):
    variations: list[VariationResponse]


agent = AmazonAgent()
product_registry: dict[str, ProductResult] = {}
search_tasks: dict[str, dict[str, Any]] = {}
agent_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await agent.start()
    yield
    await agent.stop()


app = FastAPI(title="Autonomous Shopping Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_private_network_headers(request, call_next):
    if request.method == "OPTIONS":
        response = Response(status_code=200)
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        return response
    response = await call_next(request)
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "build": BUILD_TAG, "shopping_graph_il": str("shopping_graph_il" in PROVIDER_NAMES)}


@app.get("/debug/fetch-test", response_class=HTMLResponse)
async def debug_fetch_test() -> str:
    return """
<!doctype html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8" />
  <title>NexusAI Fetch Test</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 900px; margin: 40px auto; line-height: 1.5; }
    button { padding: 10px 16px; margin: 8px 0; cursor: pointer; }
    pre { background: #111827; color: #e5e7eb; padding: 16px; border-radius: 8px; white-space: pre-wrap; direction: ltr; text-align: left; }
    input { padding: 10px; min-width: 280px; }
  </style>
</head>
<body>
  <h1>NexusAI Local Fetch Test</h1>
  <p>אם הבדיקה הזו עובדת אבל Lovable עדיין מציג Failed to fetch, הבעיה היא בקוד/סביבת Lovable ולא בשרת המקומי.</p>
  <input id="query" value="test" />
  <button onclick="runSearch()">בדיקת חיפוש</button>
  <button onclick="runHealth()">בדיקת Health</button>
  <pre id="output">מוכן לבדיקה...</pre>
  <script>
    const output = document.getElementById("output");
    const show = (value) => output.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);

    async function runHealth() {
      try {
        const res = await fetch("http://127.0.0.1:8000/health");
        show({ status: res.status, headers: Object.fromEntries(res.headers.entries()), body: await res.json() });
      } catch (error) {
        show("HEALTH FAILED: " + (error && error.message ? error.message : error));
      }
    }

    async function runSearch() {
      try {
        const query = document.getElementById("query").value || "test";
        const res = await fetch("http://127.0.0.1:8000/amazon/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query, limit: 20 })
        });
        show({ status: res.status, headers: Object.fromEntries(res.headers.entries()), body: await res.json() });
      } catch (error) {
        show("SEARCH FAILED: " + (error && error.message ? error.message : error));
      }
    }
  </script>
</body>
</html>
"""


@app.post("/amazon/open")
async def open_amazon() -> dict[str, str]:
    try:
        url = await agent.open_amazon()
        return {"url": url}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/amazon/search", response_model=SearchTaskResponse)
async def search_amazon(request: SearchRequest, http_request: Request) -> SearchTaskResponse:
    print(
        f"[Search] Incoming headers user_agent={http_request.headers.get('user-agent')!r} "
        f"origin={http_request.headers.get('origin')!r} referer={http_request.headers.get('referer')!r}",
        flush=True,
    )
    task_id = uuid.uuid4().hex
    cache_key = _search_cache_key(request.query, min(request.limit, FAST_SEARCH_LIMIT_PER_SITE))
    cached_results = _read_cached_search(cache_key)
    if cached_results is not None:
        print(f"[Search] method=Cache query='{request.query}' results={len(cached_results)}", flush=True)
        _restore_cached_products(cache_key)
        search_tasks[task_id] = {"status": "completed", "results": cached_results, "error": None}
        return SearchTaskResponse(task_id=task_id, status="completed")

    search_tasks[task_id] = {"status": "pending", "results": None, "error": None}
    task = asyncio.create_task(_run_search_task(task_id, request.query, min(request.limit, FAST_SEARCH_LIMIT_PER_SITE), cache_key))
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=INLINE_SEARCH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return SearchTaskResponse(task_id=task_id, status=search_tasks[task_id]["status"])

    return SearchTaskResponse(task_id=task_id, status=search_tasks[task_id]["status"])


@app.get("/amazon/status/{task_id}", response_model=SearchStatusResponse)
async def amazon_search_status(task_id: str) -> SearchStatusResponse:
    task = search_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Unknown task_id.")

    return SearchStatusResponse(
        task_id=task_id,
        status=task["status"],
        results=task["results"],
        error=task["error"],
    )


@app.post("/amazon/details", response_model=DetailsResponse)
async def amazon_details(request: DetailsRequest, http_request: Request) -> DetailsResponse:
    print(
        f"[Details] Incoming headers user_agent={http_request.headers.get('user-agent')!r} "
        f"origin={http_request.headers.get('origin')!r} referer={http_request.headers.get('referer')!r} "
        f"ngrok_skip={http_request.headers.get('ngrok-skip-browser-warning')!r}",
        flush=True,
    )
    received_nexus_id = request.nexus_id
    print(f"[Details] Received nexus_id repr={received_nexus_id!r}", flush=True)
    print(f"[Details] Registry size={len(product_registry)} contains={received_nexus_id in product_registry}", flush=True)
    result = product_registry.get(received_nexus_id)
    if result is None:
        result = _restore_product_by_nexus_id(received_nexus_id)
    if result is None or not result.url:
        print(f"[Details] Registry miss for nexus_id repr={received_nexus_id!r}", flush=True)
        print(f"[Details] Known registry ids={list(product_registry.keys())[:10]!r}", flush=True)
        raise HTTPException(status_code=404, detail="Unknown or expired nexus_id. Search again before requesting details.")

    print(f"[Details] Matched nexus_id repr={result.nexus_id!r}", flush=True)
    print(f"[Details] Registry URL={result.url}", flush=True)
    try:
        if result.site == "aliexpress":
            variations = await agent.get_aliexpress_variations(result.url)
        elif result.site == "next":
            variations = await agent.get_next_variations(result.url)
        elif result.site == "amazon":
            variations = await agent.get_variations(result.url)
        elif result.site in HTTP_PROVIDER_SITES:
            variations = await agent.get_http_provider_variations(result.site, result.url)
        else:
            variations = []
        result.variations = variations
        response = _to_details_response(result, "en")
        print(f"[Details] Response JSON={response.model_dump_json().encode('ascii', errors='backslashreplace').decode('ascii')}", flush=True)
        return response
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _to_response(result: ProductResult, source_language: str) -> ProductResponse:
    title = _translate_text(result.title, source="en", target=source_language) if source_language != "en" else result.title
    return ProductResponse(
        nexus_id=result.nexus_id,
        site=result.site,
        provider_name=result.provider_name or PROVIDER_NAMES.get(result.site, result.site),
        title=title,
        price=result.price,
        image=result.image,
    )


HEBREW_TO_ENGLISH_FALLBACK = {
    "אוזניות": "headphones",
    "מחשב נייד": "laptop",
    "מכונת כביסה": "washing machine",
    "שעון יד": "watch",
    "מקלדת bluetooth": "bluetooth keyboard",
    "מקלדת בלוטות'": "bluetooth keyboard",
    "חולצה": "t-shirt",
    "נעלי אדידס": "adidas",
    "נעלי ריצה": "running shoes",
    "פנס ראש": "headlamp",
    "מצלמה": "camera",
    "מסך מחשב": "computer monitor",
    "מסרק חשמלי": "electric comb",
    "מגבת": "towel",
    "כוס תרמית": "thermos",
    "מטען לאייפון": "iphone charger",
    "מכונת קפה": "coffee machine",
    "דיסק קשיח": "external hard drive",
    "מזרן": "mattress",
    "עיפרון": "pencil",
    "מברשת שיניים חשמלית": "electric toothbrush",
    "מברשת שיניים": "toothbrush",
    "בקבוק מים": "water bottle",
    "נעלי ריצה": "running shoes",
    "סניקרס": "sneakers",
    "אייפון": "iphone",
}

def _search_query_for_amazon(query: str, source_language: str) -> str:
    normalized = query.strip().lower()
    overrides = {
        "מגף אוסטרלי": "Australian boot Blundstone",
        "מגפיים אוסטרליות": "Australian boots Blundstone",
        "מגפיים אוסטרליים": "Australian boots Blundstone",
    }
    if normalized in overrides:
        return overrides[normalized]
    if source_language == "en":
        return query
    if normalized in HEBREW_TO_ENGLISH_FALLBACK:
        return HEBREW_TO_ENGLISH_FALLBACK[normalized]
    translated = _translate_text(query, source=source_language, target="en")
    return translated if translated.strip() else query


async def _noop() -> list[ProductResult]:
    return []


async def _run_with_timeout(name: str, coro, timeout: float) -> list[ProductResult]:
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        print(f"[{name}] Completed results={len(result)}", flush=True)
        return result
    except asyncio.TimeoutError:
        print(f"[{name}] Timeout after {timeout}s", flush=True)
        return []
    except Exception as exc:
        print(f"[{name}] Failed: {exc}", flush=True)
        return []


async def _run_search_task(task_id: str, query: str, limit: int, cache_key: str) -> None:
    search_tasks[task_id]["status"] = "running"
    try:
        source_language = _detect_source_language(query)
        amazon_query = _search_query_for_amazon(query, source_language)
        hebrew_query = _translate_text(query, source=source_language, target="iw") if source_language == "en" else query
        print(f"[Search] Original query='{query}' en='{amazon_query}' he='{hebrew_query}'", flush=True)
        per_site_limit = FAST_SEARCH_LIMIT_PER_SITE
        intent = _detect_intent(query) or _detect_intent(amazon_query)
        skip_providers: set[str] = intent["skip_providers"] if intent else set()
        if intent:
            print(f"[Intent] Detected intent={intent['name']!r} skip={skip_providers}", flush=True)
        shopping_il_query = hebrew_query if hebrew_query else amazon_query

        def _http_query_for_site(site: str) -> str:
            # Fashion sites work best with English queries, even when the user typed in Hebrew.
            if site in FASHION_HTTP_SITES:
                return amazon_query if source_language == "iw" else query
            return hebrew_query

        provider_tasks = [
            _run_with_timeout("amazon", _fast_search_amazon(amazon_query, per_site_limit), AMAZON_PROVIDER_TIMEOUT_SECONDS),
            _run_with_timeout("temu", _fast_search_temu(amazon_query, per_site_limit), PER_PROVIDER_TIMEOUT_SECONDS),
            _run_with_timeout("aliexpress", _fast_search_aliexpress(amazon_query, per_site_limit), PER_PROVIDER_TIMEOUT_SECONDS),
            _run_with_timeout("next", _fast_search_next(hebrew_query, per_site_limit) if "next" not in skip_providers else _noop(), PER_PROVIDER_TIMEOUT_SECONDS),
            _run_with_timeout("shopping_graph_il", _fast_search_shopping_graph_il(shopping_il_query, per_site_limit), PER_PROVIDER_TIMEOUT_SECONDS),
            *[
                _run_with_timeout(
                    site,
                    _fast_search_http_provider(site, _http_query_for_site(site), per_site_limit) if site not in skip_providers else _noop(),
                    PER_PROVIDER_TIMEOUT_SECONDS,
                )
                for site in HTTP_PROVIDER_SITES
            ],
        ]
        site_results = await asyncio.gather(*provider_tasks)
        results: list[ProductResult] = []
        counts: dict[str, int] = {}
        sites = ["amazon", "temu", "aliexpress", "next", "shopping_graph_il", *HTTP_PROVIDER_SITES]
        for index, site in enumerate(sites):
            provider_result = site_results[index]
            if not isinstance(provider_result, list):
                counts[site] = 0
                continue
            relevant_results = []
            for result in provider_result:
                if result.site != site:
                    continue
                if _has_negative_match(query, result.title) or _has_negative_match(amazon_query, result.title):
                    print(f"[Filter] Negative match dropped from {site}: {result.title!r}", flush=True)
                    continue
                if not (AmazonAgent.is_title_relevant(query, result.title)
                        or AmazonAgent.is_title_relevant(hebrew_query, result.title)
                        or AmazonAgent.is_title_relevant(amazon_query, result.title)):
                    print(f"[Filter] Dropped unrelated item from {site}: {result.title!r}", flush=True)
                    continue
                print(f"[Filter] Kept {result.title!r} for query {query!r}", flush=True)
                relevant_results.append(result)
            print(f"[Search Summary] {site}: raw={len(provider_result)} filtered={len(relevant_results)}", flush=True)
            capped = relevant_results[:per_site_limit]
            counts[site] = len(capped)
            results.extend(capped)
        print(
            f"[Search] Aggregated {counts} total={len(results)}",
            flush=True,
        )
        _store_products(results)
        response_results = [_to_response(result, source_language) for result in results]
        search_tasks[task_id]["results"] = response_results
        if response_results:
            _write_cached_search(cache_key, response_results, results)
        search_tasks[task_id]["status"] = "completed"
    except Exception as exc:
        search_tasks[task_id]["error"] = str(exc)
        search_tasks[task_id]["status"] = "failed"


async def _fast_search_temu(query: str, limit: int) -> list[ProductResult]:
    print(f"[Temu Search] Wrapper invoked query={query!r} limit={limit}", flush=True)
    try:
        return await agent.fast_search_temu(query=query, limit=limit)
    except Exception as exc:
        print(f"[Temu Search] Wrapper failed: {exc}", flush=True)
        return []


async def _fast_search_aliexpress(query: str, limit: int) -> list[ProductResult]:
    try:
        async with agent_lock:
            return await agent.fast_search_aliexpress(query=query, limit=limit)
    except Exception as exc:
        print(f"[AliExpress Search] Failed: {exc}", flush=True)
        return []


async def _fast_search_next(query: str, limit: int) -> list[ProductResult]:
    try:
        return await agent.fast_search_next(query=query, limit=limit)
    except Exception as exc:
        print(f"[Next Search] Failed: {exc}", flush=True)
        return []


async def _fast_search_http_provider(site: str, query: str, limit: int) -> list[ProductResult]:
    try:
        return await agent.fast_search_http_provider(site=site, query=query, limit=limit)
    except Exception as exc:
        print(f"[{site} Search] Failed: {exc}", flush=True)
        return []


async def _fast_search_shopping_graph_il(query: str, limit: int) -> list[ProductResult]:
    try:
        return await agent.fast_search_shopping_graph_il(query=query, limit=limit)
    except Exception as exc:
        print(f"[ShoppingIL] Wrapper failed: {exc}", flush=True)
        return []


async def _fast_search_amazon(query: str, limit: int) -> list[ProductResult]:
    print(f"[Amazon Search] Starting query={query!r} limit={limit}", flush=True)
    try:
        http_results = await asyncio.to_thread(_fast_search_amazon_http, query, limit)
        print(f"[Amazon Search] HTTP returned {len(http_results)} results", flush=True)
        if http_results:
            print("[Amazon Search] Using HTTP results", flush=True)
            return http_results
        print("[Amazon Search] HTTP returned empty, falling back to SerpAPI", flush=True)
    except Exception as exc:
        print(f"[Amazon Search] HTTP failed: {exc}", flush=True)

    print("[Amazon Search] SerpAPI fallback", flush=True)
    async with agent_lock:
        serpapi_results = await agent.fast_search_amazon_serpapi(query=query, limit=limit)
    print(f"[Amazon Search] SerpAPI returned {len(serpapi_results)} results", flush=True)
    if serpapi_results:
        return serpapi_results

    print("[Amazon Search] Playwright fallback", flush=True)
    async with agent_lock:
        playwright_results = await agent.fast_search(query=query, limit=limit)
    print(f"[Amazon Search] Playwright returned {len(playwright_results)} results", flush=True)
    return playwright_results


def _fast_search_amazon_http(query: str, limit: int) -> list[ProductResult]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    url = f"https://www.amazon.com/s?k={quote_plus(query)}"
    print(f"[Amazon HTTP] Requesting {url}", flush=True)
    response = requests.get(
        url,
        headers=headers,
        timeout=3,
        verify=False,
    )
    print(f"[Amazon HTTP] status={response.status_code} cards={len(re.findall(r'<div[^>]+data-component-type="s-search-result"', response.text))} len={len(response.text)}", flush=True)
    response.raise_for_status()

    results: list[ProductResult] = []
    cards = re.findall(
        r'<div[^>]+data-component-type="s-search-result"[\s\S]*?</div>\s*</div>\s*</div>\s*</div>',
        response.text,
    )
    for card in cards:
        title_match = re.search(r'<h2[\s\S]*?<span[^>]*>(.*?)</span>', card)
        href_match = re.search(r'<a[^>]+class="[^"]*a-link-normal[^"]*"[^>]+href="([^"]+)"', card)
        image_match = re.search(r'<img[^>]+class="[^"]*s-image[^"]*"[^>]+data-a-dynamic-image="([^"]+)"', card)
        if not image_match:
            image_match = re.search(r'<img[^>]+class="[^"]*s-image[^"]*"[^>]+src="([^"]+)"', card)
        price_match = re.search(r'<span[^>]+class="a-offscreen"[^>]*>(.*?)</span>', card)
        if not title_match:
            continue

        title = html.unescape(re.sub(r"<[^>]+>", "", title_match.group(1))).strip()
        if not title:
            continue

        href = html.unescape(href_match.group(1)) if href_match else None
        url = f"https://www.amazon.com{href}" if href and href.startswith("/") else href
        url = AmazonAgent.normalize_product_url(url)
        if image_match:
            raw_image = html.unescape(image_match.group(1))
            # data-a-dynamic-image is JSON: {"https://...":[[w,h],...], ...}
            try:
                import json as _json
                dyn = _json.loads(raw_image)
                if isinstance(dyn, dict) and dyn:
                    image = max(dyn.keys(), key=lambda u: (dyn[u][0][0] if dyn[u] else 0))
                else:
                    image = raw_image
            except Exception:
                image = raw_image
        else:
            image = None
        price = html.unescape(price_match.group(1)).strip() if price_match else None
        nexus_id = AmazonAgent._build_nexus_id("amazon", url or title)

        results.append(
            ProductResult(
                nexus_id=nexus_id,
                site="amazon",
                title=title,
                price=price,
                url=url,
                image=image,
                variations=[],
            )
        )
        if len(results) >= limit:
            break

    return results


async def _fast_search_placeholder(site: str, query: str, limit: int) -> list[ProductResult]:
    return []


def _to_details_response(result: ProductResult, source_language: str) -> DetailsResponse:
    product = _to_response(result, source_language)
    return DetailsResponse(
        nexus_id=product.nexus_id,
        site=product.site,
        provider_name=product.provider_name,
        title=product.title,
        price=product.price,
        image=product.image,
        variations=[_variation_to_response(variation, source_language) for variation in result.variations],
    )


def _store_products(results: list[ProductResult]) -> None:
    for result in results:
        if _is_valid_nexus_product(result):
            product_registry[result.nexus_id] = result


def _is_valid_nexus_product(result: ProductResult) -> bool:
    if result.site == "amazon":
        return result.nexus_id.startswith("amazon_") and bool(result.url and result.url.startswith("https://www.amazon.com/"))
    if result.site == "aliexpress":
        return result.nexus_id.startswith("aliexpress_") and bool(result.url and result.url.startswith("https://www.aliexpress.com/item/"))
    if result.site == "next":
        return result.nexus_id.startswith("next_") and bool(result.url and result.url.startswith("https://www.next.co.il/"))
    if result.site in HTTP_PROVIDER_SITES:
        return result.nexus_id.startswith(f"{result.site}_") and bool(result.url and result.url.startswith("https://"))
    return False


def _search_cache_key(query: str, limit: int) -> str:
    return f"{query.strip().lower()}::{limit}"


def _read_cached_search(cache_key: str) -> list[ProductResponse] | None:
    entry = SEARCH_MEMORY_CACHE.get(cache_key)
    source = "memory"
    if entry is None:
        entry = _load_search_cache().get(cache_key)
        source = "disk"
    if not entry:
        return None
    if time.time() - entry.get("created_at", 0) > SEARCH_CACHE_TTL_SECONDS:
        SEARCH_MEMORY_CACHE.pop(cache_key, None)
        return None
    cached_results = entry.get("results", [])
    if not cached_results:
        return None
    original_query = cache_key.split("::", 1)[0]
    normalized_results = []
    for item in cached_results:
        if "provider_name" not in item:
            item = {**item, "provider_name": PROVIDER_NAMES.get(item.get("site"), item.get("site", ""))}
        if not AmazonAgent.is_title_relevant(original_query, item.get("title", "")):
            print(f"[Filter] Dropped unrelated cached item from {item.get('site')}: {item.get('title')!r}", flush=True)
            continue
        normalized_results.append(ProductResponse(**item))
    if normalized_results:
        SEARCH_MEMORY_CACHE[cache_key] = entry
        print(f"[Cache] Hit from {source} key={cache_key} results={len(normalized_results)}", flush=True)
    return normalized_results or None


def _write_cached_search(cache_key: str, results: list[ProductResponse], products: list[ProductResult]) -> None:
    cache = _load_search_cache()
    entry = {
        "created_at": time.time(),
        "results": [result.model_dump() for result in results],
        "products": [
            {
                "nexus_id": product.nexus_id,
                "site": product.site,
                "provider_name": product.provider_name or PROVIDER_NAMES.get(product.site, product.site),
                "title": product.title,
                "price": product.price,
                "url": product.url,
                "image": product.image,
                "variations": [],
            }
            for product in products
        ],
    }
    cache[cache_key] = entry
    SEARCH_MEMORY_CACHE[cache_key] = entry
    SEARCH_CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")


def _load_search_cache() -> dict[str, Any]:
    if not SEARCH_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(SEARCH_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _restore_product_by_nexus_id(nexus_id: str) -> ProductResult | None:
    entries = list(SEARCH_MEMORY_CACHE.values())
    entries.extend(_load_search_cache().values())
    for entry in entries:
        if time.time() - entry.get("created_at", 0) > SEARCH_CACHE_TTL_SECONDS:
            continue
        for product in entry.get("products", []):
            if product.get("nexus_id") != nexus_id:
                continue
            try:
                result = ProductResult(**product)
            except TypeError:
                return None
            if _is_valid_nexus_product(result):
                product_registry[result.nexus_id] = result
                print(f"[Details] Restored nexus_id from cache repr={result.nexus_id!r}", flush=True)
                return result
    return None


def _restore_cached_products(cache_key: str) -> None:
    entry = SEARCH_MEMORY_CACHE.get(cache_key)
    if entry is None:
        entry = _load_search_cache().get(cache_key)
    if not entry or time.time() - entry.get("created_at", 0) > SEARCH_CACHE_TTL_SECONDS:
        return
    for product in entry.get("products", []):
        try:
            result = ProductResult(**product)
        except TypeError:
            continue
        if _is_valid_nexus_product(result):
            product_registry[result.nexus_id] = result


def _variation_to_response(variation: VariationResult, source_language: str) -> VariationResponse:
    label = _translate_text(variation.label, source="en", target=source_language) if source_language != "en" else variation.label
    return VariationResponse(type=variation.type, label=label, in_stock=variation.in_stock, price=variation.price)


def _detect_source_language(text: str) -> str:
    if any("\u0590" <= character <= "\u05ff" for character in text):
        return "iw"
    if text.isascii():
        return "en"
    return "auto"


def _translate_text(text: str, source: str, target: str) -> str:
    if not text.strip() or source == target:
        return text
    original_get = requests.get
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def netfree_get(*args: Any, **kwargs: Any):
        kwargs.setdefault("verify", False)
        return original_get(*args, **kwargs)

    try:
        requests.get = netfree_get
        return _translator(source, target).translate(text)
    except Exception:
        return text
    finally:
        requests.get = original_get


@lru_cache(maxsize=32)
def _translator(source: str, target: str) -> GoogleTranslator:
    return GoogleTranslator(source=source, target=target)
