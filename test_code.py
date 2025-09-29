import asyncio
import json
import csv
import os
import re
from io import BytesIO
import requests
from PIL import Image
from playwright.async_api import async_playwright, Playwright

# --- CONFIGURATION ---
TARGET_URL = "https://www.mobiledokan.co/products/"
JSON_OUTPUT_FOLDER = 'mobiles'  # Folder for JSON files
IMAGE_OUTPUT_FOLDER = 'images'  # Folder for Image files
PROCESSED_LINKS_CSV = 'processed_links.csv'
HEADLESS_MODE = True


# --- HELPER FUNCTIONS ---

def download_and_resize_image(url, save_path, width=300):
    """Downloads an image from a URL, resizes it, and saves it."""
    if not url:
        print("  -> ❌ Image URL missing. Skipping download.")
        return
    try:
        # Create the directory for the image if it doesn't exist
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        response = requests.get(url, timeout=15)
        response.raise_for_status()

        img = Image.open(BytesIO(response.content))

        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        w_percent = (width / float(img.size[0]))
        height = int((float(img.size[1]) * float(w_percent)))
        img_resized = img.resize((width, height), Image.Resampling.LANCZOS)

        img_resized.save(save_path, 'jpeg')
        print(f"  -> 🖼️  Resized image saved: {save_path}")
    except Exception as e:
        print(f"  -> ❌ Error downloading/resizing image: {e}")


# (Other helper functions remain the same)
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


# --- JSON FORMATTING FUNCTION (No changes here) ---
def format_scraped_data(raw_data):
    formatted = {"title": raw_data.get("title", "N/A"), "brand": raw_data.get("brand", "N/A"),
                 "category": raw_data.get("category", "N/A"), "added_on": raw_data.get("added_on", "N/A"),
                 "status": raw_data.get("status", "N/A")}
    launch_info = raw_data.get("Launch", {})
    formatted["announced_date"] = launch_info.get("Announced", "N/A")
    status_string = launch_info.get("Status", "")
    release_match = re.search(r'Exp\. release (.*)', status_string)
    if release_match:
        formatted["expected_release"] = release_match.group(1).strip()
    else:
        formatted["expected_release"] = formatted["announced_date"]
    price_amount_raw = raw_data.get("price", {}).get("amount", "0")
    price_amount = int(re.sub(r'[^\d]', '', price_amount_raw)) if re.sub(r'[^\d]', '', price_amount_raw) else 0
    formatted["price"] = {"local_currency": "BDT", "amount": price_amount,
                          "note": "official price" if price_amount > 0 else ""}
    mapping = {"Camera": ["Main camera", "Selfie camera"], "Design": ["Body"], "Battery": ["Battery"],
               "Display": ["Display"], "Cellular": ["Network"], "Hardware": ["Platform", "Memory"],
               "Multimedia": ["Sound"], "Connectivity & Features": ["Connectivity", "Features"]}
    for new_group, old_groups in mapping.items():
        formatted[new_group] = {}
        for old_group in old_groups:
            if old_group in raw_data:
                for key, value in raw_data[old_group].items():
                    formatted[new_group][f"{key}:"] = value
    return formatted


# --- SCRAPING LOGIC (No changes here) ---
async def scrape_product_details(page, url):
    await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_selector('div.aps-single-product', timeout=30000)
    raw_data = {}
    image_locator = page.locator('.aps-main-image img.aps-image-zoom')
    raw_data['image_url'] = await image_locator.get_attribute('src')
    raw_data['title'] = await get_text_or_default(page.locator('h1.aps-main-title'))
    raw_data['brand'] = await get_text_or_default(page.locator('.aps-product-brand a'))
    raw_data['category'] = await get_text_or_default(page.locator('.aps-product-cat a'))
    added_on_raw = await get_text_or_default(page.locator('.aps-product-added'))
    raw_data['added_on'] = added_on_raw.replace('Added on:', '').strip()
    raw_data['price'] = {"amount": await get_text_or_default(page.locator('.aps-product-price .aps-price-value'))}
    raw_data['status'] = await get_text_or_default(page.locator('.aps-status span'))
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


# --- UPDATED MAIN EXECUTION ---
async def run(playwright: Playwright):
    # Create the output folders if they don't exist
    os.makedirs(JSON_OUTPUT_FOLDER, exist_ok=True)
    os.makedirs(IMAGE_OUTPUT_FOLDER, exist_ok=True)

    context = await playwright.chromium.launch(headless=HEADLESS_MODE)
    page = await context.new_page()

    try:
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=120000)
        link_elements = await page.query_selector_all("ul.aps-products li .aps-product-thumb a")
        all_links = {await el.get_attribute('href') for el in link_elements if await el.get_attribute('href')}
        processed_links = load_processed_links()
        new_links = list(all_links - processed_links)

        if not new_links:
            print("No new products to scrape.")
            await context.close()
            return

        total_to_scrape = len(new_links)
        print(f"Found {total_to_scrape} new products to scrape.")

        for i, link in enumerate(new_links):
            progress = f"Scraping {i + 1}/{total_to_scrape}:"
            print(f"\n{progress} {link}")
            try:
                raw_details = await scrape_product_details(page, link)
                final_details = format_scraped_data(raw_details)

                base_filename = sanitize_filename(final_details.get('title', 'unknown_product'))

                # *** CORRECTED FILE PATHS ***
                # JSON file goes into the 'mobiles' folder
                json_filepath = os.path.join(JSON_OUTPUT_FOLDER, base_filename + '.json')

                # Image file goes into the 'images' folder
                image_filepath = os.path.join(IMAGE_OUTPUT_FOLDER, base_filename + '.jpg')

                # Trigger download and resize
                download_and_resize_image(raw_details.get('image_url'), image_filepath)

                # Save the JSON file
                with open(json_filepath, 'w', encoding='utf-8') as f:
                    json.dump(final_details, f, indent=2, ensure_ascii=False)

                save_processed_link(link)
                print(f"  -> ✔  Successfully scraped and saved to '{json_filepath}'")
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
