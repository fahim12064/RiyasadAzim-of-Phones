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

    except Exception as e:
        print(f"An Error occurred While navigating to {target_url_1}: {e}")
async def main():
    async with async_playwright() as playwright:
        await run(playwright)

if __name__ == "__main__":
    asyncio.run(main())
