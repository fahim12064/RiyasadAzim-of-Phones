import asyncio
import json
import csv
import os
import re
from playwright.async_api import async_playwright, Playwright

# --- CONFIGURATION ---
TARGET_URL = "https://www.mobiledokan.co/products/"
OUTPUT_FOLDER = 'mobiles'
PROCESSED_LINKS_CSV = 'processed_links.csv'
HEADLESS_MODE = True


# --- HELPER FUNCTIONS ---
def load_processed_links():
    if not os.path.exists(PROCESSED_LINKS_CSV): return set()
    with open(PROCESSED_LINKS_CSV, mode='r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        try:
            next(reader)
            return {row[0] for row in reader}
        except StopIteration:
            return set()


def save_processed_link(link):
    write_header = not os.path.exists(PROCESSED_LINKS_CSV)
    with open(PROCESSED_LINKS_CSV, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if write_header: writer.writerow(['processed_url'])
        writer.writerow([link])


def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name)


async def get_text_or_default(locator, default="Not available"):
    try:
        return (await locator.inner_text()).strip()
    except Exception:
        return default


# --- NEW: JSON FORMATTING FUNCTION ---
def format_scraped_data(raw_data):
    """
    Takes the raw scraped dictionary and transforms it into the desired final JSON structure.
    """
    formatted = {
        "title": raw_data.get("title", "N/A"),
        "brand": raw_data.get("brand", "N/A"),
        "category": raw_data.get("category", "N/A"),
        "added_on": raw_data.get("added_on", "N/A"),
        "status": raw_data.get("status", "N/A"),
    }

    # Extract and format specific fields
    launch_info = raw_data.get("Launch", {})
    formatted["announced_date"] = launch_info.get("Announced", "N/A")

    # Extract expected release from the status string
    status_string = launch_info.get("Status", "")
    release_match = re.search(r'Exp\. release (.*)', status_string)
    if release_match:
        formatted["expected_release"] = release_match.group(1).strip()
    else:
        formatted["expected_release"] = formatted["announced_date"]

    # Price formatting
    price_amount_raw = raw_data.get("price", {}).get("amount", "0")
    price_amount = int(re.sub(r'[^\d]', '', price_amount_raw)) if re.sub(r'[^\d]', '', price_amount_raw) else 0
    formatted["price"] = {
        "local_currency": "BDT",
        "amount": price_amount,
        "note": "official price" if price_amount > 0 else ""
    }

    # Map raw scraped groups to new formatted groups
    # This structure allows for easy renaming and combining of fields
    mapping = {
        "Camera": ["Main camera", "Selfie camera"],
        "Design": ["Body"],
        "Battery": ["Battery"],
        "Display": ["Display"],
        "Cellular": ["Network"],
        "Hardware": ["Platform", "Memory"],
        "Multimedia": ["Sound"],
        "Connectivity & Features": ["Connectivity", "Features"]
    }

    for new_group, old_groups in mapping.items():
        formatted[new_group] = {}
        for old_group in old_groups:
            if old_group in raw_data:
                # Add a colon to the key for the desired format
                for key, value in raw_data[old_group].items():
                    formatted[new_group][f"{key}:"] = value

    return formatted


# --- SCRAPING LOGIC (NO LONGER CLICKS TAB) ---
async def scrape_product_details(page, url):
    """Scrapes all raw data from the product page."""
    await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_selector('div.aps-single-product', timeout=30000)

    raw_data = {}

    # Scrape Basic Info
    raw_data['title'] = await get_text_or_default(page.locator('h1.aps-main-title'))
    raw_data['brand'] = await get_text_or_default(page.locator('.aps-product-brand a'))
    raw_data['category'] = await get_text_or_default(page.locator('.aps-product-cat a'))
    added_on_raw = await get_text_or_default(page.locator('.aps-product-added'))
    raw_data['added_on'] = added_on_raw.replace('Added on:', '').strip()
    last_updated_raw = await get_text_or_default(page.locator('.aps-product-updated'))
    raw_data['last_updated'] = last_updated_raw.replace('Last updated:', '').strip()
    raw_data['price'] = {"amount": await get_text_or_default(page.locator('.aps-product-price .aps-price-value'))}
    raw_data['status'] = await get_text_or_default(page.locator('.aps-status span'))

    # Scrape All Specification Tables
    spec_groups = await page.locator('div#aps-specs .aps-group').all()
    for group in spec_groups:
        group_title_raw = await get_text_or_default(group.locator('h3.aps-group-title'))
        clean_title = re.sub(r'\s+[^\w\s]+$', '', group_title_raw).strip()
        if not clean_title: continue

        raw_data[clean_title] = {}
        rows = await group.locator('table tr').all()
        for row in rows:
            key = await get_text_or_default(row.locator('td.aps-attr-title strong.aps-term'), default=None)
            value_loc = row.locator('td.aps-attr-value')
            value = await get_text_or_default(value_loc, default=None)

            if key and value:
                clean_key = key.strip().removesuffix(':')
                if 'aps-icon-cancel' in (await value_loc.inner_html()):
                    value = "No"
                elif 'aps-icon-check' in (await value_loc.inner_html()):
                    value = "Yes"
                raw_data[clean_title][clean_key] = value.strip().replace('\n', ' ')

    return raw_data


# --- MAIN EXECUTION (WITH PROGRESS COUNTER) ---
async def run(playwright: Playwright):
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    context = await playwright.chromium.launch(headless=HEADLESS_MODE)
    page = await context.new_page()

    try:
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=120000)

        product_link_selector = "ul.aps-products li .aps-product-thumb a"
        await page.wait_for_selector(product_link_selector, timeout=30000)

        link_elements = await page.query_selector_all(product_link_selector)
        all_links = {await el.get_attribute('href') for el in link_elements if await el.get_attribute('href')}

        processed_links = load_processed_links()
        new_links = list(all_links - processed_links)  # Convert to list to use index

        if not new_links:
            print("No new products to scrape. All links have been processed before.")
            await context.close()
            return

        total_to_scrape = len(new_links)
        print(f"Found {total_to_scrape} new products to scrape.")

        # Loop with index for progress counter
        for i, link in enumerate(new_links):
            progress = f"Scraping {i + 1}/{total_to_scrape}:"
            print(f"\n{progress} {link}")
            try:
                # 1. Scrape raw data
                raw_details = await scrape_product_details(page, link)

                # 2. Format the raw data into the final structure
                final_details = format_scraped_data(raw_details)

                # 3. Save the final, formatted data
                filename = sanitize_filename(final_details.get('title', 'unknown_product')) + '.json'
                filepath = os.path.join(OUTPUT_FOLDER, filename)

                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(final_details, f, indent=2, ensure_ascii=False)

                save_processed_link(link)
                print(f"  -> ✔ Successfully scraped and saved to '{filepath}'")
            except Exception as e:
                print(f"  -> ❌ FAILED to scrape {link}. Error: {e}")

    except Exception as e:
        print(f"A critical error occurred: {e}")
    finally:
        await context.close()
        print("\nScraping session finished. Browser context closed.")


async def main():
    async with async_playwright() as playwright:
        await run(playwright)


if __name__ == "__main__":
    asyncio.run(main())
