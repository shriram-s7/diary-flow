import asyncio
import os
import urllib.request
import json
from playwright.async_api import async_playwright

ARTIFACT_DIR = r"C:\Users\shrir\.gemini\antigravity-ide\brain\75e1220b-d59f-4883-9513-ea0cc509bdac"
WORKSPACE_DIR = r"D:\AIML-BASED-REALTIME-ROUTE-OPTIMIZATION-SYSTEM-FOR-TRANSPORTATION-AND-LOGISTICS\screenshots"

os.makedirs(WORKSPACE_DIR, exist_ok=True)

async def capture():
    # First make sure backend has default scenario loaded
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8600/scenarios/chennai-default/load",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            print("Loaded scenario:", resp.status)
        
        # Trigger plan
        plan_req = urllib.request.Request(
            "http://127.0.0.1:8600/plan",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(plan_req) as resp:
            print("Generated plan:", resp.status)
    except Exception as e:
        print("API setup error (non-fatal):", e)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        resolutions = [
            (1440, 900, "manager_1440x900.png"),
            (1280, 720, "manager_1280x720.png"),
            (1024, 768, "manager_1024x768.png"),
        ]

        for width, height, filename in resolutions:
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=1
            )
            page = await context.new_page()

            # Listen to console for any errors
            page.on("console", lambda msg: print(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)

            await page.goto("http://localhost:5600", wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)

            # If at 1024x768, test drawer opening if needed or capture standard view
            dest_artifact = os.path.join(ARTIFACT_DIR, filename)
            dest_workspace = os.path.join(WORKSPACE_DIR, filename)

            await page.screenshot(path=dest_artifact, full_page=False)
            await page.screenshot(path=dest_workspace, full_page=False)
            print(f"Captured {filename} at {width}x{height}")

            # For 1024x768, also take a screenshot with drawer open to verify drawer layout
            if width == 1024:
                # Click left drawer toggle button in topbar
                drawer_btn = page.locator(".df-topbar-left .df-drawer-btn")
                if await drawer_btn.is_visible():
                    await drawer_btn.click()
                    await page.wait_for_timeout(600)
                    drawer_filename = "manager_1024x768_drawer.png"
                    await page.screenshot(path=os.path.join(ARTIFACT_DIR, drawer_filename))
                    await page.screenshot(path=os.path.join(WORKSPACE_DIR, drawer_filename))
                    print(f"Captured {drawer_filename} with drawer open")

            await context.close()

        await browser.close()

if __name__ == "__main__":
    asyncio.run(capture())
