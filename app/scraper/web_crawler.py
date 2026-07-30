import re
import httpx
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

# Standard email regex pattern
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

# Platforms, services, or default junk domains to filter out
IGNORED_DOMAINS = [
    "pinterest", "facebook", "instagram", "tiktok", "youtube",
    "twitter", "shopify", "wix", "squarespace", "wordpress",
    "sentry", "example", "noreply", "no-reply", "privacy",
    "support@shopify", "hello@squarespace", "canspace", "google", "apple"
]

# Domains that shouldn't be crawled at all
SKIP_DOMAINS = [
    "etsy.com", "youtube.com", "canva.site", "linktr.ee",
    "subscribepage.com", "wordpress.com", "blogspot.com",
    "beacons.ai", "tumblr.com", "pinterest.com", "facebook.com", "instagram.com"
]

# High-probability seed paths to check if homepage layout lacks links
SEED_PATHS = [
    "",
    "/contact",
    "/pages/contact",
    "/contact-us",
    "/pages/contact-us",
    "/about",
    "/pages/about",
]

def decode_cloudflare_email(cfemail: str) -> str:
    """Hex-decodes Cloudflare's obfuscated email strings."""
    try:
        k = int(cfemail[:2], 16)
        return "".join(chr(int(cfemail[i:i+2], 16) ^ k) for i in range(2, len(cfemail), 2))
    except Exception:
        return ""

def decode_obfuscated_email(text: str) -> list[str]:
    """Matches patterns like name[at]domain[dot]com or name (at) domain (dot) net."""
    pattern = re.compile(
        r"[a-zA-Z0-9_.+-]+"
        r"\s*[\(\[]\s*at\s*[\)\]]\s*"
        r"[a-zA-Z0-9.-]+"
        r"\s*[\(\[]\s*dot\s*[a-zA-Z0-9.-]+\s*[\)\]]",
        re.IGNORECASE
    )
    results = []
    for match in pattern.findall(text):
        cleaned = match.lower()
        cleaned = re.sub(r"\s*[\(\[]\s*at\s*[\)\]]\s*", "@", cleaned)
        cleaned = re.sub(r"\s*[\(\[]\s*dot\s*([a-zA-Z0-9.-]+)\s*[\)\]]", r".\1", cleaned)
        cleaned = cleaned.strip()
        if cleaned:
            results.append(cleaned)
    return results

def is_valid_email(email: str) -> bool:
    """Checks if an email is well-formed and doesn't belong to ignored domains."""
    if not email or "@" not in email:
        return False
    email_lower = email.lower()
    return not any(domain in email_lower for domain in IGNORED_DOMAINS)

def get_email_score(email: str, base_url: str) -> int:
    """
    Scores an email candidate. Higher scores mean we prefer this email.
    Emails using the site's own domain score highest.
    """
    try:
        domain = urlparse(base_url).netloc.replace("www.", "")
        if domain in email.lower():
            return 10
    except Exception:
        pass
    if "info@" in email.lower() or "hello@" in email.lower() or "contact@" in email.lower():
        return 5
    return 1

async def extract_emails_from_page(html: str, base_url: str) -> set[str]:
    """Scans single page HTML for mailto, Cloudflare, obfuscated, and plain text emails."""
    soup = BeautifulSoup(html, "html.parser")
    found = set()

    # 1. Parse Mailto links
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("mailto:"):
            email = href.replace("mailto:", "").split("?")[0].strip()
            if is_valid_email(email):
                found.add(email)

    # 2. Parse Cloudflare Obfuscation
    for cf_tag in soup.find_all(class_="__cf_email__"):
        data_cf = cf_tag.get("data-cfemail")
        if data_cf:
            email = decode_cloudflare_email(data_cf)
            if is_valid_email(email):
                found.add(email)

    # 3. Parse Obfuscated Text and Regular Text Scans
    page_text = soup.get_text()
    
    for email in decode_obfuscated_email(page_text):
        if is_valid_email(email):
            found.add(email)

    for email in EMAIL_REGEX.findall(page_text):
        if is_valid_email(email):
            found.add(email)

    return found

async def crawl_website(url: str) -> str | None:
    """
    Crawls a target website up to a few high-value internal pages 
    to extract and rank contact emails.
    """
    if not url:
        return None

    base = url.rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base

    if any(domain in base for domain in SKIP_DOMAINS):
        return None

    # Track distinct candidate emails discovered across the crawl
    all_candidates = set()
    
    # Track internal URLs we have queued up to visit
    pages_to_visit = set()
    for path in SEED_PATHS:
        pages_to_visit.add(urljoin(base, path))

    try:
        async with httpx.AsyncClient(
            timeout=8,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        ) as client:
            
            # 1. Fetch the Homepage first to discover contextual subpages
            try:
                r = await client.get(base)
                if r.status_code == 200:
                    all_candidates.update(await extract_emails_from_page(r.text, base))
                    
                    # Dynamically pick up contact/about pages listed in the HTML
                    soup = BeautifulSoup(r.text, "html.parser")
                    for a in soup.find_all("a", href=True):
                        href = a["href"].strip()
                        resolved_url = urljoin(base, href)
                        
                        # Only keep links inside the same domain
                        if urlparse(resolved_url).netloc == urlparse(base).netloc:
                            link_text = a.get_text().lower()
                            url_path = urlparse(resolved_url).path.lower()
                            
                            if any(k in url_path or k in link_text for k in ["contact", "about", "privacy"]):
                                pages_to_visit.add(resolved_url)
            except Exception:
                # If homepage fails completely, fallback to trying the seed paths anyway
                pass

            # 2. Process discovered deep links (Cap at max 4 additional pages to keep it fast)
            visited_count = 0
            for target_page in list(pages_to_visit):
                if visited_count >= 4:
                    break
                
                # Skip the home page duplicate run
                if target_page.strip("/") == base:
                    continue

                try:
                    r = await client.get(target_page)
                    if r.status_code != 200:
                        continue
                    
                    page_emails = await extract_emails_from_page(r.text, base)
                    if page_emails:
                        all_candidates.update(page_emails)
                    
                    visited_count += 1
                except Exception:
                    continue

    except Exception as e:
        print(f"  ⚠️ Crawler error for {url}: {e}", flush=True)

    # 3. Select the highest quality candidate found
    if all_candidates:
        sorted_emails = sorted(all_candidates, key=lambda e: get_email_score(e, base), reverse=True)
        best_match = sorted_emails[0]
        print(f"  📧 Selected best candidate for {url}: {best_match}", flush=True)
        return best_match

    return None