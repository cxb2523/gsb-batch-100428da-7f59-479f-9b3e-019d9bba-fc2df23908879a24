"""Run the RPA Challenge and screenshot the final score page."""
from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        # Navigate to the challenge
        await page.goto("https://rpachallenge.com/")
        await page.wait_for_url("**/upload", timeout=30000)

        # Upload the input file
        input_file = Path("input.xlsx").resolve()
        await page.set_input_files("input[type='file']", str(input_file))
        await page.wait_for_timeout(5000)

        # Start the challenge
        await page.click('button:has-text("Start challenge")')
        await page.wait_for_timeout(5000)

        # Wait for score to appear
        await page.wait_for_selector(
            'div:has-text("Final score")', timeout=120000
        )

        # Screenshot the final page
        await page.screenshot(path="final_score.png", full_page=True)
        print("Screenshot saved to final_score.png")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
