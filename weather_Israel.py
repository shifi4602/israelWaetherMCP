from mcp.server.fastmcp import FastMCP
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright
from urllib.parse import quote_plus


mcp = FastMCP("weather-Israel")

FORECAST_URL = "https://www.weather2day.co.il/forecast"

# Persistent browser state shared across tool calls
_pw = None
_browser = None
_page = None


async def _open_google_weather_fallback(page, city: str) -> str:
    """Fallback page when the Israeli site is unavailable on the current network."""
    fallback_url = f"https://www.google.com/search?q={quote_plus(f'weather {city}') }"
    await page.goto(fallback_url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(1_500)
    title = await page.title()
    return (
        f"Opened fallback weather page for city '{city}'. "
        f"Current page title: {title}. Current URL: {page.url}"
    )


async def _ensure_browser():
    """Launch a Chromium browser if one is not already running and return the page."""
    global _pw, _browser, _page
    if _page is None:
        _pw = await async_playwright().start()
        try:
            _browser = await _pw.chromium.launch(headless=False, timeout=15_000)
        except Exception:
            _browser = await _pw.chromium.launch(headless=True, timeout=15_000)
        _page = await _browser.new_page()
    return _page


@mcp.tool()
async def open_weather_for_city(city: str) -> str:
    """Open weather2day in a visible browser and search for an Israeli city.

    This tool is optimized for user-facing behavior: when a city is provided,
    it opens the browser window, enters the city in the search box, and tries
    to select the first suggestion so the forecast page is displayed.

    Args:
        city: Israeli city name, in Hebrew or English.
    """
    page = await _ensure_browser()
    try:
        await page.goto(FORECAST_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(1_500)

        selectors = [
            "input[type='search']",
            "input[name='search']",
            "input[placeholder*='עיר']",
            "input[placeholder*='ישוב']",
            "input[placeholder*='city' i]",
            "#search",
            ".search input",
        ]

        selected = None
        for selector in selectors:
            locator = page.locator(selector).first
            if await locator.count() > 0:
                selected = selector
                await locator.fill(city)
                break

        if not selected:
            return await _open_google_weather_fallback(page, city)

        await page.wait_for_timeout(1_500)

        suggestion_selectors = [
            ".ui-menu-item",
            ".autocomplete-suggestion",
            ".tt-suggestion",
            "li[role='option']",
            ".suggestion",
        ]

        clicked_suggestion = False
        for suggestion_selector in suggestion_selectors:
            suggestion = page.locator(suggestion_selector).first
            if await suggestion.count() > 0:
                await suggestion.click(timeout=4_000)
                clicked_suggestion = True
                break

        if not clicked_suggestion:
            await page.keyboard.press("Enter")

        await page.wait_for_timeout(2_500)
        title = await page.title()
        return (
            f"Opened browser for city '{city}'. Current page title: {title}. "
            f"Current URL: {page.url}"
        )
    except PlaywrightTimeout:
        try:
            return await _open_google_weather_fallback(page, city)
        except Exception as fallback_error:
            return (
                f"Timed out while trying to open weather for city '{city}', and fallback failed: "
                f"{fallback_error}"
            )
    except Exception as e:
        try:
            return await _open_google_weather_fallback(page, city)
        except Exception as fallback_error:
            return (
                f"Error opening weather for city '{city}': {e}. "
                f"Fallback also failed: {fallback_error}"
            )


@mcp.tool()
async def navigate_to_url(url: str) -> str:
    """Navigate the browser to a URL and return the full page HTML.

    Use this first to open the Israeli weather website and inspect the DOM
    structure so you can identify the correct CSS selectors for searching.

    Args:
        url: The full URL to navigate to (e.g. the FORECAST_URL constant).
    """
    page = await _ensure_browser()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(1_500)
        return await page.content()
    except PlaywrightTimeout:
        return "Error: Page load timed out."
    except Exception as e:
        return f"Error navigating to '{url}': {e}"


@mcp.tool()
async def fill_input_field(selector: str, text: str) -> str:
    """Fill a text input on the current page with the given value and return
    the updated page HTML (including any auto-complete suggestions).

    Use this to type a city name into the weather-site's search box after
    calling navigate_to_url so you can see the suggestions that appear.

    Args:
        selector: CSS selector that identifies the input element.
        text: The text to type into the field (e.g. an Israeli city name).
    """
    page = await _ensure_browser()
    try:
        await page.fill(selector, text)
        await page.wait_for_timeout(2_000)
        return await page.content()
    except PlaywrightTimeout:
        return f"Error: Timed out trying to fill '{selector}'."
    except Exception as e:
        return f"Error filling '{selector}': {e}"


@mcp.tool()
async def click_element(selector: str) -> str:
    """Click an element on the current page and return the updated page HTML.

    Use this to select a city from the auto-complete dropdown or to submit
    a search form, then read the returned HTML to extract the weather data.

    Args:
        selector: CSS selector of the element to click (e.g. a list item in
                  the suggestions dropdown or a submit button).
    """
    page = await _ensure_browser()
    try:
        await page.click(selector, timeout=10_000)
        await page.wait_for_timeout(2_000)
        return await page.content()
    except PlaywrightTimeout:
        return f"Error: Could not find or click '{selector}'."
    except Exception as e:
        return f"Error clicking '{selector}': {e}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

