"""Against temporary/explicit prototype servers: no production data mutations."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:15600')
    parser.add_argument('--channel',default='msedge')
    args=parser.parse_args()
    output=Path(__file__).resolve().parents[1]/'.cache'/'p03-browser'
    output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel=args.channel)
        for width,height in [(1440,900),(390,844)]:
            page=browser.new_page(viewport={'width':width,'height':height})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(args.url+'/platform')
            expect(page.get_by_role('button',name='Admin',exact=True)).to_be_visible()
            page.screenshot(path=str(output/f'login-{width}.png'),full_page=True)
            page.get_by_role('button',name='Admin',exact=True).click()
            expect(page.get_by_role('heading',name='Your admin workspace')).to_be_visible()
            expect(page.get_by_text('Toned milk 500 ml',exact=True)).to_be_visible()
            page.screenshot(path=str(output/f'admin-{width}.png'),full_page=True)
            page.get_by_role('button',name='Booth',exact=True).click()
            expect(page.get_by_role('heading',name='Your booth workspace')).to_be_visible()
            expect(page.get_by_text('Demo booth',exact=True)).to_be_visible()
            expect(page.get_by_text('Chennai demo hub',exact=True)).to_have_count(0)
            expect(page.get_by_text('Toned milk 500 ml',exact=True)).to_have_count(0)
            page.screenshot(path=str(output/f'booth-{width}.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert page.locator('.v2-platform').evaluate('(el) => el.scrollHeight >= el.clientHeight')
            page.get_by_role('button',name='Sign out',exact=True).click()
            expect(page.get_by_role('button',name='Sign in',exact=True)).to_be_visible()
            expect(page.get_by_text('Demo booth',exact=True)).to_have_count(0)
            page.reload()
            expect(page.get_by_role('button',name='Sign in',exact=True)).to_be_visible()
            assert page.evaluate('Object.keys(localStorage).every(k => !/token|session/i.test(k))')
            assert not errors,errors
            print(f'PASS {width}x{height}: login, persisted catalogue, admin View as booth, scope isolation, logout, refresh, no console errors')
            page.close()
        browser.close()


if __name__=='__main__':main()
