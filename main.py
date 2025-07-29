#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Telegram bot implemented with the aiogram framework.

This bot provides two core capabilities for Persian‐speaking users:

1. Real time price information for the Iranian free market and precious metals.
   Data are pulled from the public JSON endpoint exposed by tgju.org.  The
   endpoint returns a large dictionary where each key corresponds to a
   financial instrument.  According to tgju's own profile pages, the key
   ``price_dollar_rl`` reflects the free market USD/IRR price and
   ``sekee_real`` holds the price of the Imam gold coin.  The key ``ons``
   stores the spot price of a troy ounce of gold in USD【769651394788223†L43-L53】【769651394788223†L418-L421】.
   Values returned from tgju.org are quoted in rials and include comma
   separators; we remove the commas and divide by 10 to convert rials to
   tomans before displaying them to the user.

2. Economic news headlines from major Iranian news agencies.  The bot
   fetches the latest items from the RSS feeds provided by IRNA, ISNA and
   Tasnim.  IRNA exposes RSS endpoints for each service; the general
   economy feed is located at ``https://www.isna.ir/rss/tp/34``【884631719232891†L160-L174】.
   Tasnim's Farsi site also publishes RSS feeds; the economy feed can be
   accessed via ``https://www.tasnimnews.com/fa/rss/feed/0/7/77/اقتصاد-ایران``【930970320324611†L0-L7】.

The bot is designed to be modular: separate handlers implement the `/start`,
`/help`, `/price` and `/news` commands as well as keyword recognition for
common Persian terms.  All outbound messages are sent in Persian and
formatted for right–to–left display.  Where appropriate an interim
``loading`` message is displayed so users know the bot is working.  Any
exceptions during data retrieval are caught and reported gracefully.

The code is ready for deployment on Heroku or a Linux server.  To run the
bot locally you need to install the dependencies defined in the requirements
list below and set the ``TELEGRAM_BOT_TOKEN`` environment variable to
your bot's API token.

Dependencies:
    aiogram>=2.25.2
    httpx>=0.24.1
    feedparser>=6.0.10

Example usage:
    $ export TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
    $ python telegram_bot.py

"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import feedparser  # type: ignore
import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.utils import executor


# -----------------------------------------------------------------------------
# Configuration
#
# All static configuration, such as URLs and number of headlines to return,
# should live in this section.  Fetching live configuration from environment
# variables makes the bot flexible for deployment.
# Telegram API token.  You can set this environment variable in your
# deployment environment.  If it is not set, the bot will raise an error.
BOT_TOKEN = "8230993264:AAEY2VCrQYL4XZxAnWbGyNiQv00OGf7ojzs"

# Endpoint for the tgju price API.  This endpoint returns a JSON document with
# many currency and commodity prices.  We only extract the keys defined in
# PRICE_KEYS below.  Should this endpoint change, update this constant.
TGJU_API_URL = "https://call5.tgju.org/ajax.json"

# RSS feeds for economic news.  Each entry in the dictionary maps a source
# name to its feed URL.  IRNA and ISNA publish their own RSS feeds, and
# Tasnim provides a Farsi feed for the economy section【930970320324611†L0-L7】.
NEWS_FEEDS: Dict[str, str] = {
    "Tasnim": "https://www.tasnimnews.com/fa/rss/feed/0/7/77/اقتصاد-ایران",
    "ISNA": "https://www.isna.ir/rss/tp/34",
    "IRNA": "https://www.irna.ir/rss",  # General RSS; we filter for economic headlines in code.
}

# Number of news headlines to return per source.  Adjust this value to change
# how many headlines each feed contributes to the `/news` command response.
HEADLINES_PER_SOURCE = 3

# Mapping of Persian keywords to price keys.  When a user sends a message
# containing any of these keywords, the bot will treat it as a request for
# the `/price` command.  Feel free to expand this mapping as needed.
KEYWORD_TRIGGER_LIST: List[str] = [
    "دلار",
    "سکه",
    "طلا",
    "ارز",
    "قیمت",
]

# Keys in the tgju JSON object corresponding to the values we want to display.
# The mapping is from a human‐readable label (used in the output) to the JSON
# key returned by tgju.  The labels are intentionally kept short and
# descriptive.  The JSON keys and meaning were verified by inspecting the
# endpoint【769651394788223†L43-L53】【769651394788223†L418-L421】.
PRICE_KEYS: Dict[str, str] = {
    "قیمت لحظه‌ای دلار": "price_dollar_rl",  # Free market USD/IRR price
    "سکه امامی": "sekee_real",               # Imam gold coin price
    "انس طلا جهانی": "ons",                  # Gold ounce in USD
}

# -----------------------------------------------------------------------------
# Helper functions
#
CHANNEL_ID = "@sarasoo"


async def is_user_member(user_id: int, bot: Bot, channel_username: str) -> bool:
    try:
        member = await bot.get_chat_member(channel_username, user_id)
        return member.status in ["member", "creator", "administrator"]
    except Exception as e:
        logging.error(f"Error checking membership: {e}")
        return False




async def fetch_json(url: str) -> Dict:
    """Fetch a JSON document from the given URL asynchronously.

    Args:
        url: The URL to fetch.

    Returns:
        Parsed JSON as a dictionary.

    Raises:
        httpx.HTTPError: If the request fails.
        ValueError: If the response body is not valid JSON.
    """
    async with httpx.AsyncClient(
            timeout=10.0,
            headers={"User-Agent": "Mozilla/5.0"}
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def fetch_prices() -> Tuple[str, Dict[str, Tuple[str, datetime]]]:
    """Fetch and parse the latest market prices from tgju.org."""

    try:
        data = await fetch_json(TGJU_API_URL)
        current = data.get("current", {})
        result: Dict[str, Tuple[str, datetime]] = {}

        for label, key in PRICE_KEYS.items():
            entry = current.get(key)
            if not entry or "p" not in entry or "ts" not in entry:
                raise ValueError(f"Missing or malformed data for '{key}'")

            price_str: str = entry.get("p", "0")
            ts_str: str = entry.get("ts")

            # پاک کردن کاما و تبدیل به عدد
            price_clean = price_str.replace(",", "")
            try:
                value_num = float(price_clean)
            except ValueError:
                value_num = 0.0

            # برای دلار و سکه تبدیل به تومان
            if key != "ons":
                value_num /= 10

            # فرمت نمایش
            if key == "ons":
                formatted_value = f"{value_num:,.2f}"
            else:
                formatted_value = f"{int(value_num):,}"

            try:
                timestamp = datetime.fromisoformat(ts_str)
            except Exception:
                timestamp = datetime.now(timezone.utc)

            result[label] = (formatted_value, timestamp)

        server_time = datetime.now(timezone.utc).isoformat()
        return server_time, result

    except Exception as e:
        logging.exception("❌ خطا در تابع fetch_prices(): %s", str(e))
        raise  # تا در هندلر بالا پیام خطا به کاربر نمایش داده شود



from bs4 import BeautifulSoup

async def fetch_news() -> List[Tuple[str, str]]:
    headlines: List[Tuple[str, str]] = []
    economy_keywords = [
        "اقتصاد",
        "اقتصادی",
        "بانک",
        "ارز",
        "پول",
        "بورس",
        "سکه",
        "دلار",
    ]

    for source, url in NEWS_FEEDS.items():
        try:
            async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
                response = await client.get(url)
                response.encoding = 'utf-8'  # ⬅️ این خط کلیدی است
                response.raise_for_status()

            # استفاده از محتوای متنی به جای باینری
            soup = BeautifulSoup(response.text, "xml")

            items = soup.find_all("item")[:HEADLINES_PER_SOURCE]
            for item in items:
                title = item.title.text.strip()
                link = item.link.text.strip()

                # فیلتر اقتصادی فقط برای IRNA
                if source == "IRNA":
                    if not any(k in title for k in economy_keywords):
                        continue

                headlines.append((title, link))

        except Exception as exc:
            logging.error("Error fetching news from %s: %s", source, exc)

    return headlines[: HEADLINES_PER_SOURCE * len(NEWS_FEEDS)]





def format_time_difference(past: datetime) -> str:
    """Return a human friendly Persian string representing the time elapsed."""

    # اطمینان از اینکه هر دو datetime ها aware هستن
    if past.tzinfo is None:
        past = past.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    diff = (now - past).total_seconds() / 60  # minutes

    if diff < 1:
        return "لحظاتی پیش"
    elif diff < 60:
        minutes = int(diff)
        return f"{minutes} دقیقه پیش"
    else:
        hours = int(diff // 60)
        return f"{hours} ساعت پیش"



# -----------------------------------------------------------------------------
# Bot setup and handlers
#
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN environment variable is not set. "
        "Please set it before running the bot."
    )

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())


@dp.message_handler(commands=["start", "price", "news"])
async def restricted_handler(message: types.Message):
    user_id = message.from_user.id
    channel_username = "@sarasoo"

    # بررسی عضویت
    is_sub = await is_user_member(user_id, bot, channel_username)
    if not is_sub:
        await message.answer(
            "❗ برای استفاده از این ربات، ابتدا باید عضو کانال ما شوید:\n"
            "👉 [عضویت در کانال](https://t.me/sarasoo)",
            parse_mode="Markdown"
        )
        return

    # اجرای دستورات در صورت عضویت
    if message.text == "/start":
        text = (
            "سلام! 👋\n"
            "به ربات قیمت لحظه‌ای ارز و اخبار اقتصادی خوش آمدید.\n\n"
            "با استفاده از دستورات زیر می‌توانید اطلاعات دریافت کنید:\n"
            "• /price — دریافت قیمت لحظه‌ای دلار، سکه و انس طلا\n"
            "• /news — مشاهده جدیدترین تیترهای اقتصادی از خبرگزاری‌ها\n"
            "• /help — راهنما و توضیحات بیشتر\n\n"
            "می‌توانید به‌جای استفاده از دستورات، کلمات کلیدی مانند «دلار»، «سکه» یا "
            "«اخبار اقتصادی» را نیز ارسال کنید."
        )
        await message.answer(text)
    elif message.text == "/price":
        await cmd_price(message)
    elif message.text == "/news":
        await cmd_news(message)



@dp.message_handler(commands=["help"])
async def cmd_help(message: types.Message) -> None:
    """Handle the /help command.

    Provides detailed help in Persian describing each command and examples.
    """
    help_text = (
        "راهنمای ربات:\n\n"
        "• /price — این دستور قیمت لحظه‌ای ارز آزاد (دلار)، سکه امامی و انس طلا"
        " جهانی را از سایت Tgju دریافت کرده و نمایش می‌دهد.\n"
        "• /news — آخرین تیترهای اقتصادی از خبرگزاری‌های معتبر (ایرنا، ایسنا، تسنیم)"
        " را به همراه لینک نمایش می‌دهد.\n"
        "• /start — معرفی کوتاه ربات و نحوه استفاده.\n\n"
        "همچنین می‌توانید کلمات «دلار»، «سکه»، «طلا» یا «اخبار اقتصادی» را بدون"
        " دستور تایپ کنید تا ربات پاسخ مناسب بدهد."
    )
    await message.answer(help_text)


@dp.message_handler(commands=["price"])
async def cmd_price(message: types.Message) -> None:
    """Handle the /price command.

    Fetches the latest prices from TGJU and sends a formatted message to
    the user.  A loading indicator is displayed while fetching data.
    """
    # Send a temporary loading message
    loading_msg = await message.answer("⏳ در حال دریافت اطلاعات...")
    try:
        server_time, prices = await fetch_prices()
        # Build the response text
        lines: List[str] = []
        for label, (value, timestamp) in prices.items():
            if label == "انس طلا جهانی":
                lines.append(f"📉 {label}: {value} دلار")
            else:
                lines.append(f"💵 {label}: {value} تومان")
        # Determine the most recent update time among the instruments
        last_update = max(ts for _, (_, ts) in prices.items())
        time_diff_str = format_time_difference(last_update)
        lines.append(f"(به‌روزرسانی: {time_diff_str})")
        # Join lines with two spaces at end of each line to preserve RTL ordering
        response_text = "  \n".join(lines)
        await message.answer(response_text)
    except Exception as exc:
        logger.exception("Error in /price command")
        await message.answer("❌ خطا در دریافت داده‌ها. لطفاً بعداً تلاش کنید.")
    finally:
        # Delete the loading message to keep the chat clean
        try:
            await loading_msg.delete()
        except Exception:
            pass


@dp.message_handler(commands=["news"])
async def cmd_news(message: types.Message) -> None:
    """Handle the /news command.

    Retrieves headlines from the configured news feeds and sends them in a
    numbered list to the user.  A loading message is shown while data
    fetching is in progress.
    """
    loading_msg = await message.answer("⏳ در حال دریافت تیترهای خبری...")
    try:
        items = await fetch_news()
        if not items:
            await message.answer("هیچ تیتر اقتصادی پیدا نشد. لطفاً بعداً تلاش کنید.")
        else:
            lines = ["📰 تیترهای اقتصادی جدید:"]
            for idx, (title, link) in enumerate(items, start=1):
                lines.append(f"{idx}. [{title}]({link})")
            # Join using newline; no extra spaces needed here
            response_text = "\n".join(lines)
            await message.answer(response_text, parse_mode="Markdown")
    except Exception:
        logger.exception("Error in /news command")
        await message.answer("❌ خطا در دریافت اخبار. لطفاً بعداً تلاش کنید.")
    finally:
        try:
            await loading_msg.delete()
        except Exception:
            pass



@dp.message_handler()
async def keyword_handler(message: types.Message) -> None:
    """Handle plain text messages by looking for keywords.

    If the message contains any of the trigger words defined in
    ``KEYWORD_TRIGGER_LIST``, the bot will invoke the appropriate command
    handler.  Otherwise, it replies with a generic help message.
    """
    text = message.text or ""
    lowered = text.strip().lower()
    # Check for news keywords first
    if "اخبار" in lowered or "اخبار اقتصادی" in lowered:
        # Defer to news handler
        await cmd_news(message)
        return
    # Check for price keywords
    if any(keyword in lowered for keyword in KEYWORD_TRIGGER_LIST):
        await cmd_price(message)
        return
    # Default reply for unknown messages
    await message.answer(
        "متوجه نشدم. برای مشاهده راهنما دستور /help را ارسال کنید."
    )


async def on_startup(_):
    """Runs on bot startup to log a message."""
    logger.info("Bot started successfully.")


def main() -> None:
    """Entrypoint for running the bot.

    This function simply starts polling and blocks until interrupted.  It
    exists so the module can be imported without side effects.
    """
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)


if __name__ == "__main__":
    main()
