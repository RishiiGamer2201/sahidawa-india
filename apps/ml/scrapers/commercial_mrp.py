"""
SahiDawa — Commercial Pharmacy MRP Scraper
=============================================
Source: 1mg.com (Apollo Pharmacy alternative)

Target: Extract commercial market MRPs for medicines to compare against
        Jan Aushadhi subsidized prices in the Savings Comparison UI.

WHY 1MG:
    - Publicly accessible without login for basic search
    - Well-structured product listings with MRP displayed
    - Provides brand name, generic name, and strength

WHAT THIS SCRAPER DOES:
    1. Searches for medicines by generic name from Jan Aushadhi dataset
    2. Extracts brand name, composition, and MRP from search results
    3. Saves CSV to data/raw/commercial_mrp/
    4. Handles pagination and rate-limiting (1 second delay between requests)

HOW TO RUN:
    cd apps/ml
    python -m scrapers.commercial_mrp

PREREQUISITE:
    pip install playwright
    playwright install chromium
"""

import asyncio
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright


# ── Constants ──────────────────────────────────────────────────────────────────

SEARCH_URL = "https://www.1mg.com/search?search={query}"

# 1mg displays results in pages, we'll fetch first few pages per search
MAX_PAGES = 2
MEDICINES_PER_PAGE = 15
REQUEST_DELAY = 1.5  # seconds between requests (rate limiting)

RAW_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "raw" / "commercial_mrp"

# Sample medicines for initial scraping (these would come from Jan Aushadhi data)
SAMPLE_MEDICINES = [
    "Paracetamol",
    "Amoxicillin",
    "Azithromycin",
    "Cetirizine",
    "Metformin",
    "Amlodipine",
    "Omeprazole",
    "Pantoprazole",
    "Atorvastatin",
    "Metoprolol",
]


# ── Main Scraper Class ─────────────────────────────────────────────────────────


class CommercialMRPScraper:
    """
    Scraper for extracting commercial MRPs from 1mg.com.
    Uses Playwright to handle JavaScript-rendered pages.
    """

    def __init__(self):
        RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.results: list[dict[str, Any]] = []

    async def scrape(self, medicines: list[str] | None = None) -> Path:
        """
        Main entry point. Scrapes MRPs for given medicine list.

        Args:
            medicines: List of medicine names to search (default: SAMPLE_MEDICINES)

        Returns:
            Path to the saved CSV file
        """
        medicines_to_scrape = medicines or SAMPLE_MEDICINES
        print(f"[CommercialMRP] Starting scrape for {len(medicines_to_scrape)} medicines")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                accept_downloads=True,
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            for idx, medicine in enumerate(medicines_to_scrape):
                print(f"[CommercialMRP] [{idx + 1}/{len(medicines_to_scrape)}] Searching: {medicine}")

                try:
                    await self._search_medicine(page, medicine)
                except Exception as e:
                    print(f"[CommercialMRP] ⚠️ Failed to search {medicine}: {e}")

                # Rate limiting: wait between searches
                if idx < len(medicines_to_scrape) - 1:
                    delay = REQUEST_DELAY + random.uniform(0, 0.5)
                    await asyncio.sleep(delay)

            await browser.close()

        # Save results to CSV
        csv_path = await self._save_results()
        return csv_path

    async def _search_medicine(self, page, medicine: str) -> None:
        """Search for a medicine and extract results."""
        query = medicine.replace(" ", "+")
        url = SEARCH_URL.format(query=query)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2000)  # Wait for results to load

            # Check if we got search results
            products = await page.locator(".style__product-box").all()
            if not products:
                products = await page.locator(".product-card").all()
            if not products:
                products = await page.locator("[data-testid='product-card']").all()

            print(f"[CommercialMRP] Found {len(products)} results for '{medicine}'")

            for product in products[:MEDICINES_PER_PAGE]:
                try:
                    result = await self._extract_product_info(product, medicine)
                    if result:
                        self.results.append(result)
                except Exception as e:
                    print(f"[CommercialMRP] ⚠️ Failed to extract product: {e}")
                    continue

        except Exception as e:
            print(f"[CommercialMRP] ⚠️ Error searching {medicine}: {e}")

    async def _extract_product_info(self, product, search_term: str) -> dict | None:
        """Extract brand, generic name, strength, and MRP from a product card."""
        try:
            # Try multiple selectors for brand name
            brand = None
            for selector in [
                ".style__product-title",
                ".product-title",
                "[data-testid='product-title']",
                ".product-card__title",
            ]:
                try:
                    brand_elem = product.locator(selector).first
                    if await brand_elem.count() > 0:
                        brand = await brand_elem.inner_text()
                        break
                except:
                    continue

            if not brand:
                return None

            # Extract MRP - try multiple selectors
            mrp = None
            for selector in [
                ".style__price-tag",
                ".price-tag",
                "[data-testid='price']",
                ".product-card__price",
            ]:
                try:
                    mrp_elem = product.locator(selector).first
                    if await mrp_elem.count() > 0:
                        mrp_text = await mrp_elem.inner_text()
                        mrp = self._parse_mrp(mrp_text)
                        break
                except:
                    continue

            # Try to find generic name/composition
            composition = None
            for selector in [
                ".style__composition",
                ".product-composition",
                "[data-testid='product-composition']",
            ]:
                try:
                    comp_elem = product.locator(selector).first
                    if await comp_elem.count() > 0:
                        composition = await comp_elem.inner_text()
                        break
                except:
                    continue

            # Extract strength from composition if available
            strength = self._extract_strength(composition or brand)

            return {
                "search_term": search_term,
                "brand_name": brand.strip() if brand else None,
                "generic_name": search_term,
                "composition": composition.strip() if composition else None,
                "strength": strength,
                "mrp": mrp,
                "source": "1mg",
                "scraped_at": datetime.now().isoformat(),
            }

        except Exception as e:
            print(f"[CommercialMRP] ⚠️ Extract error: {e}")
            return None

    def _parse_mrp(self, mrp_text: str) -> float | None:
        """Parse MRP from text like '₹150' or 'Rs. 150' or '150.00'."""
        if not mrp_text:
            return None

        # Remove currency symbols and whitespace
        cleaned = re.sub(r"[₹Rs.\s,]", "", mrp_text.strip())

        # Extract numeric value
        match = re.search(r"[\d.]+", cleaned)
        if match:
            try:
                return float(match.group())
            except ValueError:
                return None

        return None

    def _extract_strength(self, text: str | None) -> str | None:
        """Extract strength from composition or name."""
        if not text:
            return None

        pattern = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|iu|%)", re.IGNORECASE)
        matches = pattern.findall(text)

        if not matches:
            return None

        return " + ".join(f"{val}{unit}" for val, unit in matches)

    async def _save_results(self) -> Path:
        """Save scraped results to CSV."""
        import pandas as pd

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = RAW_DATA_DIR / f"commercial_mrp_raw_{timestamp}.csv"

        df = pd.DataFrame(self.results)
        df.to_csv(save_path, index=False)

        print(f"[CommercialMRP] ✅ Saved {len(self.results)} records to: {save_path}")
        return save_path


# ── Runner ─────────────────────────────────────────────────────────────────────


async def main():
    scraper = CommercialMRPScraper()
    csv_path = await scraper.scrape()
    print(f"\n[CommercialMRP] Raw data ready at: {csv_path}")
    return csv_path


if __name__ == "__main__":
    asyncio.run(main())