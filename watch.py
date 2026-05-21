import os
import json
import time
import hashlib
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime
from zoneinfo import ZoneInfo
import re

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

        if "pid=" not in href:
            continue

        # 상품 카드 전체 텍스트 확보
        card = link
        for _ in range(4):
            if card.parent:
                card = card.parent

        card_text = card.get_text(" ", strip=True)
        text = link.get_text(" ", strip=True)

        if not text:
            continue

        # SOLD OUT 제외
        if "SOLD OUT" in card_text.upper():
            continue

        full_url = urljoin(URL, href)

        price_match = re.search(r'([0-9,]+)\s*円', card_text)
        price = 0

        if price_match:
            price = int(price_match.group(1).replace(",", ""))

        products.append({
            "name": text,
            "url": full_url,
            "price": price
        })

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
        print("ERROR: DISCORD_WEBHOOK_URL is not set.")
        return

    print("Sending Discord message...")

    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json={"content": message},
        timeout=20
    )

    print("Discord status code:", response.status_code)
    print("Discord response:", response.text)

    response.raise_for_status()


def build_message(new_products: list[dict]) -> str:

    now = datetime.now(ZoneInfo("Asia/Tokyo"))

    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    lines = [

        f"🕒 감지 시간: {now_str}",
        ""
    ]

    high_price = []

    for p in new_products:

        if p["price"] >= 10000:

            high_price.append(p)

    if high_price:

        lines.append("🔥 1만엔 이상 재고 발견!")

        lines.append("")

        for p in high_price:

            lines.append(f"상품명: {p['name']}")
            lines.append(f"가격: ¥{p['price']:,}")
            lines.append(f"URL: {p['url']}")
            lines.append("👉 바로 구매하세요")
            lines.append("")

    else:
        lines.append("아직 제품 입고전입니다.")
        lines.append("👉 조금만 더 기다려주세요!")
        lines.append(f"상품 수: {len(new_products)}")

    return "\n".join(lines)


def main() -> None:
    print("Checking page...")
    now = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M:%S")
    send_discord(f"🔥 아카리 램프 현재 재고 입고 되었는지 확인!\n🕒 {now}")    
    
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

    now = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M:%S")

    old_hash = old_state.get("hash")
    old_products = old_state.get("products", [])
    
    if old_hash == page_hash:
        send_discord(
            f"✅ 확인 완료\n"
            f"상품 수: {len(products)}개"
        )
        print(f"No change. Products found: {len(products)}")
        return
    
    new_products = find_new_products(old_products, products)
    
    message = build_message(new_products)
    
    if message:
        send_discord(message)
        print("Discord message sent.")
    else:
        send_discord(
            f"✅ 확인 완료\n"
            f"1만엔 이상 신규 상품은 없습니다.\n"
            f"상품 수: {len(products)}개"
        )
        print("No products over 10000 yen.")
    
    save_state(products, page_hash)
    
    print("Check completed.")
    print(f"New products: {len(new_products)}")


if __name__ == "__main__":
    main()
