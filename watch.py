import os
import requests

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def main():
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL is not set")

    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json={
            "content": "✅ GitHub Actions에서 Discord 테스트 메시지 전송 성공"
        },
        timeout=20
    )

    print("Status code:", response.status_code)
    print("Response:", response.text)

    response.raise_for_status()

if __name__ == "__main__":
    main()
