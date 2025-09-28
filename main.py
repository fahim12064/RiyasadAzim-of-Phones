import asyncio
from playwright.async_api import async_playwright, Playwright
target_url_1 = "https://www.mobiledokan.co/products/"

async def run(playwright: Playwright):
    user_data_dir = "./playwright_user_data"
    user_agent_string = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir,
        headless=True,
        user_agent=user_agent_string,
        args=['--start-maximized']
    )
    page = await context.new_page()
    print(f'Navigating to {target_url_1}...')
    try:
        await page.goto(target_url_1, wait_until="domcontentloaded", timeout=120000)
        print(f"SUCCESSFULLY NAVIGATED TO {target_url_1} ")
        #     scraping links
        print("Gathering Latest devices Link")
        product_link_selector = "ul.aps-products li .aps-product-thumb a"
        await page.wait_for_selector(product_link_selector, timeout=30000)
        link_elements = await page.query_selector_all(product_link_selector)
        if not link_elements:
            print("No product links found. The page structure might have changed.")
        else:
            product_links = []
            for link_element in link_elements:
                # Get the 'href' attribute from each link element
                href = await link_element.get_attribute('href')
                if href:
                    product_links.append(href)

            print(f"Found {len(product_links)} product links:")
            # Print all the collected links
            for link in product_links:
                print(link)
    except Exception as e:
        print(f"An Error occurred While navigating to {target_url_1}: {e}")
    finally:
        await context.close()
        print("Browser context closed.")
async def main():
    async with async_playwright() as playwright:
        await run(playwright)

if __name__ == "__main__":
    asyncio.run(main())
