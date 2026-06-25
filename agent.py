import hashlib
import html
import json
import os
import re
import sys
import builtins
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

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

MAX_VARIATIONS_PER_GROUP = 10
PROVIDER_NAMES = {
    "amazon": "אמזון",
    "aliexpress": "AliExpress",
    "next": "Next",
    "nike": "Nike",
    "adidas": "Adidas",
    "super_pharm": "סופר-פארם",
    "ksp": "KSP",
    "machsanei_hashmal": "מחסני חשמל",
    "max_stock": "מקס סטוק",
    "zol_stock": "זול סטוק",
    "ikea": "איקאה",
    "ivory": "אייבורי",
    "terminal_x": "Terminal X",
    "be_pharm": "Be פארם",
    "shufersal": "שופרסל",
    "shein": "Shein",
    "temu": "Temu",
}
QUERY_SYNONYMS = {
    "מחשבון": {"מחשבון", "מחשבונים", "calculator", "calculators", "calc"},
    "calculator": {"מחשבון", "מחשבונים", "calculator", "calculators", "calc"},
    "נעל": {"נעל", "נעליים", "נעלי", "סניקרס", "מגפיים", "כפכפים", "סנדלים", "shoe", "shoes", "sneaker", "sneakers", "boots", "sandals", "slides"},
    "נעליים": {"נעל", "נעליים", "נעלי", "סניקרס", "מגפיים", "כפכפים", "סנדלים", "shoe", "shoes", "sneaker", "sneakers", "boots", "sandals", "slides"},
    "shoe": {"נעל", "נעליים", "נעלי", "סניקרס", "מגפיים", "כפכפים", "סנדלים", "shoe", "shoes", "sneaker", "sneakers", "boots", "sandals", "slides"},
    "shoes": {"נעל", "נעליים", "נעלי", "סניקרס", "מגפיים", "כפכפים", "סנדלים", "shoe", "shoes", "sneaker", "sneakers", "boots", "sandals", "slides"},
    "חולצה": {"חולצה", "חולצות", "shirt", "shirts", "tee", "t-shirt"},
    "shirt": {"חולצה", "חולצות", "shirt", "shirts", "tee", "t-shirt"},
    "מכנס": {"מכנס", "מכנסיים", "מכנסי", "ג'ינס", "טייץ", "שורטס", "ברמודה", "pants", "trousers", "jeans", "shorts", "leggings"},
    "מכנסיים": {"מכנס", "מכנסיים", "מכנסי", "ג'ינס", "טייץ", "שורטס", "ברמודה", "pants", "trousers", "jeans", "shorts", "leggings"},
    "pants": {"מכנס", "מכנסיים", "מכנסי", "ג'ינס", "טייץ", "שורטס", "ברמודה", "pants", "trousers", "jeans", "shorts", "leggings"},
    "jeans": {"מכנס", "מכנסיים", "מכנסי", "ג'ינס", "טייץ", "שורטס", "ברמודה", "pants", "trousers", "jeans", "shorts", "leggings"},
    "shorts": {"מכנס", "מכנסיים", "מכנסי", "ג'ינס", "טייץ", "שורטס", "ברמודה", "pants", "trousers", "jeans", "shorts", "leggings"},
    "leggings": {"מכנס", "מכנסיים", "מכנסי", "ג'ינס", "טייץ", "שורטס", "ברמודה", "pants", "trousers", "jeans", "shorts", "leggings"},
}
NO_RESULTS_PATTERNS = (
    "לא נמצאו תוצאות",
    "לא נמצאו מוצרים",
    "אין תוצאות",
    "0 items found",
    "0 results",
    "no results",
    "no products found",
    "we couldn't find",
    "did not match any products",
)


@dataclass(slots=True)
class VariationResult:
    type: str
    label: str
    in_stock: bool
    price: str | None


@dataclass(slots=True)
class ProductResult:
    nexus_id: str
    site: str
    title: str
    price: str | None
    url: str | None
    image: str | None
    variations: list[VariationResult]
    provider_name: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_name:
            self.provider_name = PROVIDER_NAMES.get(self.site, self.site)


class AmazonAgent:
    def __init__(self, headless: bool | None = None) -> None:
        self.headless = headless if headless is not None else os.getenv("HEADLESS", "false").lower() == "true"
        self._playwright: Any = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def start(self) -> None:
        if self._browser is not None:
            return

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(
            locale="en-US",
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        await self._context.route("**/*", self._block_heavy_resources)
        self._page = await self._context.new_page()

    async def stop(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._page = None

    async def open_amazon(self) -> str:
        page = await self._require_page()
        await page.goto("https://www.amazon.com/", wait_until="domcontentloaded", timeout=60000)
        return page.url

    async def search(self, query: str, limit: int = 5) -> list[ProductResult]:
        page = await self._require_page()
        await page.goto("https://www.amazon.com/", wait_until="domcontentloaded", timeout=60000)

        search_input = page.locator("#twotabsearchtextbox")
        await search_input.wait_for(state="visible", timeout=60000)
        await search_input.fill("")
        await search_input.type(query, delay=40)

        search_button = page.locator("#nav-search-submit-button")
        await search_button.wait_for(state="visible", timeout=30000)
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
            await search_button.click()
        await page.wait_for_load_state("domcontentloaded", timeout=60000)

        search_input = page.locator("#twotabsearchtextbox")
        await search_input.wait_for(state="visible", timeout=60000)
        await page.wait_for_url("**/s?*k=*", timeout=60000)

        items = page.locator(".s-result-item[data-component-type='s-search-result']")
        await items.first.wait_for(state="visible", timeout=60000)
        await page.screenshot(path="amazon_search_debug.png", full_page=True)

        count = min(await items.count(), limit)
        results: list[ProductResult] = []

        for index in range(count):
            item = items.nth(index)
            title_locator = item.locator("h2 span").first
            link_locator = item.locator("a.a-link-normal:has(h2), h2 a, a.a-link-normal.s-no-outline").first
            image_locator = item.locator("img.s-image").first
            whole_price_locator = item.locator(".a-price .a-offscreen").first

            title = await self._safe_inner_text(title_locator)
            if not title:
                continue

            price = await self._safe_inner_text(whole_price_locator)
            href = await self._safe_attribute(link_locator, "href")
            url = f"https://www.amazon.com{href}" if href and href.startswith("/") else href
            url = self.normalize_product_url(url)
            image = None
            try:
                image_raw = await self._safe_attribute(image_locator, "data-a-dynamic-image")
                if image_raw:
                    import json as _json
                    dyn = _json.loads(image_raw)
                    image = max(dyn.keys(), key=lambda u: (dyn[u][0][0] if dyn[u] else 0)) if isinstance(dyn, dict) and dyn else image_raw
                else:
                    image = await self._safe_attribute(image_locator, "src")
            except Exception as img_err:
                print(f"[Amazon Playwright] image parse error: {img_err}", flush=True)
                image = None
            nexus_id = self._build_nexus_id("amazon", url or title)

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

        return results

    async def fast_search(self, query: str, limit: int = 3) -> list[ProductResult]:
        page = await self._require_page()
        await page.goto(f"https://www.amazon.com/s?k={quote_plus(query)}", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_url("**/s?*k=*", timeout=30000)
        items = page.locator(".s-result-item[data-component-type='s-search-result']")
        await items.first.wait_for(state="visible", timeout=30000)

        count = min(await items.count(), limit)
        results: list[ProductResult] = []

        for index in range(count):
            item = items.nth(index)
            title_locator = item.locator("h2 span").first
            link_locator = item.locator("a.a-link-normal:has(h2), h2 a, a.a-link-normal.s-no-outline").first
            image_locator = item.locator("img.s-image").first
            whole_price_locator = item.locator(".a-price .a-offscreen").first

            title = await self._safe_inner_text(title_locator)
            if not title:
                continue

            price = await self._safe_inner_text(whole_price_locator)
            href = await self._safe_attribute(link_locator, "href")
            url = f"https://www.amazon.com{href}" if href and href.startswith("/") else href
            url = self.normalize_product_url(url)
            image_raw = await self._safe_attribute(image_locator, "data-a-dynamic-image")
            if image_raw:
                try:
                    import json as _json
                    dyn = _json.loads(image_raw)
                    image = max(dyn.keys(), key=lambda u: (dyn[u][0][0] if dyn[u] else 0)) if isinstance(dyn, dict) and dyn else image_raw
                except Exception:
                    image = image_raw
            else:
                image = await self._safe_attribute(image_locator, "src")
            nexus_id = self._build_nexus_id("amazon", url or title)

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

        return results

    async def get_variations(self, url: str) -> list[VariationResult]:
        print("[Stage] Fetching variations for product details...", flush=True)
        return await self._scrape_product_variations(self.normalize_product_url(url) or url)

    async def _scrape_product_variations(self, url: str) -> list[VariationResult]:
        if self._context is None:
            return []

        detail_page = await self._context.new_page()
        await detail_page.route("**/*", self._block_detail_resources)
        detail_page.set_default_timeout(6000)
        print(f"[Stage] Details URL={url}", flush=True)
        try:
            try:
                await detail_page.goto(url, wait_until="domcontentloaded", timeout=5500)
                await detail_page.wait_for_load_state("domcontentloaded", timeout=1000)
            except Exception as navigation_exc:
                print(f"[Stage] Details navigation timeout/failed; trying partial HTML: {navigation_exc}", flush=True)

            price = await self._current_detail_price(detail_page)
            html = await detail_page.content()
            variations = self._extract_variations_from_html(html, price)
            if not variations:
                variations = await self._extract_all_variations(detail_page, price)
            if not variations:
                print(f"[Stage] No variations found. Screenshot saved: amazon_no_variations_debug.png url={detail_page.url}", flush=True)
                try:
                    html = await detail_page.content()
                    print(f"[Stage] Empty variations HTML preview={html[:500]!r}", flush=True)
                    with open("amazon_no_variations_debug.html", "w", encoding="utf-8") as debug_file:
                        debug_file.write(html)
                except Exception as html_exc:
                    print(f"[Stage] HTML debug save failed: {html_exc}", flush=True)
                try:
                    await detail_page.screenshot(path="amazon_no_variations_debug.png", full_page=False, timeout=2000)
                except Exception as screenshot_exc:
                    print(f"[Stage] Screenshot failed: {screenshot_exc}", flush=True)

            return variations
        except Exception as exc:
            print(f"[Stage] Variation scrape failed: {exc}", flush=True)
            return []
        finally:
            await detail_page.close()

    async def _extract_all_variations(self, page: Page, price: str | None) -> list[VariationResult]:
        raw_options = await self._safe_evaluate(
            page.locator("body"),
            f"""() => {{
                const maxPerGroup = {MAX_VARIATIONS_PER_GROUP};
                const containers = Array.from(document.querySelectorAll('[id^="variation_"]'));
                const dropdowns = Array.from(document.querySelectorAll('select[id*="dropdown_selected"], select[name*="dropdown_selected"], select[id*="variation"], select[name*="variation"]'));
                const groups = [...containers, ...dropdowns];
                const seen = new Set();
                const results = [];
                const blocked = ['add to cart', 'buy now', 'share', 'review', 'customer', 'helpful', 'report', 'feedback', 'amazon', 'close'];
                const guessType = (group) => {{
                    const text = ((group.id || '') + ' ' + (group.name || '') + ' ' + (group.getAttribute('class') || '') + ' ' + (group.getAttribute('aria-label') || '')).toLowerCase();
                    if (text.includes('size')) return 'size';
                    if (text.includes('color') || text.includes('colour')) return 'color';
                    if (text.includes('style')) return 'style';
                    if (text.includes('pattern')) return 'pattern';
                    if (text.includes('width')) return 'width';
                    return 'option';
                }};
                const labelOf = (element) => {{
                    const imageAlt = element.querySelector?.('img[alt]')?.getAttribute('alt');
                    const text = (element.innerText || element.textContent || '').trim();
                    return text || element.getAttribute('aria-label') || element.getAttribute('title') || element.getAttribute('label') || imageAlt || element.getAttribute('value');
                }};
                const validLabel = (label) => {{
                    const normalized = (label || '').replace(/\\s+/g, ' ').trim();
                    const lower = normalized.toLowerCase();
                    if (!normalized || normalized === '-' || lower === 'select' || lower.startsWith('select ')) return false;
                    if (normalized.length > 80) return false;
                    if (blocked.some((word) => lower.includes(word))) return false;
                    return true;
                }};
                const isStock = (element) => {{
                    const className = (element.getAttribute('class') || '').toLowerCase();
                    const text = (element.innerText || '').toLowerCase();
                    return !className.includes('unavailable') && !className.includes('disabled') && element.getAttribute('aria-disabled') !== 'true' && !element.disabled && !text.includes('unavailable');
                }};
                for (const group of groups) {{
                    const type = guessType(group);
                    const optionNodes = group.tagName?.toLowerCase() === 'select'
                        ? Array.from(group.querySelectorAll('option'))
                        : Array.from(group.querySelectorAll('li, button, [role="button"], .swatchAvailable, .swatchUnavailable, option'));
                    let perGroup = 0;
                    for (const option of optionNodes) {{
                        const label = (labelOf(option) || '').replace(/\\s+/g, ' ').trim();
                        if (!validLabel(label)) continue;
                        const key = `${{type}}::${{label}}`;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        results.push({{ type, label, in_stock: isStock(option) }});
                        perGroup += 1;
                        if (perGroup >= maxPerGroup) break;
                    }}
                }}
                return results;
            }}""",
        )
        variations: list[VariationResult] = []
        for option in raw_options or []:
            label = str(option.get("label") or "").strip()
            variation_type = str(option.get("type") or "option").strip() or "option"
            if not label:
                continue
            variations.append(
                VariationResult(
                    type=variation_type,
                    label=label,
                    in_stock=bool(option.get("in_stock")),
                    price=price,
                )
            )
        print(f"[Stage] Extracted variations count={len(variations)}", flush=True)
        return variations

    @staticmethod
    def _extract_variations_from_html(page_html: str, price: str | None) -> list[VariationResult]:
        variations: list[VariationResult] = []
        seen: set[str] = set()
        type_patterns = (
            ("size", r'"size_name"\s*:\s*\{([^{}]+)\}'),
            ("color", r'"color_name"\s*:\s*\{([^{}]+)\}'),
            ("style", r'"style_name"\s*:\s*\{([^{}]+)\}'),
            ("pattern", r'"pattern_name"\s*:\s*\{([^{}]+)\}'),
        )
        for variation_type, pattern in type_patterns:
            for group_match in re.finditer(pattern, page_html):
                group = group_match.group(1)
                for label_match in re.finditer(r'"([^"{}:]+)"\s*:', group):
                    label = label_match.group(1).strip()
                    if not label or label in seen or label.lower().startswith("select"):
                        continue
                    seen.add(label)
                    variations.append(
                        VariationResult(
                            type=variation_type,
                            label=label,
                            in_stock=True,
                            price=price,
                        )
                    )
                    if len([item for item in variations if item.type == variation_type]) >= MAX_VARIATIONS_PER_GROUP:
                        break
        print(f"[Stage] HTML fallback variations count={len(variations)}", flush=True)
        return variations

    async def _extract_variation_group(self, page: Page, variation_type: str, selector: str) -> list[VariationResult]:
        price = await self._current_detail_price(page)
        raw_options = await self._safe_evaluate(
            page.locator("body"),
            f"""() => Array.from(document.querySelectorAll({selector!r})).slice(0, {MAX_VARIATIONS_PER_GROUP}).map((element) => {{
                const text = (element.innerText || '').trim();
                const label = text || element.getAttribute('aria-label') || element.getAttribute('title') || element.getAttribute('label') || element.getAttribute('value') || element.querySelector('[aria-label]')?.getAttribute('aria-label') || element.querySelector('[title]')?.getAttribute('title') || element.querySelector('img[alt]')?.getAttribute('alt');
                const className = element.getAttribute('class') || '';
                return {{
                    label,
                    in_stock: !className.toLowerCase().includes('unavailable') && element.getAttribute('aria-disabled') !== 'true' && !element.disabled,
                }};
            }})""",
        )
        variations: list[VariationResult] = []

        for option in raw_options or []:
            label = str(option.get("label") or "").strip()
            if not label or label.startswith("-") or label.lower() == "select":
                continue
            variations.append(
                VariationResult(
                    type=variation_type,
                    label=label,
                    in_stock=bool(option.get("in_stock")),
                    price=price,
                )
            )

        return variations

    async def _variation_label(self, locator: Any) -> str | None:
        text = await self._safe_inner_text(locator)
        if text and not text.startswith("-") and text.lower() != "select":
            return text

        for attribute in ("aria-label", "title", "label", "value", "data-defaultasin"):
            value = await self._safe_attribute(locator, attribute)
            if value and not value.startswith("-") and value.lower() != "select":
                return value

        for selector, attribute in (
            ("[aria-label]", "aria-label"),
            ("[title]", "title"),
            ("img[alt]", "alt"),
            ("option", "value"),
        ):
            value = await self._safe_attribute(locator.locator(selector).first, attribute)
            if value and not value.startswith("-"):
                return value

        return None

    async def _current_detail_price(self, page: Page) -> str | None:
        return await self._safe_evaluate(
            page.locator("body"),
            """() => {
                const selectors = [
                    '#corePrice_feature_div .a-offscreen',
                    '#priceblock_ourprice',
                    '#priceblock_dealprice',
                    '.apexPriceToPay .a-offscreen',
                    '.a-price .a-offscreen'
                ];
                for (const selector of selectors) {
                    const element = document.querySelector(selector);
                    const text = (element?.innerText || element?.textContent || '').trim();
                    if (text) return text;
                }
                return null;
            }""",
        )

    @staticmethod
    def normalize_product_url(url: str | None) -> str | None:
        if not url:
            return None
        parsed = urlparse(url)
        query_url = parse_qs(parsed.query).get("url", [None])[0]
        if query_url:
            decoded = unquote(query_url)
            url = f"https://www.amazon.com{decoded}" if decoded.startswith("/") else decoded
            parsed = urlparse(url)
        asin_match = None
        parts = [part for part in parsed.path.split("/") if part]
        for index, part in enumerate(parts):
            if part in {"dp", "gp", "product"} and index + 1 < len(parts):
                asin_match = parts[index + 1]
                break
        if not asin_match:
            for part in parts:
                if len(part) == 10 and part.upper().startswith("B"):
                    asin_match = part
                    break
        if asin_match:
            return f"https://www.amazon.com/dp/{asin_match}"
        return url

    @staticmethod
    def _build_nexus_id(site: str, value: str) -> str:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
        normalized = "".join(character.lower() if character.isalnum() else "-" for character in value)
        normalized = "-".join(part for part in normalized.split("-") if part)
        return f"{site}_{normalized[:32]}_{digest}"

    @staticmethod
    def page_has_no_results(page_html: str) -> bool:
        normalized = re.sub(r"\s+", " ", html.unescape(page_html)).lower()
        return any(pattern.lower() in normalized for pattern in NO_RESULTS_PATTERNS)

    @staticmethod
    def query_tokens(query: str) -> set[str]:
        tokens = {token.lower() for token in re.findall(r"[\w\u0590-\u05ff'-]{2,}", query)}
        expanded = set(tokens)
        for token in tokens:
            expanded.update(QUERY_SYNONYMS.get(token, set()))
            for key, synonyms in QUERY_SYNONYMS.items():
                if token.startswith(key) or key.startswith(token):
                    expanded.update(synonyms)
        return {token.lower() for token in expanded if len(token) >= 2}

    @staticmethod
    def is_title_relevant(query: str, title: str) -> bool:
        tokens = AmazonAgent.query_tokens(query)
        if not tokens:
            return True
        normalized_title = re.sub(r"\s+", " ", html.unescape(title)).lower()
        title_tokens = {token.lower() for token in re.findall(r"[\w\u0590-\u05ff'-]{2,}", normalized_title)}
        matched = 0
        for token in tokens:
            token_lower = token.lower()
            is_hebrew = any("\u0590" <= c <= "\u05ff" for c in token_lower)
            if is_hebrew:
                # Hebrew tokens must match a full word. Also allow standard prefixes (ל, ב, כ, מ, ש, ה, ו).
                if token_lower in title_tokens:
                    matched += 1
                else:
                    prefixes = "לבוכמשהו"
                    if any(t.startswith(p) and t[len(p):] == token_lower for t in title_tokens for p in prefixes):
                        matched += 1
            else:
                # English/other tokens: exact word, or word-boundary substring, or prefix match with boundary.
                if token_lower in title_tokens:
                    matched += 1
                elif re.search(r"(?<!\w)" + re.escape(token_lower) + r"(?!\w)", normalized_title):
                    matched += 1
                elif any(token_lower.startswith(t) or (t.startswith(token_lower) and not re.match(r"[a-z0-9]", t[len(token_lower):])) for t in title_tokens if len(token_lower) >= 3 and len(t) >= 3):
                    matched += 1

        score = matched / len(tokens)
        # Hebrew queries are strict: all Hebrew tokens must be present to avoid false positives like "פנס" in "פנסוניק".
        if any("\u0590" <= c <= "\u05ff" for c in query.lower()):
            return score >= 1.0
        # English queries: at least half of the tokens must match (or the only token).
        if len(tokens) == 1:
            return score >= 1.0
        return score >= 0.5

    async def fast_search_aliexpress(self, query: str, limit: int = 3) -> list[ProductResult]:
        search_slug = quote_plus(query).replace("+", "-")
        url = f"https://www.aliexpress.com/w/wholesale-{search_slug}.html?sortType=bestmatch_sort"
        try:
            async with httpx.AsyncClient(headers=self._aliexpress_headers(), follow_redirects=True, timeout=6.0, verify=False) as client:
                response = await client.get(url)
            print(f"[AliExpress Search] HTTP status={response.status_code} url={url}", flush=True)
            if response.status_code >= 400:
                print(f"[AliExpress Search] Block/error status={response.status_code} preview={response.text[:300]!r}", flush=True)
                return []
            page_html = response.text
            if self.page_has_no_results(page_html):
                print("[AliExpress Search] No-results page detected", flush=True)
                return []
        except Exception as exc:
            print(f"[AliExpress Search] HTTP failed: {exc}", flush=True)
            return []

        raw_items = self._extract_aliexpress_search_items(page_html, limit)
        results: list[ProductResult] = []
        for item in raw_items:
            item_id = str(item.get("item_id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not item_id or not title:
                continue
            clean_url = self.normalize_aliexpress_url(str(item.get("url") or ""), item_id)
            nexus_id = self._build_aliexpress_nexus_id(item_id, clean_url or title)
            results.append(
                ProductResult(
                    nexus_id=nexus_id,
                    site="aliexpress",
                    title=title,
                    price=self._clean_price(item.get("price")),
                    url=clean_url,
                    image=item.get("image"),
                    variations=[],
                )
            )
        print(f"[AliExpress Search] Found {len(results)} results", flush=True)
        if not results:
            print(f"[AliExpress Search] Empty results preview={page_html[:500]!r}", flush=True)
        return results[:limit]

    async def get_aliexpress_variations(self, url: str) -> list[VariationResult]:
        clean_url = self.normalize_aliexpress_url(url)
        print(f"[AliExpress Details] URL={clean_url}", flush=True)
        if not clean_url:
            return []
        try:
            async with httpx.AsyncClient(headers=self._aliexpress_headers(), follow_redirects=True, timeout=6.0, verify=False) as client:
                response = await client.get(clean_url)
            print(f"[AliExpress Details] HTTP status={response.status_code}", flush=True)
            if response.status_code >= 400:
                print(f"[AliExpress Details] Block/error status={response.status_code} preview={response.text[:300]!r}", flush=True)
                return []
            variations = self._extract_aliexpress_variations_from_html(response.text)
            print(f"[AliExpress Details] Extracted {len(variations)} variations", flush=True)
            return variations
        except Exception as exc:
            print(f"[AliExpress Details] Failed: {exc}", flush=True)
            return []

    @classmethod
    def _extract_aliexpress_search_items(cls, page_html: str, limit: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        pattern = r'(?:https?:)?//(?:www\.)?aliexpress\.com/item/(\d+)\.html[^"\'<\s]*|/item/(\d+)\.html[^"\'<\s]*'
        for match in re.finditer(pattern, page_html):
            item_id = match.group(1) or match.group(2)
            if not item_id or item_id in seen:
                continue
            block = page_html[max(0, match.start() - 2500): min(len(page_html), match.end() + 2500)]
            title = cls._extract_aliexpress_title(block, item_id)
            if not title:
                continue
            seen.add(item_id)
            items.append(
                {
                    "item_id": item_id,
                    "title": title,
                    "price": cls._extract_aliexpress_price(block),
                    "image": cls._extract_aliexpress_image(block),
                    "url": f"https://www.aliexpress.com/item/{item_id}.html",
                }
            )
            if len(items) >= limit:
                break
        return items

    @staticmethod
    def _extract_aliexpress_title(block: str, item_id: str) -> str | None:
        for pattern in (
            r'"title"\s*:\s*"([^"]{6,250})"',
            r'"productTitle"\s*:\s*"([^"]{6,250})"',
            r'"subject"\s*:\s*"([^"]{6,250})"',
            r'title="([^"]{6,250})"',
            r'alt="([^"]{6,250})"',
        ):
            match = re.search(pattern, block)
            if not match:
                continue
            title = html.unescape(match.group(1))
            if "\\u" in title or "\\x" in title:
                try:
                    title = title.encode("utf-8").decode("unicode_escape", errors="ignore")
                except Exception:
                    pass
            try:
                title = title.encode("latin1").decode("utf-8")
            except Exception:
                pass
            title = re.sub(r"\s+", " ", title).strip()
            if title and item_id not in title:
                return title
        return None

    @staticmethod
    def _extract_aliexpress_price(block: str) -> str | None:
        for pattern in (
            r'"formattedPrice"\s*:\s*"([^"]+)"',
            r'"salePriceString"\s*:\s*"([^"]+)"',
            r'"minPrice"\s*:\s*"([^"]+)"',
            r'((?:ILS|US\s?\$|\$|₪|€|£)\s?[\d,.]+(?:\s?-\s?(?:ILS|US\s?\$|\$|₪|€|£)?\s?[\d,.]+)?)',
        ):
            match = re.search(pattern, block, re.IGNORECASE)
            if match:
                return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()
        return None

    @staticmethod
    def _extract_aliexpress_image(block: str) -> str | None:
        match = re.search(r'(https?:)?//[^"\'<\s]+?\.(?:jpg|jpeg|png|webp)(?:_[^"\'<\s]+)?', block)
        if not match:
            return None
        image = match.group(0)
        return f"https:{image}" if image.startswith("//") else image

    @staticmethod
    def _aliexpress_headers() -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,he;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://www.aliexpress.com/",
        }

    async def _extract_aliexpress_variations_from_dom(self, page: Page) -> list[VariationResult]:
        raw_options = await self._safe_evaluate(
            page.locator("body"),
            """() => {
                const results = [];
                const seen = new Set();
                const nodes = Array.from(document.querySelectorAll('[class*="sku"], [class*="Sku"], [class*="option"], [class*="Option"], [class*="property"], [class*="Property"]'));
                const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                for (const node of nodes) {
                    const groupText = clean(node.innerText || node.textContent);
                    const type = /color|colour|צבע/i.test(groupText) ? 'color' : /size|מידה|shoe/i.test(groupText) ? 'size' : 'option';
                    for (const child of Array.from(node.querySelectorAll('button, span, div, li'))) {
                        const label = clean(child.getAttribute('title') || child.getAttribute('aria-label') || child.innerText || child.textContent);
                        if (!label || label.length > 60 || label.includes('\n')) continue;
                        const key = `${type}::${label}`;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        results.push({ type, label, in_stock: !/disabled|sold|unavailable/i.test(child.className || '') });
                        if (results.length >= 30) return results;
                    }
                }
                return results;
            }""",
        )
        return [
            VariationResult(
                type=str(item.get("type") or "option"),
                label=str(item.get("label") or "").strip(),
                in_stock=bool(item.get("in_stock", True)),
                price=None,
            )
            for item in raw_options or []
            if str(item.get("label") or "").strip()
        ]

    @classmethod
    def _extract_aliexpress_variations_from_html(cls, page_html: str) -> list[VariationResult]:
        candidates = cls._aliexpress_json_candidates(page_html)
        variations: list[VariationResult] = []
        for candidate in candidates:
            variations = cls._extract_aliexpress_variations_from_json(candidate)
            if variations:
                break
        print(f"[AliExpress Details] JSON variations count={len(variations)}", flush=True)
        if variations:
            return variations
        fallback = cls._extract_aliexpress_variations_fallback(page_html)
        print(f"[AliExpress Details] Fallback variations count={len(fallback)}", flush=True)
        return fallback

    @classmethod
    def _aliexpress_json_candidates(cls, page_html: str) -> list[Any]:
        candidates: list[Any] = []
        for key in ('skuModule', 'window.runParams', 'runParams', 'data:', 'productSKUPropertyList', 'skuPriceList'):
            index = page_html.find(key)
            while index != -1:
                brace_index = page_html.find('{', index)
                if brace_index == -1:
                    break
                block = cls._balanced_json_block(page_html, brace_index)
                if block:
                    try:
                        candidates.append(json.loads(block))
                    except Exception:
                        pass
                index = page_html.find(key, index + len(key))
        return candidates

    @classmethod
    def _extract_aliexpress_variations_from_json(cls, data: Any) -> list[VariationResult]:
        sku_modules = cls._find_dicts_with_key(data, 'skuPriceList') + cls._find_dicts_with_key(data, 'productSKUPropertyList')
        variations: list[VariationResult] = []
        seen: set[str] = set()
        for module in sku_modules:
            sku_prices = module.get('skuPriceList') or module.get('skuPriceList'.lower()) or []
            price_by_prop: dict[str, tuple[str | None, bool]] = {}
            for sku in sku_prices if isinstance(sku_prices, list) else []:
                prop_path = str(
                    sku.get('skuPropIds')
                    or sku.get('skuAttr')
                    or sku.get('skuAttrs')
                    or sku.get('skuId')
                    or sku.get('id')
                    or ''
                )
                price = cls._extract_price_from_any(sku)
                stock = cls._extract_stock_from_any(sku)
                if prop_path:
                    price_by_prop[prop_path] = (price, stock)
            properties = module.get('productSKUPropertyList') or []
            for prop in properties if isinstance(properties, list) else []:
                prop_name = str(prop.get('skuPropertyName') or prop.get('skuPropertyDisplayName') or prop.get('name') or '').lower()
                variation_type = cls._aliexpress_variation_type(prop_name)
                values = prop.get('skuPropertyValues') or []
                for value in values if isinstance(values, list) else []:
                    label = cls._clean_aliexpress_label(
                        value.get('propertyValueDisplayName')
                        or value.get('skuPropertyTips')
                        or value.get('propertyValueName')
                        or value.get('name')
                        or value.get('value')
                    )
                    value_id = str(value.get('propertyValueId') or value.get('skuPropertyValueId') or value.get('id') or '')
                    if not label:
                        continue
                    key = f'{variation_type}::{label}'
                    if key in seen:
                        continue
                    seen.add(key)
                    matched_price = None
                    in_stock = True
                    for prop_path, price_stock in price_by_prop.items():
                        if value_id and value_id in prop_path:
                            matched_price, in_stock = price_stock
                            break
                    variations.append(VariationResult(type=variation_type, label=label, in_stock=in_stock, price=matched_price))
        return variations[:40]

    @classmethod
    def _extract_aliexpress_variations_fallback(cls, page_html: str) -> list[VariationResult]:
        variations: list[VariationResult] = []
        seen: set[str] = set()
        page_text = html.unescape(page_html)
        price = cls._extract_price_from_any(page_text)
        property_blocks = re.findall(r'"skuPropertyName"\s*:\s*"([^"]+)".{0,12000}?"skuPropertyValues"\s*:\s*\[(.*?)\]', page_text, re.DOTALL)
        for prop_name, values_block in property_blocks:
            variation_type = cls._aliexpress_variation_type(prop_name)
            for label in re.findall(r'"(?:propertyValueDisplayName|skuPropertyTips|propertyValueName|name|value)"\s*:\s*"([^"]+)"', values_block):
                clean_label = cls._clean_aliexpress_label(label)
                if not clean_label:
                    continue
                key = f"{variation_type}::{clean_label}"
                if key in seen:
                    continue
                seen.add(key)
                variations.append(VariationResult(type=variation_type, label=clean_label, in_stock=True, price=price))
        if variations:
            return variations[:40]
        chip_patterns = (
            ('size', r'(?:Size|Shoe Size|מידה|מידת נעליים)[^<]{0,80}</[^>]+>(.{0,3000})'),
            ('color', r'(?:Color|Colour|צבע|סגנון|Style)[^<]{0,80}</[^>]+>(.{0,3000})'),
        )
        for variation_type, pattern in chip_patterns:
            for block in re.findall(pattern, page_text, re.IGNORECASE | re.DOTALL):
                labels = re.findall(r'(?:title|aria-label|alt)\s*=\s*["\']([^"\']{1,80})["\']', block)
                labels.extend(re.findall(r'>([^<>]{1,40})<', block))
                for label in labels:
                    clean_label = cls._clean_aliexpress_label(label)
                    if not clean_label or not cls._is_plausible_aliexpress_variation(variation_type, clean_label):
                        continue
                    key = f"{variation_type}::{clean_label}"
                    if key in seen:
                        continue
                    seen.add(key)
                    variations.append(VariationResult(type=variation_type, label=clean_label, in_stock=True, price=price))
        return variations[:40]

    @staticmethod
    def _aliexpress_variation_type(value: str) -> str:
        text = value.lower()
        if any(token in text for token in ('color', 'colour', 'style', 'צבע', 'סגנון')):
            return 'color'
        if any(token in text for token in ('size', 'shoe', 'מידה', 'מידת')):
            return 'size'
        return 'option'

    @staticmethod
    def _clean_aliexpress_label(value: Any) -> str | None:
        if value is None:
            return None
        label = html.unescape(str(value)).strip()
        if "\\u" in label or "\\x" in label:
            try:
                label = label.encode("utf-8").decode("unicode_escape", errors="ignore")
            except Exception:
                pass
        try:
            label = label.encode("latin1").decode("utf-8")
        except Exception:
            pass
        label = re.sub(r"<[^>]+>", " ", label)
        label = re.sub(r"\s+", " ", label).strip()
        if not label or len(label) > 80:
            return None
        if label.lower() in {"select", "choose", "null", "undefined"}:
            return None
        return label

    @staticmethod
    def _is_plausible_aliexpress_variation(variation_type: str, label: str) -> bool:
        lower = label.lower()
        blocked = {
            "browse by category", "non-login complaint entrance", "portuguese", "spanish", "french",
            "german", "italian", "dutch", "turkish", "japanese", "korean", "thai", "arabic",
            "hebrew", "polish", "product", "sale", "החזרות", "החזרות בחינם", "מוצר",
            "מבצע", "הכי פופולרי", "מרכז שקיפות", ",",
        }
        if lower in blocked or label in blocked:
            return False
        if variation_type == "size":
            return bool(re.fullmatch(r"(?:EU\s*)?\d{1,2}(?:\.\d)?|[XSML]{1,4}|XXS|XXL|XXXL|US\s*\d{1,2}(?:\.\d)?|UK\s*\d{1,2}(?:\.\d)?", label, re.IGNORECASE))
        if variation_type == "color":
            color_words = {
                "black", "white", "gray", "grey", "red", "blue", "green", "yellow", "brown",
                "beige", "pink", "purple", "orange", "khaki", "navy", "silver", "gold",
                "שחור", "לבן", "אפור", "אדום", "כחול", "ירוק", "צהוב", "חום", "בז'",
                "ורוד", "סגול", "כתום", "כסף", "זהב",
            }
            return lower in color_words or any(word in lower for word in color_words)
        return 1 <= len(label) <= 40

    @classmethod
    def _find_dicts_with_key(cls, data: Any, key: str) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        if isinstance(data, dict):
            if key in data:
                matches.append(data)
            for value in data.values():
                matches.extend(cls._find_dicts_with_key(value, key))
        elif isinstance(data, list):
            for value in data:
                matches.extend(cls._find_dicts_with_key(value, key))
        return matches

    @staticmethod
    def _balanced_json_block(text: str, start: int) -> str | None:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == '\\':
                    escape = True
                elif char == '"':
                    in_string = False
            else:
                if char == '"':
                    in_string = True
                elif char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        return text[start:index + 1]
        return None

    @staticmethod
    def _extract_price_from_any(data: Any) -> str | None:
        if isinstance(data, dict):
            for key in ('salePriceString', 'skuActivityAmountLocal', 'skuAmountLocal', 'formattedPrice', 'price'):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, dict):
                    nested = AmazonAgent._extract_price_from_any(value)
                    if nested:
                        return nested
            for value in data.values():
                nested = AmazonAgent._extract_price_from_any(value)
                if nested:
                    return nested
        elif isinstance(data, list):
            for value in data:
                nested = AmazonAgent._extract_price_from_any(value)
                if nested:
                    return nested
        elif isinstance(data, str):
            match = re.search(r'((?:ILS|US\s?\$|\$|₪|€|£)\s?[\d,.]+(?:\s?-\s?(?:ILS|US\s?\$|\$|₪|€|£)?\s?[\d,.]+)?)', data, re.IGNORECASE)
            if match:
                return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()
        return None

    @staticmethod
    def _extract_stock_from_any(data: Any) -> bool:
        if isinstance(data, dict):
            for key in ('skuVal', 'availQuantity', 'stock', 'inventory'):
                value = data.get(key)
                if isinstance(value, int):
                    return value > 0
            if data.get('salable') is False or data.get('disabled') is True:
                return False
        return True

    @staticmethod
    def normalize_aliexpress_url(url: str | None, item_id: str | None = None) -> str | None:
        if not url and not item_id:
            return None
        source = url or ''
        match = re.search(r'/item/(\d+)\.html', source) or re.search(r'(\d{8,})', source)
        clean_item_id = item_id or (match.group(1) if match else None)
        if not clean_item_id:
            return source or None
        return f"https://www.aliexpress.com/item/{clean_item_id}.html"

    @staticmethod
    def _build_aliexpress_nexus_id(item_id: str, value: str) -> str:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
        return f"aliexpress_{item_id}_{digest}"

    @staticmethod
    def _clean_price(value: Any) -> str | None:
        if value is None:
            return None
        price = re.sub(r"\s+", " ", html.unescape(str(value))).strip()
        return price or None

    async def fast_search_next(self, query: str, limit: int = 3) -> list[ProductResult]:
        url = f"https://www.next.co.il/he/search?w={quote_plus(query)}"
        try:
            async with httpx.AsyncClient(headers=self._next_headers(), follow_redirects=True, timeout=8.0, verify=False) as client:
                response = await client.get(url)
                if response.status_code == 403:
                    response = await client.get("https://www.next.co.il/he")
                    response = await client.get(url)
            print(f"[Next Search] HTTP status={response.status_code} url={url}", flush=True)
            if response.status_code >= 400:
                print(f"[Next Search] Block/error status={response.status_code} preview={response.text[:300]!r}", flush=True)
                return []
            page_html = response.text
            if self.page_has_no_results(page_html):
                print("[Next Search] No-results page detected", flush=True)
                return []
        except Exception as exc:
            print(f"[Next Search] HTTP failed: {exc}", flush=True)
            return []

        raw_items = self._extract_next_search_items(page_html, limit)
        print(f"[Scraper] next found {len(raw_items)} raw elements", flush=True)
        results: list[ProductResult] = []
        for item in raw_items:
            item_id = str(item.get("item_id") or "").strip()
            title = str(item.get("title") or "").strip()
            clean_url = self.normalize_next_url(str(item.get("url") or ""))
            if not item_id or not title or not clean_url:
                continue
            nexus_id = self._build_next_nexus_id(item_id, clean_url)
            results.append(
                ProductResult(
                    nexus_id=nexus_id,
                    site="next",
                    title=title,
                    price=self._clean_price(item.get("price")),
                    url=clean_url,
                    image=item.get("image"),
                    variations=[],
                )
            )
        print(f"[Next Search] Found {len(results)} results", flush=True)
        if not results:
            print(f"[Next Search] Empty results preview={page_html[:500]!r}", flush=True)
        return results[:limit]

    async def get_next_variations(self, url: str) -> list[VariationResult]:
        clean_url = self.normalize_next_url(url)
        print(f"[Next Details] URL={clean_url}", flush=True)
        if not clean_url:
            return []
        try:
            async with httpx.AsyncClient(headers=self._next_headers(), follow_redirects=True, timeout=8.0, verify=False) as client:
                response = await client.get(clean_url)
            print(f"[Next Details] HTTP status={response.status_code}", flush=True)
            if response.status_code >= 400:
                print(f"[Next Details] Block/error status={response.status_code} preview={response.text[:300]!r}", flush=True)
                return []
            variations = self._extract_next_variations_from_html(response.text)
            print(f"[Next Details] Extracted {len(variations)} variations", flush=True)
            return variations
        except Exception as exc:
            print(f"[Next Details] Failed: {exc}", flush=True)
            return []

    @classmethod
    def _extract_next_search_items(cls, page_html: str, limit: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        config = cls._http_provider_configs()["next"] if "next" in cls._http_provider_configs() else {"base_url": "https://www.next.co.il"}
        embedded = cls._extract_products_from_embedded_json("next", page_html, config, limit)
        for product in embedded:
            item_id = cls._next_item_id(product.url) or cls._generic_provider_item_id((product.url or "") + product.title)
            if item_id in seen:
                continue
            seen.add(item_id)
            items.append({"item_id": item_id, "title": product.title, "price": product.price, "image": product.image, "url": product.url})
            if len(items) >= limit:
                return items
        for match in re.finditer(r'href=["\']([^"\']*(?:/style/|/shop/|/g\d+|p\d+)[^"\']*)["\']', page_html, re.IGNORECASE):
            href = html.unescape(match.group(1))
            product_url = cls.normalize_next_url(href)
            if not product_url:
                continue
            item_id = cls._next_item_id(product_url)
            if not item_id or item_id in seen:
                continue
            block = page_html[max(0, match.start() - 2500): min(len(page_html), match.end() + 2500)]
            title = cls._extract_next_title(block)
            price = cls._extract_next_price(block)
            image = cls._extract_next_image(block)
            if not title:
                continue
            seen.add(item_id)
            items.append({"item_id": item_id, "title": title, "price": price, "image": image, "url": product_url})
            if len(items) >= limit:
                return items
        for block in cls._fashion_product_blocks(page_html):
            href_match = re.search(r'href=["\']([^"\']+)["\']', block)
            if not href_match:
                continue
            product_url = cls.normalize_next_url(html.unescape(href_match.group(1)))
            item_id = cls._next_item_id(product_url)
            if not product_url or not item_id or item_id in seen:
                continue
            title = cls._extract_next_title(block)
            if not title:
                continue
            seen.add(item_id)
            items.append({"item_id": item_id, "title": title, "price": cls._extract_next_price(block), "image": cls._extract_next_image(block), "url": product_url})
            if len(items) >= limit:
                break
        return items

    @classmethod
    def _extract_next_variations_from_html(cls, page_html: str) -> list[VariationResult]:
        price = cls._extract_next_price(page_html)
        variations: list[VariationResult] = []
        seen: set[str] = set()
        for select_match in re.finditer(r'<select[^>]*(?:id|name)=["\'][^"\']*(?:Size|size|Colour|Color|צבע|מידה)[^"\']*["\'][^>]*>(.*?)</select>', page_html, re.IGNORECASE | re.DOTALL):
            select_html = select_match.group(0)
            variation_type = "color" if re.search(r'Colour|Color|צבע', select_html, re.IGNORECASE) else "size"
            options_html = select_match.group(1)
            for option in re.finditer(r'<option([^>]*)>(.*?)</option>', options_html, re.IGNORECASE | re.DOTALL):
                attrs = option.group(1)
                label = cls._clean_next_label(option.group(2))
                if not label:
                    continue
                in_stock = not re.search(r'disabled|sold\s*out|אזל|out of stock', attrs + label, re.IGNORECASE)
                key = f"{variation_type}::{label}"
                if key in seen:
                    continue
                seen.add(key)
                variations.append(VariationResult(type=variation_type, label=label, in_stock=in_stock, price=price))
        if variations:
            return variations[:40]
        for label in re.findall(r'"(?:size|Size|displaySize|text|label)"\s*:\s*"([^"]{1,80})"', page_html):
            clean_label = cls._clean_next_label(label)
            if not clean_label or not cls._is_plausible_next_size(clean_label):
                continue
            key = f"size::{clean_label}"
            if key in seen:
                continue
            seen.add(key)
            variations.append(VariationResult(type="size", label=clean_label, in_stock=True, price=price))
        for label in re.findall(r'"(?:colour|color|Colour|Color|displayColour|displayColor)"\s*:\s*"([^"]{1,80})"', page_html):
            clean_label = cls._clean_next_label(label)
            if not clean_label:
                continue
            key = f"color::{clean_label}"
            if key in seen:
                continue
            seen.add(key)
            variations.append(VariationResult(type="color", label=clean_label, in_stock=True, price=price))
        return variations[:40]

    @staticmethod
    def _extract_next_title(block: str) -> str | None:
        for pattern in (
            r'<h[1-4][^>]*>(.*?)</h[1-4]>',
            r'(?:aria-label|title|alt)=["\']([^"\']{3,180})["\']',
            r'"(?:name|title|productName)"\s*:\s*"([^"]{3,180})"',
        ):
            match = re.search(pattern, block, re.IGNORECASE | re.DOTALL)
            if match:
                label = AmazonAgent._clean_next_label(match.group(1))
                if label and not re.search(r'next|logo|search|wishlist|bag|account', label, re.IGNORECASE):
                    return label
        return None

    @staticmethod
    def _extract_next_price(block: str) -> str | None:
        for pattern in (
            r'(₪\s?[\d,.]+)',
            r'(\d+(?:[,.]\d+)?\s?₪)',
            r'"(?:price|sellingPrice|displayPrice)"\s*:\s*"([^"]+)"',
        ):
            match = re.search(pattern, block, re.IGNORECASE)
            if match:
                return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()
        return None

    @staticmethod
    def _extract_next_image(block: str) -> str | None:
        match = re.search(r'(https?:)?//[^"\'<\s]+?\.(?:jpg|jpeg|png|webp)(?:[^"\'<\s]*)?', block, re.IGNORECASE)
        if not match:
            return None
        image = html.unescape(match.group(0))
        return f"https:{image}" if image.startswith("//") else image

    @staticmethod
    def normalize_next_url(url: str | None) -> str | None:
        if not url:
            return None
        clean_url = html.unescape(url).strip()
        if clean_url.startswith("//"):
            clean_url = f"https:{clean_url}"
        elif clean_url.startswith("/"):
            clean_url = f"https://www.next.co.il{clean_url}"
        if not clean_url.startswith("http"):
            return None
        parsed = urlparse(clean_url)
        if "next.co.il" not in parsed.netloc:
            return None
        return f"https://www.next.co.il{parsed.path}"

    @staticmethod
    def _next_item_id(url: str | None) -> str | None:
        if not url:
            return None
        match = re.search(r'(?:/style/|/shop/)?([A-Za-z]?\d{4,}|g\d+|p\d+)', url, re.IGNORECASE)
        if match:
            return re.sub(r'\W+', '', match.group(1)).lower()
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        return digest

    @staticmethod
    def _build_next_nexus_id(item_id: str, value: str) -> str:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
        return f"next_{item_id}_{digest}"

    @staticmethod
    def _clean_next_label(value: Any) -> str | None:
        if value is None:
            return None
        label = html.unescape(re.sub(r"<[^>]+>", " ", str(value))).strip()
        label = re.sub(r"\s+", " ", label).strip()
        if not label or len(label) > 120:
            return None
        if label.lower() in {"select", "choose", "null", "undefined", "בחר", "בחר מידה"}:
            return None
        return label

    @staticmethod
    def _is_plausible_next_size(label: str) -> bool:
        return bool(re.fullmatch(r"(?:EU\s*)?\d{1,2}(?:\.\d)?|[XSML]{1,4}|XXS|XXL|XXXL|UK\s*\d{1,2}(?:\.\d)?|US\s*\d{1,2}(?:\.\d)?", label, re.IGNORECASE))

    @staticmethod
    def _next_headers() -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "DNT": "1",
            "Host": "www.next.co.il",
            "Pragma": "no-cache",
            "Referer": "https://www.next.co.il/he",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }

    async def fast_search_temu(self, query: str, limit: int = 3) -> list[ProductResult]:
        api_key = os.getenv("SERPAPI_API_KEY")
        print(f"[Temu Search] SerpAPI Key found: {bool(api_key)}", flush=True)
        if not api_key:
            print("[Temu Search] Missing SERPAPI_API_KEY - returning empty", flush=True)
            return []
        url = "https://serpapi.com/search.json"
        params = {
            "engine": "google_shopping",
            "q": query,
            "api_key": api_key,
            "gl": "us",
            "hl": "en",
            "tbm": "shop",
        }
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=3.0) as client:
                response = await client.get(url, params=params)
            print(f"[Temu Search] HTTP status={response.status_code}", flush=True)
            if response.status_code >= 400:
                print(f"[Temu Search] API Error: {response.text[:300]}", flush=True)
                return []
            data = response.json()
            items = data.get("shopping_results", []) or []
            print(f"[Temu Search] Google Shopping returned {len(items)} raw items", flush=True)
            results: list[ProductResult] = []
            print(f"[Temu Search] Scanning up to {min(len(items),10)} items for Temu results", flush=True)
            for item in items[:10]:
                source = (item.get("source") or "").lower()
                link = item.get("link") or ""
                if "temu" not in source and "temu" not in link.lower():
                    continue
                if len(results) >= limit:
                    break
                title = item.get("title") or ""
                price = item.get("price") or None
                thumbnail = item.get("thumbnail") or item.get("image") or None
                if not title:
                    continue
                url_id = self._generic_provider_item_id(link) if link else hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]
                nexus_id = self._build_generic_provider_nexus_id("temu", url_id, link or title)
                results.append(
                    ProductResult(
                        nexus_id=nexus_id,
                        site="temu",
                        title=title,
                        price=str(price) if price else None,
                        url=link,
                        image=thumbnail,
                        variations=[],
                    )
                )
            print(f"[Temu Search] Found {len(results)} Temu results after filter", flush=True)
            return results
        except Exception as exc:
            print(f"[Temu Search] Failed with exception: {exc}", flush=True)
            return []

    async def fast_search_amazon_serpapi(self, query: str, limit: int = 3) -> list[ProductResult]:
        api_key = os.getenv("SERPAPI_API_KEY")
        print(f"[Amazon SerpAPI] Key found: {bool(api_key)} query={query!r}", flush=True)
        if not api_key:
            print("[Amazon SerpAPI] Missing SERPAPI_API_KEY - returning empty", flush=True)
            return []

        normalized = query.strip().lower()
        # Use a short, brand/product focused query for better SerpApi results.
        simple = query
        if "adidas shoes" in normalized or "adidas" in normalized:
            simple = "adidas"
        if "nike shoes" in normalized or "nike" in normalized:
            simple = "nike"
        if "running shoes" in normalized:
            simple = "nike running shoes"
        if "sneakers" in normalized:
            simple = "nike sneakers"
        variants = list(dict.fromkeys([query, simple]))
        print(f"[Amazon SerpAPI] variants: {variants}", flush=True)

        url = "https://serpapi.com/search.json"
        all_results: list[ProductResult] = []

        def _parse_amazon(data: dict) -> list[ProductResult]:
            parsed: list[ProductResult] = []
            for item in data.get("organic_results", []) or []:
                if len(parsed) >= limit:
                    break
                title = item.get("title") or ""
                if not title:
                    continue
                link = item.get("link") or item.get("link_clean") or ""
                price = item.get("price") or item.get("extracted_price")
                thumbnail = item.get("thumbnail") or None
                asin = item.get("asin") or ""
                url_id = self._generic_provider_item_id(link) if link else (asin or hashlib.sha1(title.encode("utf-8")).hexdigest()[:12])
                nexus_id = self._build_generic_provider_nexus_id("amazon", url_id, link or title)
                parsed.append(
                    ProductResult(
                        nexus_id=nexus_id,
                        site="amazon",
                        title=title,
                        price=str(price) if price else None,
                        url=link,
                        image=thumbnail,
                        variations=[],
                    )
                )
            return parsed

        def _parse_shopping(data: dict) -> list[ProductResult]:
            parsed: list[ProductResult] = []
            for item in data.get("shopping_results", []) or []:
                if len(parsed) >= limit:
                    break
                source = (item.get("source") or "").lower()
                link = item.get("link") or ""
                if "amazon" not in source and "amazon" not in link.lower():
                    continue
                title = item.get("title") or ""
                price = item.get("price") or None
                thumbnail = item.get("thumbnail") or item.get("image") or None
                if not title:
                    continue
                url_id = self._generic_provider_item_id(link) if link else hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]
                nexus_id = self._build_generic_provider_nexus_id("amazon", url_id, link or title)
                parsed.append(
                    ProductResult(
                        nexus_id=nexus_id,
                        site="amazon",
                        title=title,
                        price=str(price) if price else None,
                        url=link,
                        image=thumbnail,
                        variations=[],
                    )
                )
            return parsed

        # Try SerpApi Amazon engine with up to two variants.
        for variant in variants:
            if len(all_results) >= limit:
                break
            params = {"engine": "amazon", "k": variant, "api_key": api_key, "amazon_domain": "amazon.com"}
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=8.0) as client:
                    response = await client.get(url, params=params)
                print(f"[Amazon SerpAPI] Amazon variant={variant!r} status={response.status_code}", flush=True)
                if response.status_code >= 400:
                    text = response.text[:300]
                    print(f"[Amazon SerpAPI] Amazon error: {text}", flush=True)
                    if "credits" in text.lower() or "quota" in text.lower() or "plan" in text.lower():
                        print("[Amazon SerpAPI] SERPAPI quota/credit issue detected", flush=True)
                    continue
                data = response.json()
                organic = data.get("organic_results", []) or []
                print(f"[Amazon SerpAPI] Amazon variant={variant!r} raw={len(organic)}", flush=True)
                all_results.extend(_parse_amazon(data))
                if all_results:
                    break
            except Exception as exc:
                print(f"[Amazon SerpAPI] Amazon variant={variant!r} failed: {exc}", flush=True)

        # Fallback to Google Shopping (filtered for Amazon) only if Amazon engine gave nothing.
        if not all_results:
            for variant in variants:
                if len(all_results) >= limit:
                    break
                params = {"engine": "google_shopping", "q": variant, "api_key": api_key, "gl": "us", "hl": "en", "tbm": "shop"}
                try:
                    async with httpx.AsyncClient(follow_redirects=True, timeout=8.0) as client:
                        response = await client.get(url, params=params)
                    print(f"[Amazon SerpAPI] Shopping variant={variant!r} status={response.status_code}", flush=True)
                    if response.status_code >= 400:
                        print(f"[Amazon SerpAPI] Shopping error: {response.text[:300]}", flush=True)
                        continue
                    data = response.json()
                    items = data.get("shopping_results", []) or []
                    print(f"[Amazon SerpAPI] Shopping variant={variant!r} raw={len(items)}", flush=True)
                    all_results.extend(_parse_shopping(data))
                    if all_results:
                        break
                except Exception as exc:
                    print(f"[Amazon SerpAPI] Shopping variant={variant!r} failed: {exc}", flush=True)

        print(f"[Amazon SerpAPI] Found {len(all_results)} results for query={query!r}", flush=True)
        return all_results[:limit]
    async def fast_search_http_provider(self, site: str, query: str, limit: int = 3) -> list[ProductResult]:
        config = self._http_provider_configs().get(site)
        if not config:
            return []
        if site == "ksp":
            return await self._fast_search_ksp(query, limit)
        if site == "super_pharm":
            return await self._fast_search_structured_provider(site, query, limit)
        if site == "ikea":
            return await self._fast_search_ikea(query, limit)
        if site in {"max_stock", "zol_stock"}:
            return await self._fast_search_stock_provider(site, query, limit)
        url = str(config["search_url"]).format(query=quote_plus(query))
        try:
            async with httpx.AsyncClient(headers=self._israeli_provider_headers(config["base_url"]), follow_redirects=True, timeout=6.0, verify=False) as client:
                response = await client.get(url)
            print(f"[{site} Search] HTTP status={response.status_code} url={url}", flush=True)
            if response.status_code >= 400:
                print(f"[{site} Search] Block/error status={response.status_code} preview={response.text[:300]!r}", flush=True)
                return []
            if self.page_has_no_results(response.text):
                print(f"[{site} Search] No-results page detected", flush=True)
                return []
        except Exception as exc:
            print(f"[{site} Search] HTTP failed: {exc}", flush=True)
            return []
        results = self._extract_generic_provider_items(site, response.text, config, limit)
        print(f"[{site} Search] Found {len(results)} results", flush=True)
        return results

    async def _fast_search_ksp(self, query: str, limit: int) -> list[ProductResult]:
        config = self._http_provider_configs()["ksp"]
        urls = [
            f"https://ksp.co.il/m_action.php?act=search&q={quote_plus(query)}",
            f"https://www.ksp.co.il/m_action.php?act=search&q={quote_plus(query)}",
            f"https://ksp.co.il/m_action/api/display_items?search={quote_plus(query)}",
            f"https://ksp.co.il/web/cat/0..0..0?q={quote_plus(query)}",
        ]
        async with httpx.AsyncClient(headers=self._israeli_provider_headers(config["base_url"]), follow_redirects=True, timeout=8.0, verify=False) as client:
            for url in urls:
                try:
                    response = await client.get(url)
                    print(f"[ksp Search] HTTP status={response.status_code} url={url}", flush=True)
                    if response.status_code >= 400:
                        continue
                    if self.page_has_no_results(response.text):
                        return []
                    data = self._safe_json_from_response(response)
                    results = self._extract_products_from_json("ksp", data, config, limit) if data is not None else []
                    if not results:
                        results = self._extract_generic_provider_items("ksp", response.text, config, limit)
                    if results:
                        print(f"[ksp Search] Found {len(results)} results", flush=True)
                        return results
                except Exception as exc:
                    print(f"[ksp Search] Attempt failed url={url}: {exc}", flush=True)
        return []

    async def _fast_search_structured_provider(self, site: str, query: str, limit: int) -> list[ProductResult]:
        config = self._http_provider_configs()[site]
        url = str(config["search_url"]).format(query=quote_plus(query))
        try:
            async with httpx.AsyncClient(headers=self._israeli_provider_headers(config["base_url"]), follow_redirects=True, timeout=8.0, verify=False) as client:
                response = await client.get(url)
            print(f"[{site} Search] HTTP status={response.status_code} url={url}", flush=True)
            if response.status_code >= 400 or self.page_has_no_results(response.text):
                return []
            data = self._safe_json_from_response(response)
            results = self._extract_products_from_json(site, data, config, limit) if data is not None else []
            if not results:
                results = self._extract_products_from_embedded_json(site, response.text, config, limit)
            if not results:
                results = self._extract_generic_provider_items(site, response.text, config, limit)
            print(f"[{site} Search] Found {len(results)} results", flush=True)
            return results
        except Exception as exc:
            print(f"[{site} Search] HTTP failed: {exc}", flush=True)
            return []

    async def _fast_search_ikea(self, query: str, limit: int) -> list[ProductResult]:
        config = self._http_provider_configs()["ikea"]
        url = f"https://www.ikea.co.il/catalogue/search?query={quote_plus(query)}"
        try:
            async with httpx.AsyncClient(headers=self._israeli_provider_headers(config["base_url"]), follow_redirects=True, timeout=8.0, verify=False) as client:
                response = await client.get(url)
            print(f"[ikea Search] HTTP status={response.status_code} url={url}", flush=True)
            if response.status_code >= 400 or self.page_has_no_results(response.text):
                return []
            data = self._safe_json_from_response(response)
            results = self._extract_products_from_json("ikea", data, config, limit) if data is not None else []
            if not results:
                results = self._extract_generic_provider_items("ikea", response.text, config, limit)
            print(f"[ikea Search] Found {len(results)} results", flush=True)
            return results
        except Exception as exc:
            print(f"[ikea Search] HTTP failed: {exc}", flush=True)
            return []

    async def _fast_search_stock_provider(self, site: str, query: str, limit: int) -> list[ProductResult]:
        config = self._http_provider_configs()[site]
        url = str(config["search_url"]).format(query=quote_plus(query))
        try:
            async with httpx.AsyncClient(headers=self._israeli_provider_headers(config["base_url"]), follow_redirects=True, timeout=8.0, verify=False) as client:
                response = await client.get(url)
            print(f"[{site} Search] HTTP status={response.status_code} url={url}", flush=True)
            if response.status_code >= 400 or self.page_has_no_results(response.text):
                return []
            results = self._extract_stock_grid_items(site, response.text, config, limit)
            if not results:
                results = self._extract_generic_provider_items(site, response.text, config, limit)
            print(f"[{site} Search] Found {len(results)} results", flush=True)
            return results
        except Exception as exc:
            print(f"[{site} Search] HTTP failed: {exc}", flush=True)
            return []

    async def get_http_provider_variations(self, site: str, url: str) -> list[VariationResult]:
        config = self._http_provider_configs().get(site)
        if not config:
            return []
        clean_url = self._normalize_provider_url(url, config)
        if not clean_url:
            return []
        try:
            async with httpx.AsyncClient(headers=self._israeli_provider_headers(config["base_url"]), follow_redirects=True, timeout=6.0, verify=False) as client:
                response = await client.get(clean_url)
            print(f"[{site} Details] HTTP status={response.status_code} url={clean_url}", flush=True)
            if response.status_code >= 400:
                return []
            return self._extract_generic_provider_variations(response.text)
        except Exception as exc:
            print(f"[{site} Details] Failed: {exc}", flush=True)
            return []

    @classmethod
    def _extract_generic_provider_items(cls, site: str, page_html: str, config: dict[str, str], limit: int) -> list[ProductResult]:
        embedded_results = cls._extract_products_from_embedded_json(site, page_html, config, limit)
        if embedded_results:
            print(f"[Scraper] {site} found {len(embedded_results)} raw elements", flush=True)
            return embedded_results[:limit]
        results: list[ProductResult] = []
        seen: set[str] = set()
        link_pattern = str(config.get("link_pattern") or r'href=["\']([^"\']+)["\']')
        matches = list(re.finditer(link_pattern, page_html, re.IGNORECASE))
        blocks = cls._fashion_product_blocks(page_html) if site in {"next", "nike", "adidas", "terminal_x", "shein"} else []
        print(f"[Scraper] {site} found {len(matches) + len(blocks)} raw elements", flush=True)
        for source in [*matches, *blocks]:
            if hasattr(source, "group"):
                href = html.unescape(source.group(1))
                block = page_html[max(0, source.start() - 2500): min(len(page_html), source.end() + 2500)]
            else:
                block = str(source)
                href_match = re.search(r'href=["\']([^"\']+)["\']', block, re.IGNORECASE)
                if not href_match:
                    continue
                href = html.unescape(href_match.group(1))
            product_url = cls._normalize_provider_url(href, config)
            if not product_url:
                continue
            item_id = cls._generic_provider_item_id(product_url)
            if item_id in seen:
                continue
            title = cls._extract_generic_provider_title(block)
            if not title:
                continue
            seen.add(item_id)
            results.append(
                ProductResult(
                    nexus_id=cls._build_generic_provider_nexus_id(site, item_id, product_url),
                    site=site,
                    title=title,
                    price=cls._extract_generic_provider_price(block),
                    url=product_url,
                    image=cls._normalize_relative_image(cls._extract_generic_provider_image(block), str(config.get("base_url", ""))),
                    variations=[],
                    provider_name=PROVIDER_NAMES.get(site, site),
                )
            )
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _fashion_product_blocks(page_html: str) -> list[str]:
        blocks = re.findall(
            r'<(?:article|li|div)[^>]+(?:class|data-testid|data-test|data-cy)=["\'][^"\']*(?:product-card|product_item|product-item|grid-item|product_shot|ProductShot|item-card|productCard|product-tile|productTile|plp-item|catalog-item|gallery-item)[^"\']*["\'][^>]*>[\s\S]{80,5000}?</(?:article|li|div)>',
            page_html,
            re.IGNORECASE,
        )
        if blocks:
            return blocks
        return re.findall(
            r'<(?:article|li)[^>]*>[\s\S]{80,5000}?</(?:article|li)>',
            page_html,
            re.IGNORECASE,
        )

    @classmethod
    def _extract_generic_provider_variations(cls, page_html: str) -> list[VariationResult]:
        price = cls._extract_generic_provider_price(page_html)
        variations: list[VariationResult] = []
        seen: set[str] = set()
        for select_match in re.finditer(r'<select[^>]*(?:size|Size|color|Color|colour|Colour|מידה|צבע)[^>]*>(.*?)</select>', page_html, re.IGNORECASE | re.DOTALL):
            select_html = select_match.group(0)
            variation_type = "color" if re.search(r'color|colour|צבע', select_html, re.IGNORECASE) else "size"
            for option in re.finditer(r'<option([^>]*)>(.*?)</option>', select_match.group(1), re.IGNORECASE | re.DOTALL):
                label = cls._clean_next_label(option.group(2))
                if not label:
                    continue
                in_stock = not re.search(r'disabled|out of stock|sold out|אזל|לא זמין', option.group(1) + label, re.IGNORECASE)
                key = f"{variation_type}::{label}"
                if key in seen:
                    continue
                seen.add(key)
                variations.append(VariationResult(type=variation_type, label=label, in_stock=in_stock, price=price))
        if variations:
            return variations[:40]
        for label in re.findall(r'"(?:size|Size|color|Color|colour|Colour|label|name)"\s*:\s*"([^"]{1,80})"', page_html):
            clean_label = cls._clean_next_label(label)
            if not clean_label:
                continue
            variation_type = "size" if cls._is_plausible_next_size(clean_label) else "color"
            key = f"{variation_type}::{clean_label}"
            if key in seen:
                continue
            seen.add(key)
            variations.append(VariationResult(type=variation_type, label=clean_label, in_stock=True, price=price))
        return variations[:40]

    @staticmethod
    def _safe_json_from_response(response: httpx.Response) -> Any | None:
        try:
            return response.json()
        except Exception:
            text = response.text.strip()
            if not text:
                return None
            match = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', text)
            if not match:
                return None
            try:
                return json.loads(match.group(1))
            except Exception:
                return None

    @classmethod
    def _extract_products_from_json(cls, site: str, data: Any, config: dict[str, str], limit: int) -> list[ProductResult]:
        candidates: list[dict[str, Any]] = []
        if site == "ksp" and isinstance(data, dict):
            items = data.get("result", {}).get("items") if isinstance(data.get("result"), dict) else None
            if isinstance(items, dict):
                candidates.extend(value for value in items.values() if isinstance(value, dict))
            elif isinstance(items, list):
                candidates.extend(value for value in items if isinstance(value, dict))
        cls._collect_product_like_dicts(data, candidates)
        results: list[ProductResult] = []
        seen: set[str] = set()
        for item in candidates:
            try:
                title = cls._json_first_text(item, ("title", "name", "productName", "description", "product_name", "itemName", "text"))
                url = cls._json_first_text(item, ("url", "link", "productUrl", "product_url", "href", "seoUrl"))
                price = cls._json_first_text(item, ("price", "finalPrice", "salePrice", "displayPrice", "priceFormatted", "actualPrice", "minPrice"))
                sku = cls._json_first_text(item, ("id", "sku", "productId", "itemId", "itemUin", "uin", "uinsql", "code", "catalogNumber", "pid"))
                if not title:
                    continue
                try:
                    image = cls._json_first_text(item, ("image", "imageUrl", "img", "itemImg", "thumbnail", "picture", "mediaUrl"))
                    image = cls._normalize_json_image(image, config)
                except Exception as img_err:
                    print(f"[Scraper] {site} image extraction error: {img_err}", flush=True)
                    image = None
                product_url = cls._normalize_provider_url(url, config) if url else None
                if not product_url:
                    product_url = str(config["base_url"])
                    if sku:
                        product_url = f"{product_url.rstrip()}/web/item/{sku}" if site == "ksp" else f"{product_url.rstrip('/')}/item/{sku}"
                item_id = re.sub(r"\W+", "", str(sku or cls._generic_provider_item_id(product_url))).lower()[:48]
                if item_id in seen:
                    continue
                seen.add(item_id)
                results.append(
                    ProductResult(
                        nexus_id=cls._build_generic_provider_nexus_id(site, item_id, product_url),
                        site=site,
                        title=title,
                        price=cls._clean_price(price),
                        url=product_url,
                        image=image,
                        variations=[],
                        provider_name=PROVIDER_NAMES.get(site, site),
                    )
                )
            except Exception as item_err:
                print(f"[Scraper] {site} item parse error: {item_err}", flush=True)
                continue
            if len(results) >= limit:
                break
        return results

    @classmethod
    def _collect_product_like_dicts(cls, data: Any, candidates: list[dict[str, Any]]) -> None:
        if isinstance(data, dict):
            keys = {str(key).lower() for key in data.keys()}
            has_title = bool(keys & {"title", "name", "productname", "description", "product_name", "itemname"})
            has_product_signal = bool(keys & {"price", "finalprice", "saleprice", "productid", "sku", "itemid", "itemuin", "uin", "uinsql", "image", "imageurl", "itemimg", "img", "thumbnail"})
            if has_title and has_product_signal:
                candidates.append(data)
            for value in data.values():
                cls._collect_product_like_dicts(value, candidates)
        elif isinstance(data, list):
            for value in data:
                cls._collect_product_like_dicts(value, candidates)

    @staticmethod
    def _json_first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str | None:
        lowered = {str(key).lower(): value for key, value in item.items()}
        for key in keys:
            value = lowered.get(key.lower())
            if isinstance(value, (str, int, float)) and str(value).strip():
                return html.unescape(str(value)).strip()
            if isinstance(value, list):
                for elem in value:
                    if isinstance(elem, str) and elem.strip():
                        return html.unescape(elem).strip()
                    if isinstance(elem, dict):
                        nested = AmazonAgent._json_first_text(elem, keys)
                        if nested:
                            return nested
            if isinstance(value, dict):
                nested = AmazonAgent._json_first_text(value, keys)
                if nested:
                    return nested
        return None

    @classmethod
    def _extract_products_from_embedded_json(cls, site: str, page_html: str, config: dict[str, str], limit: int) -> list[ProductResult]:
        for script in re.findall(r'<script[^>]+type=["\']application/(?:ld\+)?json["\'][^>]*>(.*?)</script>', page_html, re.IGNORECASE | re.DOTALL):
            try:
                data = json.loads(html.unescape(script).strip())
            except Exception:
                continue
            results = cls._extract_products_from_json(site, data, config, limit)
            if results:
                return results
        for match in re.finditer(r'(?:window\.__INITIAL_STATE__|__NEXT_DATA__|digitalData|dataLayer)\s*=?\s*(\{[\s\S]{100,}?\})\s*[;<]', page_html):
            try:
                data = json.loads(match.group(1))
            except Exception:
                continue
            results = cls._extract_products_from_json(site, data, config, limit)
            if results:
                return results
        return []

    @classmethod
    def _extract_stock_grid_items(cls, site: str, page_html: str, config: dict[str, str], limit: int) -> list[ProductResult]:
        results: list[ProductResult] = []
        seen: set[str] = set()
        blocks = re.findall(
            r'<(?:li|div|article)[^>]+(?:class|data-testid)=["\'][^"\']*(?:product|item|catalog|grid|card)[^"\']*["\'][^>]*>[\s\S]{80,2500}?</(?:li|div|article)>',
            page_html,
            re.IGNORECASE,
        )
        for block in blocks:
            href_match = re.search(r'href=["\']([^"\']+)["\']', block)
            product_url = cls._normalize_provider_url(html.unescape(href_match.group(1)), config) if href_match else None
            title = cls._extract_generic_provider_title(block)
            if not title:
                continue
            product_url = product_url or str(config["base_url"])
            item_id = cls._generic_provider_item_id(product_url + title)
            if item_id in seen:
                continue
            seen.add(item_id)
            stock_match = re.search(r'(?:במלאי|זמין|אזל|out of stock|in stock|available)', block, re.IGNORECASE)
            variations = []
            if stock_match:
                variations.append(VariationResult(type="availability", label=html.unescape(stock_match.group(0)), in_stock=not re.search(r'אזל|out of stock', stock_match.group(0), re.IGNORECASE), price=cls._extract_generic_provider_price(block)))
            results.append(
                ProductResult(
                    nexus_id=cls._build_generic_provider_nexus_id(site, item_id, product_url),
                    site=site,
                    title=title,
                    price=cls._extract_generic_provider_price(block),
                    url=product_url,
                    image=cls._normalize_relative_image(cls._extract_generic_provider_image(block), str(config.get("base_url", ""))),
                    variations=variations,
                    provider_name=PROVIDER_NAMES.get(site, site),
                )
            )
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _normalize_json_image(image: str | None, config: dict[str, str]) -> str | None:
        if not image:
            return None
        image = html.unescape(image).strip()
        if image.startswith("//"):
            return f"https:{image}"
        if image.startswith("/"):
            return f"{str(config['base_url']).rstrip('/')}{image}"
        return image if image.startswith("http") else None

    @staticmethod
    def _http_provider_configs() -> dict[str, dict[str, str]]:
        return {
            "nike": {"base_url": "https://www.nike.com", "search_url": "https://www.nike.com/il/w?q={query}", "link_pattern": r'href=["\']([^"\']*/il/t/[^"\']+)["\']'},
            "adidas": {"base_url": "https://www.adidas.co.il", "search_url": "https://www.adidas.co.il/he/search?q={query}", "link_pattern": r'href=["\']([^"\']*/[^"\']*\.html[^"\']*)["\']'},
            "super_pharm": {"base_url": "https://shop.super-pharm.co.il", "search_url": "https://shop.super-pharm.co.il/search?q={query}", "link_pattern": r'href=["\']([^"\']*/(?:p|product|products)/[^"\']+)["\']'},
            "ksp": {"base_url": "https://ksp.co.il", "search_url": "https://ksp.co.il/web/cat/0..0..0?q={query}", "link_pattern": r'href=["\']([^"\']*(?:item|web/item|mob/item)[^"\']+)["\']'},
            "machsanei_hashmal": {"base_url": "https://www.payless.co.il", "search_url": "https://www.payless.co.il/search?q={query}", "link_pattern": r'href=["\']([^"\']*/(?:product|item)/[^"\']+)["\']'},
            "max_stock": {"base_url": "https://www.maxstock.co.il", "search_url": "https://www.maxstock.co.il/search?q={query}", "link_pattern": r'href=["\']([^"\']*/(?:product|products|catalog)/[^"\']+)["\']'},
            "zol_stock": {"base_url": "https://www.zolstock.co.il", "search_url": "https://www.zolstock.co.il/?s={query}&post_type=product", "link_pattern": r'href=["\']([^"\']*/(?:product|products|shop)/[^"\']+)["\']'},
            "ikea": {"base_url": "https://www.ikea.co.il", "search_url": "https://www.ikea.co.il/catalogue/search?query={query}", "link_pattern": r'href=["\']([^"\']*/catalogue/[^"\']+)["\']'},
            "ivory": {"base_url": "https://www.ivory.co.il", "search_url": "https://www.ivory.co.il/catalog.php?act=cat&q={query}", "link_pattern": r'href=["\']([^"\']*(?:catalog\.php\?id=|product|item)[^"\']+)["\']'},
            "terminal_x": {"base_url": "https://www.terminalx.com", "search_url": "https://www.terminalx.com/catalogsearch/result/?q={query}", "link_pattern": r'href=["\']([^"\']*/(?:item|product|p)/[^"\']+)["\']'},
            "be_pharm": {"base_url": "https://www.bestore.co.il", "search_url": "https://www.bestore.co.il/search?q={query}", "link_pattern": r'href=["\']([^"\']*/(?:p|product|products)/[^"\']+)["\']'},
            "shufersal": {"base_url": "https://www.shufersal.co.il", "search_url": "https://www.shufersal.co.il/online/he/search?text={query}", "link_pattern": r'href=["\']([^"\']*/(?:P|p|product|products)/[^"\']+)["\']'},
            "shein": {"base_url": "https://il.shein.com", "search_url": "https://il.shein.com/pdsearch/{query}/", "link_pattern": r'href=["\']([^"\']*/[^"\']*-p-\d+[^"\']*\.html[^"\']*)["\']'},
        }

    @staticmethod
    def _normalize_provider_url(url: str | None, config: dict[str, str]) -> str | None:
        if not url:
            return None
        base_url = str(config["base_url"])
        clean_url = html.unescape(url).strip()
        if clean_url.startswith("//"):
            clean_url = f"https:{clean_url}"
        elif clean_url.startswith("/"):
            clean_url = f"{base_url}{clean_url}"
        if not clean_url.startswith("http"):
            return None
        parsed = urlparse(clean_url)
        base_host = urlparse(base_url).netloc.replace("www.", "")
        if base_host not in parsed.netloc.replace("www.", ""):
            return None
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    @staticmethod
    def _generic_provider_item_id(url: str) -> str:
        parts = [part for part in urlparse(url).path.split("/") if part]
        for part in reversed(parts):
            token = re.sub(r"\W+", "", part).lower()
            if len(token) >= 4:
                return token[:48]
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _build_generic_provider_nexus_id(site: str, item_id: str, value: str) -> str:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
        return f"{site}_{item_id}_{digest}"

    @staticmethod
    def _extract_generic_provider_title(block: str) -> str | None:
        for pattern in (r'<h[1-4][^>]*>(.*?)</h[1-4]>', r'(?:aria-label|title|alt)=["\']([^"\']{3,180})["\']', r'"(?:name|title|productName)"\s*:\s*"([^"]{3,180})"'):
            match = re.search(pattern, block, re.IGNORECASE | re.DOTALL)
            if match:
                label = AmazonAgent._clean_next_label(match.group(1))
                if label and not re.search(r'logo|search|wishlist|account|cart|bag|menu|terminal x|לעבור לדף הבית|מזמינים היום|משלוח|התחברות|הרשמה|סל קניות', label, re.IGNORECASE):
                    return label
        return None

    @staticmethod
    def _extract_generic_provider_price(block: str) -> str | None:
        for pattern in (r'(₪\s?[\d,.]+)', r'(\d+(?:[,.]\d+)?\s?₪)', r'((?:ILS|NIS)\s?[\d,.]+)', r'"(?:price|finalPrice|sellingPrice|displayPrice)"\s*:\s*"?([^",}]+)"?'):
            match = re.search(pattern, block, re.IGNORECASE)
            if match:
                return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()
        return None

    @staticmethod
    def _normalize_relative_image(image: str | None, base_url: str) -> str | None:
        if not image or image.startswith("http") or image.startswith("//"):
            return image
        if base_url:
            return f"{base_url.rstrip('/')}/{image.lstrip('/')}"
        return None

    @staticmethod
    def _extract_generic_provider_image(block: str) -> str | None:
        _SKIP = ("placeholder", "nogray", "yes.png", "no.png", "stock/", "spinner",
                 "loading", "blank", "default", "nophoto", "noimage", "logo")
        for attr in ("data-src", "data-lazy", "src"):
            for m in re.finditer(
                attr + r"""=["\']([^"\'> ]+\.(?:jpg|jpeg|png|webp|gif)[^"\'> ]*)["\']""",
                block, re.IGNORECASE,
            ):
                raw = html.unescape(m.group(1)).strip()
                if any(p in raw.lower() for p in _SKIP):
                    continue
                if raw.startswith("//"):
                    return f"https:{raw}"
                if raw.startswith("http"):
                    return raw
                if raw and not raw.startswith("#"):
                    return raw
        full_url = re.search(r'(https?://[^"\'<\s]+?\.(?:jpg|jpeg|png|webp)(?:[^"\'<\s]*)?)', block, re.IGNORECASE)
        if full_url:
            raw = html.unescape(full_url.group(1))
            if not any(p in raw.lower() for p in _SKIP):
                return raw
        return None

    @staticmethod
    def _israeli_provider_headers(base_url: str) -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": base_url,
        }

    async def _require_page(self) -> Page:
        if self._page is None:
            await self.start()
        if self._page is None:
            raise RuntimeError("Browser page was not initialized")
        return self._page

    @staticmethod
    async def _safe_inner_text(locator: Any) -> str | None:
        try:
            text = await locator.inner_text(timeout=3000)
            return text.strip() or None
        except Exception:
            return None

    @staticmethod
    async def _safe_attribute(locator: Any, name: str) -> str | None:
        try:
            value = await locator.get_attribute(name, timeout=3000)
            return value.strip() if value else None
        except Exception:
            return None

    @staticmethod
    async def _safe_click(locator: Any) -> bool:
        try:
            await locator.click(timeout=3000)
            return True
        except Exception:
            return False

    @staticmethod
    async def _safe_evaluate(locator: Any, expression: str) -> Any:
        try:
            return await locator.evaluate(expression, timeout=3000)
        except Exception as exc:
            print(f"[Stage] Evaluate failed: {exc}", flush=True)
            return None

    @staticmethod
    async def _safe_select_option(locator: Any) -> bool:
        try:
            value = await locator.get_attribute("value", timeout=3000)
            if not value or value.startswith("-"):
                return False
            select = locator.locator("xpath=ancestor::select").first
            await select.select_option(value=value, timeout=3000)
            return True
        except Exception:
            return False


    @staticmethod
    async def _block_detail_resources(route: Any) -> None:
        if route.request.resource_type in {"image", "stylesheet", "font", "media"}:
            await route.abort()
            return
        await route.continue_()

    @staticmethod
    async def _block_heavy_resources(route: Any) -> None:
        if route.request.resource_type in {"image", "stylesheet", "font", "media"}:
            await route.abort()
            return
        await route.continue_()
