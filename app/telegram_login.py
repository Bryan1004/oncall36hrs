"""One-time interactive authorization. Stop app before running: see README."""
import asyncio
import getpass
from pathlib import Path
from telethon import TelegramClient
from app.config import Config


async def main():
    config = Config()
    if not config.telegram_api_id or not config.telegram_api_hash:
        raise SystemExit("先在 .env 填入 TELEGRAM_API_ID 和 TELEGRAM_API_HASH")
    Path(config.data_dir).mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(Path(config.data_dir) / "telegram"),
                            int(config.telegram_api_id), config.telegram_api_hash)
    try:
        await client.start(phone=lambda: input("Telegram 手机号（含区号）: "),
            code_callback=lambda: getpass.getpass("Telegram 登录码: "),
            password=lambda: getpass.getpass("Telegram 两步验证密码: "))
        print("Telegram 已授权。会话保存在服务器数据目录。")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
