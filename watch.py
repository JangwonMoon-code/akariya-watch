import os
import json
import time
import hashlib
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
from datetime import datetime
from zoneinfo import ZoneInfo
import re

URL = "https://akariya-jishichi.co.jp/?mode=cate&cbid=2606203&csid=0"
STATE_FILE = "state.json"

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

MIN_PRICE = 10000
INITIAL_NOTIFY = False  # 첫 실행 때도 조건 맞는 상품 알림 보내려면 True


def now_jst() -> str:
    return datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M:%S")


def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8,ko;q=0.7",
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    # apparent_encoding이 틀릴 때가 있어서 fallback 처리
    response.encoding = response.apparent_encoding or response.encoding or "utf-8"

    return response.text


def get_pid(url: str) -> str:
    """
    상품 고유 ID 추출.
    URL 전체보다 pid 기준이 훨씬 안정적.
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    pid = qs.get("pid", [""])[0]
    return pid.strip()


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def extract_price(text: str) -> int:
    """
    12,345円 / ¥12,345 / 12,345 円 대응
    """
    patterns = [
        r"([0-9][0-9,]*)\s*円",
        r"¥\s*([0-9][0-9,]*)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1).replace(",", ""))

    return 0


def is_sold_out(text: str) -> bool:
    upper = text.upper()
    soldout_words = [
        "SOLD OUT",
        "SOLDOUT",
        "売り切れ",
        "売切れ",
        "在庫なし",
        "品切れ",
    ]
    return any(word.upper() in upper for word in soldout_words)


def find_product_card(link):
    """
    상품 링크 주변에서 가장 그럴듯한 카드 영역을 찾는다.
    부모를 무조건 4번 올리는 방식보다 안전.
    """
    selectors = [
        "li",
        "article",
        "div",
    ]

    for selector in selectors:
        card = link.find_parent(selector)
        if card:
            text = clean_text(card.get_text(" ", strip=True))

            # 너무 큰 영역이면 상품 카드가 아닐 가능성이 높음
            if 0 < len(text) <= 500:
                return card

    return link.parent or link


def extract_product_name(link, card) -> str:
    """
    1순위: a 태그 텍스트
    2순위: 이미지 alt
    3순위: 카드 전체 텍스트에서 가격/SOLD OUT 제거
    """
    link_text = clean_text(link.get_text(" ", strip=True))
    if link_text:
        return link_text

    img = link.find("img")
    if img:
        alt = clean_text(img.get("alt", ""))
        if alt:
            return alt

    card_text = clean_text(card.get_text(" ", strip=True))

    # 가격, SOLD OUT 등 제거해서 임시 상품명 생성
    card_text = re.sub(r"[0-9][0-9,]*\s*円", "", card_text)
    card_text = re.sub(r"¥\s*[0-9][0-9,]*", "", card_text)
    card_text = re.sub(r"SOLD\s*OUT", "", card_text, flags=re.I)
    card_text = clean_text(card_text)

    return card_text[:80] if card_text else "상품명 확인 필요"


def extract_products(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    products_by_pid = {}

    for link in soup.find_all("a", href=True):
        href = link.get("href", "").strip()

        if "pid=" not in href:
            continue

        full_url = urljoin(URL, href)
        pid = get_pid(full_url)

        if not pid:
            continue

        card = find_product_card(link)
        card_text = clean_text(card.get_text(" ", strip=True))

        name = extract_product_name(link, card)
        price = extract_price(card_text)
        sold_out = is_sold_out(card_text)

        item = {
            "pid": pid,
            "name": name,
            "url": full_url,
            "price": price,
            "sold_out": sold_out,
            "available": not sold_out,
        }

        # 같은 pid가 여러 번 잡히면 정보가 더 많은 쪽을 우선
        old = products_by_pid.get(pid)
        if old is None:
            products_by_pid[pid] = item
        else:
            if item["price"] > old["price"] or len(item["name"]) > len(old["name"]):
                products_by_pid[pid] = item

    products = list(products_by_pid.values())

    # pid 기준 정렬: hash 안정화
    products.sort(key=lambda x: x["pid"])

    return products


def make_hash(products: list[dict]) -> str:
    """
    전체 상품 상태 hash.
    sold_out, price 변경도 감지됨.
    """
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
        "checked_at": now_jst(),
        "products": products,
    }

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def detect_changes(old_products: list[dict], new_products: list[dict]) -> dict:
    old_by_pid = {p["pid"]: p for p in old_products}
    new_by_pid = {p["pid"]: p for p in new_products}

    newly_added = []
    restocked = []
    price_changed = []

    for pid, new in new_by_pid.items():
        old = old_by_pid.get(pid)

        if old is None:
            if new["available"]:
                newly_added.append(new)
            continue

        # 예전엔 품절, 지금은 판매 가능
        if old.get("sold_out") is True and new.get("sold_out") is False:
            restocked.append(new)

        # 가격 변경
        if old.get("price") != new.get("price"):
            price_changed.append({
                "old": old,
                "new": new,
            })

    return {
        "newly_added": newly_added,
        "restocked": restocked,
        "price_changed": price_changed,
    }


def send_discord(message: str) -> None:
    if not message:
        print("No Discord message to send.")
        return

    if not DISCORD_WEBHOOK_URL:
        print("ERROR: DISCORD_WEBHOOK_URL is not set.")
        return

    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json={"content": message},
        timeout=20,
    )

    print("Discord status code:", response.status_code)
    print("Discord response:", response.text)

    response.raise_for_status()


def filter_high_price(products: list[dict]) -> list[dict]:
    return [
        p for p in products
        if p.get("available") is True and p.get("price", 0) >= MIN_PRICE
    ]


def build_message(changes: dict) -> str:
    targets = []

    for p in changes["newly_added"]:
        targets.append(("신규 상품", p))

    for p in changes["restocked"]:
        targets.append(("재입고", p))

    # 가격 변경은 보통 알림 우선순위가 낮으므로, 1만엔 이상 판매 가능 상품만 포함
    for item in changes["price_changed"]:
        new = item["new"]
        old = item["old"]

        if new.get("available") and new.get("price", 0) >= MIN_PRICE:
            copied = dict(new)
            copied["old_price"] = old.get("price", 0)
            targets.append(("가격 변경", copied))

    # 1만엔 이상만 알림
    targets = [
        (label, p)
        for label, p in targets
        if p.get("price", 0) >= MIN_PRICE
    ]

    if not targets:
        return ""

    lines = [
        f"🕒 감지 시간: {now_jst()}",
        "",
        f"🔥 {MIN_PRICE:,}엔 이상 상품 감지!",
        "",
    ]

    for label, p in targets:
        lines.append(f"[{label}]")
        lines.append(f"상품명: {p['name']}")
        lines.append(f"💴 ¥{p['price']:,}")

        if label == "가격 변경":
            lines.append(f"이전 가격: ¥{p.get('old_price', 0):,}")

        lines.append(f"🔗 {p['url']}")
        lines.append("👉 바로 확인하세요")
        lines.append("")

    return "\n".join(lines)


def build_initial_message(products: list[dict]) -> str:
    high_price = filter_high_price(products)

    if not high_price:
        return ""

    lines = [
        f"🕒 초기 확인 시간: {now_jst()}",
        "",
        f"🔥 현재 {MIN_PRICE:,}엔 이상 판매 가능 상품 발견!",
        "",
    ]

    for p in high_price:
        lines.append(f"상품명: {p['name']}")
        lines.append(f"💴 ¥{p['price']:,}")
        lines.append(f"🔗 {p['url']}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    print("Checking page...")

    html = fetch_html(URL)
    products = extract_products(html)

    print(f"Products parsed: {len(products)}")

    available_count = sum(1 for p in products if p["available"])
    high_price_count = len(filter_high_price(products))

    print(f"Available products: {available_count}")
    print(f"High price available products: {high_price_count}")

    if not products:
        print("No products found. Page structure may have changed.")
        page_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
    else:
        page_hash = make_hash(products)

    old_state = load_state()

    # 첫 실행
    if old_state is None:
        save_state(products, page_hash)
        print(f"Initial state saved. Products found: {len(products)}")

        if INITIAL_NOTIFY:
            message = build_initial_message(products)
            send_discord(message)

        return

    old_hash = old_state.get("hash")
    old_products = old_state.get("products", [])

    if old_hash == page_hash:
        print(f"No change. Products found: {len(products)}")
        print(f"Checked at: {now_jst()}")

        # 매번 Discord 확인 메시지 보내면 너무 시끄러울 수 있어서 기본 비활성
        # send_discord(f"✅ 확인 완료\n상품 수: {len(products)}개\n판매 가능: {available_count}개")

        return

    changes = detect_changes(old_products, products)

    print(f"Newly added: {len(changes['newly_added'])}")
    print(f"Restocked: {len(changes['restocked'])}")
    print(f"Price changed: {len(changes['price_changed'])}")

    message = build_message(changes)

    if message:
        send_discord(message)
        print("Discord alert sent.")
    else:
        print("Page changed, but no target product matched alert condition.")

    save_state(products, page_hash)

    print("Check completed.")


if __name__ == "__main__":
    main()
