import os
import json
import time
import hashlib
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

URL = "https://akariya-jishichi.co.jp/?mode=cate&cbid=2606203&csid=0"
STATE_FILE = "state.json"

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return response.text


def extract_products(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    products = []

    for link in soup.find_all("a", href=True):
        href = link.get("href", "").strip()
        text = link.get_text(" ", strip=True)

        if not text:
            continue

        # 상품 상세 링크로 보이는 것만 추출
        if "pid=" not in href:
            continue

        full_url = urljoin(URL, href)

        products.append({
            "name": text,
            "url": full_url
        })

    # 중복 제거
    unique = {}
    for item in products:
        unique[item["url"]] = item

    return list(unique.values())


def make_hash(products: list[dict]) -> str:
    normalized = json.dumps(products, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_state() -> dict | None:
    if not os.path.exists(STATE_FILE):
        return None

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(products: list[dict], page_hash: str) -> None:
    state = {
        "hash": page_hash,
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "products": products
    }

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def find_new_products(old_products: list[dict], new_products: list[dict]) -> list[dict]:
    old_urls = {item["url"] for item in old_products}
    return [item for item in new_products if item["url"] not in old_urls]


def send_discord(message: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL is not set.")
        return

    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json={"content": message},
        timeout=20
    )
    response.raise_for_status()


def build_message(new_products: list[dict]) -> str:
    if new_products:
        lines = ["새 상품이 감지되었습니다.\n"]

        for item in new_products[:10]:
            lines.append(f"- {item['name']}\n{item['url']}")

        if len(new_products) > 10:
            lines.append(f"\n외 {len(new_products) - 10}개 상품 추가 감지")

        lines.append(f"\n카테고리 페이지:\n{URL}")
        return "\n".join(lines)

    return f"AKARI 페이지 변경이 감지되었습니다.\n{URL}"


def main() -> None:
    print("Checking page...")

    html = fetch_html(URL)
    products = extract_products(html)

    if not products:
        print("No products found. Page structure may have changed.")
        page_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
    else:
        page_hash = make_hash(products)

    old_state = load_state()

    if old_state is None:
        save_state(products, page_hash)
        print(f"Initial state saved. Products found: {len(products)}")
        return

    old_hash = old_state.get("hash")
    old_products = old_state.get("products", [])

    if old_hash == page_hash:
        print(f"No change. Products found: {len(products)}")
        return

    new_products = find_new_products(old_products, products)

    message = build_message(new_products)
    send_discord(message)

    save_state(products, page_hash)

    print("Change detected. Notification sent.")
    print(f"New products: {len(new_products)}")


if __name__ == "__main__":
    main()