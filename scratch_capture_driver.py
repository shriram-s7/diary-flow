import asyncio
from playwright.async_api import async_playwright

async def capture():
    async with async_playwright() as p:
        # Launch browser in headless mode
        browser = await p.chromium.launch(headless=True)
        
        # Target viewport 390x844 (iPhone 14 / modern Android resolution)
        context = await browser.new_context(
            viewport={'width': 390, 'height': 844},
            device_scale_factor=2,
            user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36"
        )
        page = await context.new_page()
        
        print("Navigating to driver app...")
        await page.goto("http://localhost:5600/driver.html?truckId=TRK-03", wait_until="domcontentloaded")
        await page.wait_for_timeout(3500)
        
        # 1. State: Driving
        print("Capturing 1. Driving state...")
        await page.screenshot(path="C:/Users/shrir/.gemini/antigravity-ide/brain/75e1220b-d59f-4883-9513-ea0cc509bdac/driver_driving_390x844.png")
        await page.screenshot(path="d:/AIML-BASED-REALTIME-ROUTE-OPTIMIZATION-SYSTEM-FOR-TRANSPORTATION-AND-LOGISTICS/screenshots/driver_driving_390x844.png")

        # 2. State: Rerouting (Trigger rerouting notification banner)
        print("Capturing 2. Rerouting state...")
        await page.evaluate("""() => {
            const banner = document.createElement('div');
            banner.id = 'test-reroute-banner';
            banner.className = 'df-driver-notification rerouting';
            banner.innerHTML = `
                <div style="display:flex;align-items:center;gap:8px;">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
                        <path d="M3 3v5h5"/>
                        <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/>
                        <path d="M16 21h5v-5"/>
                    </svg>
                    <span style="font-size:12px;font-weight:700;">Road closed ahead — rerouting (+6 min)</span>
                </div>
            `;
            const stack = document.querySelector('.df-driver-alerts-stack');
            if (stack) stack.prepend(banner);
        }""")
        await page.wait_for_timeout(500)
        await page.screenshot(path="C:/Users/shrir/.gemini/antigravity-ide/brain/75e1220b-d59f-4883-9513-ea0cc509bdac/driver_rerouting_390x844.png")
        await page.screenshot(path="d:/AIML-BASED-REALTIME-ROUTE-OPTIMIZATION-SYSTEM-FOR-TRANSPORTATION-AND-LOGISTICS/screenshots/driver_rerouting_390x844.png")

        # Clean reroute banner
        await page.evaluate("() => { const b = document.getElementById('test-reroute-banner'); if (b) b.remove(); }")

        # 3. State: Blocked (No route available, Call Manager button)
        print("Capturing 3. Blocked state...")
        await page.evaluate("""() => {
            const banner = document.createElement('div');
            banner.id = 'test-blocked-banner';
            banner.className = 'df-driver-notification blocked';
            banner.innerHTML = `
                <div style="display:flex;align-items:center;gap:8px;">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10"/>
                        <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>
                    </svg>
                    <span style="font-size:12px;font-weight:700;">No route available — stop safely. Manager notified.</span>
                </div>
                <button type="button" style="background:#ffffff;color:#b91c1c;border:none;padding:5px 12px;border-radius:12px;font-size:11px;font-weight:800;cursor:pointer;box-shadow:0 2px 4px rgba(0,0,0,0.2);">
                    Call Manager
                </button>
            `;
            const stack = document.querySelector('.df-driver-alerts-stack');
            if (stack) stack.prepend(banner);
        }""")
        await page.wait_for_timeout(500)
        await page.screenshot(path="C:/Users/shrir/.gemini/antigravity-ide/brain/75e1220b-d59f-4883-9513-ea0cc509bdac/driver_blocked_390x844.png")
        await page.screenshot(path="d:/AIML-BASED-REALTIME-ROUTE-OPTIMIZATION-SYSTEM-FOR-TRANSPORTATION-AND-LOGISTICS/screenshots/driver_blocked_390x844.png")

        # Clean blocked banner
        await page.evaluate("() => { const b = document.getElementById('test-blocked-banner'); if (b) b.remove(); }")

        # 4. State: Unloading (Circular progress ring HUD)
        print("Capturing 4. Unloading state...")
        await page.evaluate("""() => {
            const overlay = document.createElement('div');
            overlay.id = 'test-unloading-overlay';
            overlay.className = 'df-unloading-overlay';
            overlay.innerHTML = `
                <div class="df-progress-ring-container">
                    <svg width="170" height="170" viewBox="0 0 120 120">
                        <circle cx="60" cy="60" r="50" fill="none" stroke="#334155" stroke-width="8" />
                        <circle
                            cx="60"
                            cy="60"
                            r="50"
                            fill="none"
                            stroke="#22c55e"
                            stroke-width="8"
                            stroke-dasharray="314"
                            stroke-dashoffset="110"
                            stroke-linecap="round"
                            transform="rotate(-90 60 60)"
                        />
                    </svg>
                    <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;">
                        <svg width="42" height="42" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/>
                        </svg>
                    </div>
                </div>
                <div class="df-unloading-title" style="margin-top:14px;font-size:20px;font-weight:800;color:#f8fafc;">
                    Unloading 1,500 L
                </div>
                <div class="df-unloading-desc" style="font-size:13px;color:#94a3b8;margin-top:4px;">
                    6 min left at Whitefield Hub
                </div>
            `;
            const phoneFrame = document.querySelector('.df-phone-frame') || document.body;
            phoneFrame.appendChild(overlay);
        }""")
        await page.wait_for_timeout(500)
        await page.screenshot(path="C:/Users/shrir/.gemini/antigravity-ide/brain/75e1220b-d59f-4883-9513-ea0cc509bdac/driver_unloading_390x844.png")
        await page.screenshot(path="d:/AIML-BASED-REALTIME-ROUTE-OPTIMIZATION-SYSTEM-FOR-TRANSPORTATION-AND-LOGISTICS/screenshots/driver_unloading_390x844.png")

        await browser.close()
        print("All 4 states successfully captured at 390x844!")

if __name__ == "__main__":
    asyncio.run(capture())
