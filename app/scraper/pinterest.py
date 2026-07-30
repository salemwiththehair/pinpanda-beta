import asyncio
import re
import json
import functools
from playwright.async_api import async_playwright

print = functools.partial(print, flush=True)
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

SKIP_HANDLES = {
    "search", "login", "signup", "ideas", "business", "about",
    "help", "settings", "notifications", "explore", "pin", "pins",
    "r3v0kit", "inspiration", "Temu_Armenia", "users", ""
}

SKIP_CRAWL_DOMAINS = [
    "etsy.com", "youtube.com", "canva.site", "linktr.ee",
    "subscribepage.com", "wordpress.com", "blogspot.com",
    "beacons.ai", "tumblr.com"
]

async def load_cookies(context, cookie_path: str = "pinterest_cookies.json") -> bool:
    try:
        with open(cookie_path, "r") as f:
            cookies = json.load(f)
        formatted = []
        for c in cookies:
            cookie = {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain", ".pinterest.com"),
                "path": c.get("path", "/"),
                "secure": c.get("secure", False),
                "httpOnly": c.get("httpOnly", False),
            }
            if "expirationDate" in c and c["expirationDate"]:
                cookie["expires"] = int(c["expirationDate"])
            same_site = c.get("sameSite", "")
            if same_site == "no_restriction":
                cookie["sameSite"] = "None"
            elif same_site == "lax":
                cookie["sameSite"] = "Lax"
            elif same_site == "strict":
                cookie["sameSite"] = "Strict"
            formatted.append(cookie)
        await context.add_cookies(formatted)
        return True
    except Exception as e:
        print(f"Cookie load failed: {e}")
        return False

async def search_profiles(page, keywords: list, limit: int, seen_handles: set = None) -> list:
    if seen_handles is None:
        seen_handles = set()
    handles = set()

    for keyword in keywords:
        if len(handles) >= limit:
            break
        try:
            url = f"https://www.pinterest.com/search/users/?q={keyword.replace(' ', '%20')}&rs=content_type_filter&filter_location=1"
            print(f"Searching: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(6000)

            scrolls = 0
            while len(handles) < limit:
                await page.evaluate("window.scrollBy(0, 2000)")
                await page.wait_for_timeout(2000)
                all_links = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
                for link in all_links:
                    if "pinterest.com/" in link:
                        path = link.split("pinterest.com/")[1].split("/")[0].split("?")[0].split("#")[0]
                        if path and path not in SKIP_HANDLES and path not in seen_handles:
                            handles.add(path)
                    if len(handles) >= limit:
                        break
                scrolls += 1
                print(f"Scroll {scrolls}: {len(handles)} handles found")
                if scrolls > 20:
                    break
        except Exception as e:
            print(f"Search error for '{keyword}': {e}")

    result = list(handles)[:limit]
    print(f"Total profiles to scrape: {len(result)}")
    return result

async def scrape_profile(page, handle: str) -> dict:
    data = {
        "shop_name": handle,
        "pinterest_url": f"https://www.pinterest.com/{handle}/",
        "email": None,
        "website": None,
        "platform": None,
        "instagram": None,
        "facebook": None,
        "tiktok": None,
        "youtube": None,
    }

    try:
        await page.goto(data["pinterest_url"], wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)

        try:
            name = await page.inner_text('[data-test-id="profile-name"]')
            if name:
                data["shop_name"] = name.strip()
        except:
            pass

        try:
            bio = await page.inner_text('[data-test-id="main-user-description-text"]')
            found = EMAIL_REGEX.findall(bio)
            if found:
                data["email"] = found[0]
        except:
            pass

        try:
            website_link = await page.evaluate("""
                () => {
                    const el = document.querySelector('[data-test-id="website-icon-and-url"]');
                    if (!el) return null;
                    const a = el.querySelector('a') || el.closest('a');
                    return a ? a.href : null;
                }
            """)
            if website_link and "etsy.com" not in website_link.lower():
                data["website"] = website_link
                w = website_link.lower()
                if "shopify" in w or "myshopify" in w:
                    data["platform"] = "Shopify"
                elif "zazzle.com" in w:
                    data["platform"] = "Zazzle"
                else:
                    data["platform"] = "Independent"
        except:
            pass

        if not data["email"]:
            try:
                btn = page.locator('button', has_text="Contact")
                if await btn.count() > 0:
                    await btn.first.click()
                    await page.wait_for_timeout(1500)
                    page_text = await page.evaluate("() => document.body.innerText")
                    found = EMAIL_REGEX.findall(page_text)
                    for email in found:
                        if not any(x in email.lower() for x in ["pinterest", "example", "noreply", "support@"]):
                            data["email"] = email
                            break
                    await page.keyboard.press("Escape")
            except:
                pass

        try:
            all_links = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            for link in all_links:
                if "instagram.com" in link and not data["instagram"]:
                    data["instagram"] = link
                elif "facebook.com" in link and not data["facebook"]:
                    data["facebook"] = link
                elif "tiktok.com" in link and not data["tiktok"]:
                    data["tiktok"] = link
                elif "youtube.com" in link and not data["youtube"]:
                    data["youtube"] = link
        except:
            pass

    except Exception as e:
        print(f"Profile scrape error for {handle}: {e}")

    return data

def run_scrape_job_sync(job_id: int, keywords: list, limit: int, fresh: bool = True, emails_only: bool = False, headless: bool = True):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_scrape_job_async(job_id, keywords, limit, fresh, emails_only, headless))
    finally:
        loop.close()

async def run_scrape_job_async(job_id: int, keywords: list, limit: int, fresh: bool = True, emails_only: bool = False, headless: bool = True):
    from app.models.database import SessionLocal, Lead, SearchJob
    from app.scraper.web_crawler import crawl_website
    from app.api.routes import email_blocklist
    db = SessionLocal()

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            loaded = await load_cookies(context)
            if not loaded:
                print("Could not load cookies")
                await browser.close()
                return

            page = await context.new_page()
            await page.goto("https://www.pinterest.com", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)

            if "login" in page.url:
                print("Cookies expired")
                await browser.close()
                return

            print("Logged in successfully!")

            job = db.query(SearchJob).filter(SearchJob.id == job_id).first()
            if job:
                job.status = "running"
                db.commit()

            seen_handles = set()
            if not fresh:
                existing = db.query(Lead).filter(Lead.job_id == job_id).all()
                seen_handles = {l.pinterest_url.split("pinterest.com/")[1].rstrip("/") for l in existing if l.pinterest_url}

            to_crawl = []
            found_with_emails = 0

            if emails_only:
                keyword_index = 0
                while found_with_emails < limit:
                    db.refresh(job)
                    if job.status == "stopped":
                        break

                    kw = keywords[keyword_index % len(keywords)]
                    keyword_index += 1
                    handles = await search_profiles(page, [kw], 50, seen_handles)
                    seen_handles.update(handles)

                    for handle in handles:
                        if found_with_emails >= limit:
                            break
                        db.refresh(job)
                        if job.status == "stopped":
                            break

                        await asyncio.sleep(2)
                        print(f"[{found_with_emails}/{limit} emails] Scraping: {handle}")
                        profile_data = await scrape_profile(page, handle)

                        # Check blocklist
                        if profile_data["email"] and profile_data["email"].lower() in email_blocklist:
                            print(f"Skipping {profile_data['email']} - in blocklist")
                            profile_data["email"] = None

                        if profile_data["email"]:
                            lead = Lead(job_id=job_id, **profile_data)
                            db.add(lead)
                            db.commit()
                            found_with_emails += 1
                            print(f"Email found: {profile_data['email']} ({found_with_emails}/{limit})")
                        elif profile_data["website"] and not any(x in profile_data["website"] for x in SKIP_CRAWL_DOMAINS):
                            to_crawl.append(profile_data)
            else:
                handles = await search_profiles(page, keywords, limit, seen_handles)
                for i, handle in enumerate(handles):
                    db.refresh(job)
                    if job.status == "stopped":
                        break

                    await asyncio.sleep(2)
                    print(f"[{i+1}/{len(handles)}] Scraping: {handle}")
                    profile_data = await scrape_profile(page, handle)

                    # Check blocklist
                    if profile_data["email"] and profile_data["email"].lower() in email_blocklist:
                        print(f"Skipping {profile_data['email']} - in blocklist")
                        profile_data["email"] = None

                    lead = Lead(job_id=job_id, **profile_data)
                    db.add(lead)
                    db.commit()
                    db.refresh(lead)
                    print(f"Saved: {profile_data['shop_name']} | {profile_data['email']}")

                    if not profile_data["email"] and profile_data["website"]:
                        if not any(x in profile_data["website"] for x in SKIP_CRAWL_DOMAINS):
                            to_crawl.append({"id": lead.id, **profile_data})

            await browser.close()

        if to_crawl:
            print(f"Crawling {len(to_crawl)} websites in parallel...")

            async def crawl_and_update(item):
                if emails_only:
                    nonlocal found_with_emails
                    if found_with_emails >= limit:
                        return
                    email = await crawl_website(item["website"])
                    if email:
                        if email.lower() in email_blocklist:
                            print(f"Skipping crawled email {email} - in blocklist")
                            return
                        item["email"] = email
                        lead = Lead(job_id=job_id, **{k: v for k, v in item.items() if k != "id"})
                        db.add(lead)
                        db.commit()
                        found_with_emails += 1
                        print(f"Crawl found: {email}")
                else:
                    lead = db.query(Lead).filter(Lead.id == item["id"]).first()
                    if not lead:
                        return
                    email = await crawl_website(item["website"])
                    if email:
                        if email.lower() in email_blocklist:
                            print(f"Skipping crawled email {email} - in blocklist")
                            return
                        lead.email = email
                        db.commit()
                        print(f"Crawl found: {email} for {lead.shop_name}")

            await asyncio.gather(*[crawl_and_update(item) for item in to_crawl])
            print("Web crawl complete!")

        job = db.query(SearchJob).filter(SearchJob.id == job_id).first()
        if job and job.status != "stopped":
            job.status = "done"
            db.commit()

    except Exception as e:
        print(f"Job error: {e}")
        try:
            job = db.query(SearchJob).filter(SearchJob.id == job_id).first()
            if job:
                job.status = "failed"
                db.commit()
        except:
            pass
    finally:
        db.close()

    print(f"Job {job_id} done!")