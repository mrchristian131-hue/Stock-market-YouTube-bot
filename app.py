import os
import subprocess
import tempfile
import textwrap
from datetime import datetime
from pathlib import Path

import edge_tts
import imageio.v2 as imageio
import imageio_ffmpeg
import numpy as np
import yfinance as yf
from flask import Flask, redirect, render_template_string, request, session
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from PIL import Image, ImageDraw, ImageFont
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-in-railway")

MARKETS = {"S&P 500": "^GSPC", "Dow Jones": "^DJI", "Nasdaq": "^IXIC"}
YOUTUBE_SCOPE = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]
DEFAULT_BASE_URL = "https://stock-market-youtube-bot-production-42e0.up.railway.app"
APP_BASE_URL = os.environ.get("APP_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
REDIRECT_URI = f"{APP_BASE_URL}/oauth2callback"


def get_market_data():
    results = []
    for name, symbol in MARKETS.items():
        try:
            history = yf.download(symbol, period="5d", interval="1d", progress=False,
                                  auto_adjust=False, multi_level_index=False)
            if len(history) < 2:
                results.append({"name": name, "error": "Market data is not available yet."})
                continue
            close_prices = history["Close"].dropna()
            latest_close = float(close_prices.iloc[-1])
            previous_close = float(close_prices.iloc[-2])
            change = latest_close - previous_close
            percent_change = (change / previous_close) * 100
            results.append({"name": name, "price": latest_close, "change": change,
                            "percent": percent_change,
                            "color": "#37a31b" if change >= 0 else "#ff332b"})
        except Exception as exc:
            results.append({"name": name, "error": str(exc)})
    return results


SECTOR_ETFS = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Energy": "XLE",
    "Health Care": "XLV",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}


def get_sector_data():
    sectors = []
    for name, symbol in SECTOR_ETFS.items():
        try:
            history = yf.download(
                symbol,
                period="5d",
                interval="1d",
                progress=False,
                auto_adjust=False,
                multi_level_index=False,
            )
            close_prices = history["Close"].dropna()
            if len(close_prices) < 2:
                sectors.append({"name": name, "error": "Sector data unavailable"})
                continue

            latest_close = float(close_prices.iloc[-1])
            previous_close = float(close_prices.iloc[-2])
            change = latest_close - previous_close
            percent_change = (change / previous_close) * 100

            sectors.append({
                "name": name,
                "symbol": symbol,
                "price": latest_close,
                "change": change,
                "percent": percent_change,
            })
        except Exception as exc:
            sectors.append({"name": name, "error": str(exc)})
    return sectors


MOVER_WATCHLIST = {
    "Apple": "AAPL",
    "Microsoft": "MSFT",
    "Nvidia": "NVDA",
    "Amazon": "AMZN",
    "Meta": "META",
    "Alphabet": "GOOGL",
    "Tesla": "TSLA",
    "JPMorgan": "JPM",
    "Walmart": "WMT",
    "Broadcom": "AVGO",
    "Eli Lilly": "LLY",
    "Exxon Mobil": "XOM",
}


def get_stock_movers():
    """Return leading gainers and laggards from a liquid large-cap watchlist."""
    movers = []

    try:
        tickers = list(MOVER_WATCHLIST.values())
        history = yf.download(
            tickers,
            period="5d",
            interval="1d",
            progress=False,
            auto_adjust=False,
            group_by="ticker",
            threads=True,
        )

        for name, symbol in MOVER_WATCHLIST.items():
            try:
                if len(tickers) == 1:
                    close_prices = history["Close"].dropna()
                else:
                    close_prices = history[symbol]["Close"].dropna()

                if len(close_prices) < 2:
                    continue

                latest_close = float(close_prices.iloc[-1])
                previous_close = float(close_prices.iloc[-2])
                change = latest_close - previous_close
                percent_change = (change / previous_close) * 100

                movers.append({
                    "name": name,
                    "symbol": symbol,
                    "price": latest_close,
                    "change": change,
                    "percent": percent_change,
                })
            except Exception as exc:
                print(f"Mover data failed for {symbol}: {exc}")
    except Exception as exc:
        print(f"Stock mover batch download failed: {exc}")

    gainers = sorted(movers, key=lambda m: m["percent"], reverse=True)[:3]
    laggards = sorted(movers, key=lambda m: m["percent"])[:3]
    return gainers, laggards


def get_market_news(limit=5):
    """Fetch a small set of current market headlines from yfinance Search."""
    stories = []
    seen = set()
    queries = ["stock market", "S&P 500", "Nasdaq"]

    for query in queries:
        if len(stories) >= limit:
            break
        try:
            results = yf.Search(
                query,
                max_results=0,
                news_count=limit,
                lists_count=0,
                include_cb=False,
                include_nav_links=False,
                include_research=False,
                raise_errors=False,
            ).news or []

            for item in results:
                if not isinstance(item, dict):
                    continue

                content = item.get("content", item)
                if not isinstance(content, dict):
                    continue

                title = content.get("title") or content.get("headline") or item.get("title")
                provider = content.get("provider", {})
                publisher = (
                    provider.get("displayName")
                    if isinstance(provider, dict)
                    else None
                ) or content.get("publisher") or item.get("publisher") or "Market news"

                if not title:
                    continue

                title = " ".join(str(title).split())
                key = title.lower()
                if key in seen:
                    continue

                seen.add(key)
                stories.append({
                    "title": title,
                    "publisher": str(publisher),
                })

                if len(stories) >= limit:
                    break
        except Exception as exc:
            print(f"Market news query failed for {query}: {exc}")

    return stories


def client_config():
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET are missing in Railway.")
    return {"web": {"client_id": client_id, "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [REDIRECT_URI]}}


def credentials_from_environment():
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    if not refresh_token or not client_id or not client_secret:
        return None
    credentials = Credentials(token=None, refresh_token=refresh_token,
                              token_uri="https://oauth2.googleapis.com/token",
                              client_id=client_id, client_secret=client_secret,
                              scopes=YOUTUBE_SCOPE)
    credentials.refresh(Request())
    return credentials


def youtube_service():
    credentials = credentials_from_environment()
    if credentials is None:
        raise RuntimeError("YouTube is not fully connected. Open /authorize, then add the displayed refresh token to Railway as YOUTUBE_REFRESH_TOKEN.")
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def load_font(size):
    for candidate in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def make_market_video(report_type, markets):
    width, height, fps = 1280, 720, 6
    scene_seconds = 42
    sectors = get_sector_data()
    news_stories = get_market_news(limit=5)
    top_gainers, top_laggards = get_stock_movers()

    valid_markets = [m for m in markets if "error" not in m]
    valid_sectors = [s for s in sectors if "error" not in s]

    avg_percent = (
        sum(m["percent"] for m in valid_markets) / len(valid_markets)
        if valid_markets else 0
    )
    strongest = max(valid_markets, key=lambda m: m["percent"]) if valid_markets else None
    weakest = min(valid_markets, key=lambda m: m["percent"]) if valid_markets else None
    strongest_sectors = sorted(valid_sectors, key=lambda s: s["percent"], reverse=True)[:3]
    weakest_sectors = sorted(valid_sectors, key=lambda s: s["percent"])[:3]

    if avg_percent > 0.25:
        tone = "positive"
        tone_sentence = "The broad market tone is positive so far."
    elif avg_percent < -0.25:
        tone = "cautious"
        tone_sentence = "The broad market tone is cautious so far."
    else:
        tone = "mixed"
        tone_sentence = "The broad market tone is mixed so far."

    def pct_line(item):
        sign = "+" if item["percent"] >= 0 else ""
        return f"{item['name']} is {sign}{item['percent']:.2f} percent"

    index_sentences = ". ".join(pct_line(m) for m in valid_markets)
    if index_sentences:
        index_sentences += "."

    leader_sentence = ""
    if strongest and weakest:
        leader_sentence = (
            f"The strongest of the three major indexes is {strongest['name']} at "
            f"{'+' if strongest['percent'] >= 0 else ''}{strongest['percent']:.2f} percent. "
            f"The weakest is {weakest['name']} at "
            f"{'+' if weakest['percent'] >= 0 else ''}{weakest['percent']:.2f} percent."
        )

    sector_leader_sentence = ""
    if strongest_sectors:
        sector_leader_sentence = (
            "Leading sectors include "
            + ", ".join(
                f"{s['name']} at {'+' if s['percent'] >= 0 else ''}{s['percent']:.2f} percent"
                for s in strongest_sectors
            )
            + "."
        )

    sector_laggard_sentence = ""
    if weakest_sectors:
        sector_laggard_sentence = (
            "The weakest sector groups include "
            + ", ".join(
                f"{s['name']} at {'+' if s['percent'] >= 0 else ''}{s['percent']:.2f} percent"
                for s in weakest_sectors
            )
            + "."
        )

    # Decide how much attention today's market deserves.
    index_move = max((abs(m["percent"]) for m in valid_markets), default=0)
    sector_move = max((abs(s["percent"]) for s in valid_sectors), default=0)
    mover_move = max(
        [abs(m["percent"]) for m in (top_gainers + top_laggards)],
        default=0,
    )

    activity_score = 0
    if index_move >= 1.0:
        activity_score += 2
    elif index_move >= 0.5:
        activity_score += 1

    if sector_move >= 1.5:
        activity_score += 2
    elif sector_move >= 0.75:
        activity_score += 1

    if mover_move >= 4.0:
        activity_score += 2
    elif mover_move >= 2.0:
        activity_score += 1

    if len(news_stories) >= 5:
        activity_score += 2
    elif len(news_stories) >= 3:
        activity_score += 1

    if activity_score >= 6:
        day_type = "high activity"
    elif activity_score >= 3:
        day_type = "active"
    else:
        day_type = "quiet"

    if news_stories:
        news_narration = (
            "Now let's turn to the top market stories on the radar. "
            + " ".join(
                f"Story {i + 1}: {story['title']}. Source: {story['publisher']}."
                for i, story in enumerate(news_stories)
            )
            + " These headlines are a watchlist, not a claim that any single headline caused today's market move. "
            "The key is how investors respond as the underlying reporting is digested and prices adjust. "
        )
    else:
        news_narration = (
            "Current market headlines are temporarily unavailable, so this report will stay focused "
            "on verified index, sector, and stock-price data. "
        )

    if top_gainers:
        mover_narration = (
            "Now for the large-cap mover check. "
            "Among our selected liquid watchlist, the strongest names include "
            + ", ".join(
                f"{m['name']}, ticker {m['symbol']}, at {'+' if m['percent'] >= 0 else ''}{m['percent']:.2f} percent"
                for m in top_gainers
            )
            + ". "
        )
        if top_laggards:
            mover_narration += (
                "On the weaker side of the same watchlist, the laggards include "
                + ", ".join(
                    f"{m['name']}, ticker {m['symbol']}, at {'+' if m['percent'] >= 0 else ''}{m['percent']:.2f} percent"
                    for m in top_laggards
                )
                + ". "
            )
        mover_narration += (
            "These are selected large-cap names, not a ranking of every stock in the market. "
            "They are useful for seeing where momentum is concentrated inside a group of widely followed companies. "
        )
    else:
        mover_narration = (
            "Large-cap mover data is temporarily unavailable, so we will continue with index, sector, and headline data. "
        )

    if day_type == "high activity":
        dynamic_middle = (
            "Today qualifies as a high-activity session based on the size of the moves across indexes, sectors, "
            "large-cap stocks, and the amount of headline flow. "
            "On a day like this, the relationships between these pieces matter more than any single number. "
            "Strong index movement backed by broad sector participation can carry more weight than a move driven by only one group. "
            "If the largest stock movers line up with the strongest sectors, that can reinforce the idea that leadership is broadening. "
            "If they diverge, company-specific news or narrow positioning may be doing more of the work. "
            "This is also the kind of session where momentum can change quickly, so watch whether early leaders keep attracting buyers "
            "and whether weak groups continue to make new lows or begin to stabilize. "
            "Volume, breadth, and confirmation between the major indexes become especially important when the tape is moving fast. "
        )
        dynamic_close = (
            "Because today's activity level is elevated, this is a session where it makes sense to stay alert for follow-through, "
            "reversals, and new headline catalysts. "
            "If the market keeps confirming the same message across indexes, sectors, and large-cap leaders, the trend is clearer. "
            "If those signals begin to split apart, the character of the session may be changing. "
        )
    elif day_type == "active":
        dynamic_middle = (
            "Today looks like an active but not extreme session. "
            "There is enough movement to make leadership and rotation worth watching closely. "
            "The most useful question is whether the strongest areas can hold their advantage as the session develops. "
            "If sector leaders stay firm while the major indexes remain aligned, the market message is more consistent. "
            "If leadership rotates quickly from one group to another, the tape may be more tactical and less directional. "
        )
        dynamic_close = (
            "With a moderate level of activity, the focus stays on confirmation. "
            "We want to see whether the current leaders remain in control or whether the market settles into a more mixed pattern. "
        )
    else:
        dynamic_middle = (
            "Today is shaping up as a quieter session. "
            "That does not mean there is nothing to watch; it means the most useful information may come from smaller shifts in leadership "
            "rather than dramatic index moves. "
            "On quieter days, sector rotation and individual-stock strength can stand out more clearly because the major averages are not "
            "dominating the story. "
        )
        dynamic_close = (
            "Because today's activity is relatively quiet, there is no reason to stretch the story beyond what the data supports. "
            "The main job is to keep an eye on whether new headlines or late-session movement change the character of the market. "
        )

    if report_type.lower() == "close":
        session_context = (
            "Now that the regular session is wrapping up, the focus shifts from early direction to what actually held into the close. "
            "The closing tape can tell us whether buyers or sellers maintained control, whether sector leadership survived the full session, "
            "and whether late-day trading confirmed or challenged the story we saw earlier. "
        )
        timing_context = (
            "For a closing report, the final hour matters because institutions rebalance positions, traders reduce risk, "
            "and late headlines can change the character of the day. "
            "The key question is whether the strongest indexes and sectors finished near their highs, faded into the bell, "
            "or reversed from earlier weakness. "
        )
        wrap_context = (
            "Looking ahead to the next session, the main things to carry forward are today's strongest and weakest areas, "
            "whether leadership broadened or narrowed, and whether any late-day headlines could affect the next opening. "
        )
    else:
        session_context = (
            "With the session getting underway, the focus is on whether the early direction can hold as volume builds "
            "and new information is priced into the market. "
            "The opening tape can change quickly, so we want to see whether index direction is confirmed by sector leadership "
            "and large-cap momentum. "
        )
        timing_context = (
            "For an opening report, early moves can change quickly as more volume enters the market and new information is priced in. "
            "The key question is whether the strongest indexes and sectors keep their advantage or whether the tape rotates as the morning develops. "
        )
        wrap_context = (
            "For the rest of the session, watch whether the major indexes confirm one another, whether sector leadership holds, "
            "and whether new headlines change the tone before the closing bell. "
        )

    narration = (
        f"Welcome to the Mr. Christian Daily Market Update for "
        f"{datetime.now().strftime('%A, %B %d, %Y')}. "
        f"This is the stock market {report_type.lower()} report. "
        f"Today's market is currently classified as a {day_type} session based on price movement, sector rotation, "
        f"large-cap movers, and headline flow. "

        f"First, the major United States stock indexes. "
        f"{index_sentences} {tone_sentence} {leader_sentence} "
        f"That gives us the headline direction, but the index numbers are only the first layer of the story. "
        f"The next question is whether the move is broad, concentrated, or beginning to rotate. "

        f"Now let's look beneath the surface at sector performance. "
        f"{sector_leader_sentence} {sector_laggard_sentence} "
        f"Sector rotation helps tell us whether the market's strength or weakness is being shared across several groups "
        f"or carried by only a narrow part of the market. "

        f"{dynamic_middle} "

        f"{news_narration} "
        f"The important thing with headlines is to separate the fact of the headline from the market's reaction to it. "
        f"A major story can sound positive or negative and still produce a different price response than expected. "
        f"That is why we watch both the news and the tape. "

        f"{mover_narration} "
        f"Large-cap movers can show where investors are making stronger individual-company decisions. "
        f"A stock outperforming while its sector is also strong may be participating in a broader theme. "
        f"A stock moving sharply against its sector may be reacting to company-specific information instead. "

        f"{session_context} "
        f"{timing_context} "

        f"We also want to watch confirmation between the indexes. "
        f"If the Dow Jones, the S and P 500, and the Nasdaq are moving in the same direction, the message is usually clearer. "
        f"If they are split, the market may be dealing with rotation, differences in sector exposure, or concentrated leadership. "

        f"{dynamic_close} "

        f"For this {report_type.lower()} update, the overall market tone is {tone}. "
        f"The strongest and weakest indexes, the leading and lagging sectors, the current headlines, "
        f"and the large-cap movers together give us a more complete picture than any single number. "

        f"Before we wrap up, here is the key takeaway. "
        f"{wrap_context} "
        f"Watch whether the major indexes confirm one another. "
        f"Watch whether the leading sectors maintain their strength. "
        f"Watch whether lagging sectors stabilize or weaken further. "
        f"Watch whether the strongest large-cap names continue to attract buyers. "
        f"And watch for new economic, earnings, policy, or company headlines that could change momentum. "

        f"We will keep focusing on direction, breadth, sector rotation, momentum, and the market's reaction to new information. "
        f"This report is for informational purposes only and is not financial advice. "
        f"Always do your own research and make decisions based on your own objectives and risk tolerance. "
        f"This is Mr. Christian with your Daily Market Update."
    )

    def market_rows(draw, start_y=230, font_size=34):
        y = start_y
        for market in markets:
            if "error" in market:
                draw.text((80, y), f"{market['name']}: data unavailable",
                          fill=(170, 180, 197), font=load_font(font_size))
            else:
                value_color = (55, 163, 27) if market["change"] >= 0 else (255, 51, 43)
                sign = "+" if market["change"] >= 0 else ""
                draw.text((80, y), market["name"], fill=(245, 247, 250), font=load_font(font_size))
                draw.text((450, y), f"{market['price']:,.2f}", fill=(245, 247, 250), font=load_font(font_size))
                draw.text((745, y), f"{sign}{market['change']:,.2f}", fill=value_color, font=load_font(font_size))
                draw.text((985, y), f"{sign}{market['percent']:.2f}%", fill=value_color, font=load_font(font_size))
            y += 100

    scenes = []

    image = Image.new("RGB", (width, height), (11, 18, 32))
    draw = ImageDraw.Draw(image)
    draw.text((70, 70), "Mr. Christian", fill=(245, 247, 250), font=load_font(72))
    draw.text((70, 175), "Daily Market Update", fill=(170, 180, 197), font=load_font(48))
    draw.rectangle((70, 245, 390, 253), fill=(39, 104, 189))
    draw.text((70, 285), f"Stock Market {report_type.title()}", fill=(245, 247, 250), font=load_font(54))
    draw.text((70, 365), datetime.now().strftime("%A, %B %d, %Y"), fill=(170, 180, 197), font=load_font(34))
    draw.text((70, 565), f"Session: {day_type.title()}", fill=(245, 247, 250), font=load_font(30))
    draw.text((70, 610), "Narrated private preview", fill=(170, 180, 197), font=load_font(24))
    scenes.append(np.asarray(image))

    image = Image.new("RGB", (width, height), (11, 18, 32))
    draw = ImageDraw.Draw(image)
    draw.text((70, 55), "Major Index Board", fill=(245, 247, 250), font=load_font(58))
    draw.text((80, 155), "INDEX", fill=(170, 180, 197), font=load_font(28))
    draw.text((450, 155), "PRICE", fill=(170, 180, 197), font=load_font(28))
    draw.text((745, 155), "CHANGE", fill=(170, 180, 197), font=load_font(28))
    draw.text((985, 155), "PERCENT", fill=(170, 180, 197), font=load_font(28))
    market_rows(draw)
    scenes.append(np.asarray(image))

    image = Image.new("RGB", (width, height), (11, 18, 32))
    draw = ImageDraw.Draw(image)
    draw.text((70, 55), "Index Leadership", fill=(245, 247, 250), font=load_font(58))
    draw.text((70, 150), f"Broad market tone: {tone}", fill=(170, 180, 197), font=load_font(34))
    if strongest:
        draw.text((80, 280), "Strongest index", fill=(170, 180, 197), font=load_font(30))
        draw.text((80, 340), f"{strongest['name']}  {'+' if strongest['percent'] >= 0 else ''}{strongest['percent']:.2f}%",
                  fill=(245, 247, 250), font=load_font(48))
    if weakest:
        draw.text((80, 470), "Weakest index", fill=(170, 180, 197), font=load_font(30))
        draw.text((80, 530), f"{weakest['name']}  {'+' if weakest['percent'] >= 0 else ''}{weakest['percent']:.2f}%",
                  fill=(245, 247, 250), font=load_font(48))
    scenes.append(np.asarray(image))

    image = Image.new("RGB", (width, height), (11, 18, 32))
    draw = ImageDraw.Draw(image)
    draw.text((70, 55), "Sector Leaders", fill=(245, 247, 250), font=load_font(58))
    y = 190
    for sector in strongest_sectors:
        sign = "+" if sector["percent"] >= 0 else ""
        draw.text((90, y), f"{sector['name']}: {sign}{sector['percent']:.2f}%",
                  fill=(55, 163, 27) if sector["percent"] >= 0 else (255, 51, 43),
                  font=load_font(42))
        y += 120
    scenes.append(np.asarray(image))

    image = Image.new("RGB", (width, height), (11, 18, 32))
    draw = ImageDraw.Draw(image)
    draw.text((70, 55), "Sector Laggards", fill=(245, 247, 250), font=load_font(58))
    y = 190
    for sector in weakest_sectors:
        sign = "+" if sector["percent"] >= 0 else ""
        draw.text((90, y), f"{sector['name']}: {sign}{sector['percent']:.2f}%",
                  fill=(55, 163, 27) if sector["percent"] >= 0 else (255, 51, 43),
                  font=load_font(42))
        y += 120
    scenes.append(np.asarray(image))

    # Market headlines
    if news_stories:
        for page_index in range(0, len(news_stories), 3):
            page_stories = news_stories[page_index:page_index + 3]
            image = Image.new("RGB", (width, height), (11, 18, 32))
            draw = ImageDraw.Draw(image)
            draw.text((70, 55), "Top Market Stories", fill=(245, 247, 250), font=load_font(58))
            y = 150

            for story_index, story in enumerate(page_stories, start=page_index + 1):
                draw.text(
                    (80, y),
                    f"{story_index}. {story['publisher']}",
                    fill=(170, 180, 197),
                    font=load_font(26),
                )
                y += 45

                wrapped = textwrap.wrap(story["title"], width=54)[:3]
                for line in wrapped:
                    draw.text(
                        (100, y),
                        line,
                        fill=(245, 247, 250),
                        font=load_font(30),
                    )
                    y += 42
                y += 42

            draw.text(
                (70, 655),
                "Headlines only â¢ Verify full reporting before drawing conclusions",
                fill=(170, 180, 197),
                font=load_font(21),
            )
            scenes.append(np.asarray(image))
    else:
        image = Image.new("RGB", (width, height), (11, 18, 32))
        draw = ImageDraw.Draw(image)
        draw.text((70, 55), "Top Market Stories", fill=(245, 247, 250), font=load_font(58))
        draw.text((90, 260), "Headlines temporarily unavailable",
                  fill=(245, 247, 250), font=load_font(40))
        draw.text((90, 330), "Continuing with verified market data",
                  fill=(170, 180, 197), font=load_font(30))
        scenes.append(np.asarray(image))

    # Large-cap stock movers
    image = Image.new("RGB", (width, height), (11, 18, 32))
    draw = ImageDraw.Draw(image)
    draw.text((70, 55), "Large-Cap Leaders", fill=(245, 247, 250), font=load_font(58))
    draw.text((70, 125), "Selected liquid watchlist", fill=(170, 180, 197), font=load_font(25))
    y = 205
    if top_gainers:
        for mover in top_gainers:
            sign = "+" if mover["percent"] >= 0 else ""
            draw.text(
                (90, y),
                f"{mover['symbol']}  {mover['name']}",
                fill=(245, 247, 250),
                font=load_font(34),
            )
            draw.text(
                (885, y),
                f"{sign}{mover['percent']:.2f}%",
                fill=(55, 163, 27) if mover["percent"] >= 0 else (255, 51, 43),
                font=load_font(38),
            )
            y += 120
    else:
        draw.text((90, 285), "Mover data temporarily unavailable",
                  fill=(170, 180, 197), font=load_font(34))
    scenes.append(np.asarray(image))

    image = Image.new("RGB", (width, height), (11, 18, 32))
    draw = ImageDraw.Draw(image)
    draw.text((70, 55), "Large-Cap Laggards", fill=(245, 247, 250), font=load_font(58))
    draw.text((70, 125), "Selected liquid watchlist", fill=(170, 180, 197), font=load_font(25))
    y = 205
    if top_laggards:
        for mover in top_laggards:
            sign = "+" if mover["percent"] >= 0 else ""
            draw.text(
                (90, y),
                f"{mover['symbol']}  {mover['name']}",
                fill=(245, 247, 250),
                font=load_font(34),
            )
            draw.text(
                (885, y),
                f"{sign}{mover['percent']:.2f}%",
                fill=(55, 163, 27) if mover["percent"] >= 0 else (255, 51, 43),
                font=load_font(38),
            )
            y += 120
    else:
        draw.text((90, 285), "Mover data temporarily unavailable",
                  fill=(170, 180, 197), font=load_font(34))
    scenes.append(np.asarray(image))

    image = Image.new("RGB", (width, height), (11, 18, 32))
    draw = ImageDraw.Draw(image)
    draw.text((70, 55), "What To Watch", fill=(245, 247, 250), font=load_font(58))
    items = [
        "â¢ Does the opening direction hold?",
        "â¢ Do leading sectors keep their strength?",
        "â¢ Does market breadth improve or weaken?",
        "â¢ Do new headlines change momentum?",
    ]
    y = 180
    for item in items:
        draw.text((90, y), item, fill=(245, 247, 250), font=load_font(34))
        y += 105
    scenes.append(np.asarray(image))

    image = Image.new("RGB", (width, height), (11, 18, 32))
    draw = ImageDraw.Draw(image)
    checklist_title = "Closing Takeaways" if report_type.lower() == "close" else "Session Checklist"
    draw.text((70, 55), checklist_title, fill=(245, 247, 250), font=load_font(58))
    draw.text((70, 120), f"Session type: {day_type.title()}", fill=(170, 180, 197), font=load_font(27))
    checklist_items = [
        "Major indexes confirming?",
        "Sector leadership holding?",
        "Market breadth improving?",
        "Large-cap momentum continuing?",
        "New headlines changing the tape?",
    ]
    y = 190
    for item in checklist_items:
        draw.text((95, y), f"â¢ {item}", fill=(245, 247, 250), font=load_font(34))
        y += 92
    scenes.append(np.asarray(image))

    image = Image.new("RGB", (width, height), (11, 18, 32))
    draw = ImageDraw.Draw(image)
    draw.text((70, 55), "Market Snapshot Recap", fill=(245, 247, 250), font=load_font(58))
    y = 175
    for market in valid_markets:
        sign = "+" if market["percent"] >= 0 else ""
        color = (55, 163, 27) if market["percent"] >= 0 else (255, 51, 43)
        draw.text((90, y), f"{market['name']}: {sign}{market['percent']:.2f}%", fill=color, font=load_font(40))
        y += 92
    if strongest_sectors:
        draw.text((90, 500), f"Top sector: {strongest_sectors[0]['name']}",
                  fill=(245, 247, 250), font=load_font(34))
    draw.text((70, 645), "Informational purposes only â¢ Not financial advice",
              fill=(170, 180, 197), font=load_font(24))
    scenes.append(np.asarray(image))

    # Closing card
    image = Image.new("RGB", (width, height), (11, 18, 32))
    draw = ImageDraw.Draw(image)
    draw.text((70, 115), "Mr. Christian", fill=(245, 247, 250), font=load_font(72))
    draw.text((70, 225), "Daily Market Update", fill=(170, 180, 197), font=load_font(46))
    draw.rectangle((70, 300, 430, 308), fill=(39, 104, 189))
    draw.text((70, 360), "Direction â¢ Breadth â¢ Rotation â¢ Momentum", fill=(245, 247, 250), font=load_font(34))
    draw.text((70, 455), "Thanks for watching.", fill=(245, 247, 250), font=load_font(38))
    draw.text((70, 520), "Informational purposes only â¢ Not financial advice", fill=(170, 180, 197), font=load_font(24))
    scenes.append(np.asarray(image))

    # Broadcast polish: add consistent original branding to every scene.
    polished_scenes = []
    for scene_index, scene in enumerate(scenes, start=1):
        frame = Image.fromarray(scene).convert("RGB")
        frame_draw = ImageDraw.Draw(frame)

        # Top accent rail
        frame_draw.rectangle((0, 0, width, 10), fill=(39, 104, 189))

        # Small upper-right show label
        show_label = "MR. CHRISTIAN  â¢  DAILY MARKET UPDATE"
        frame_draw.text(
            (735, 24),
            show_label,
            fill=(170, 180, 197),
            font=load_font(20),
        )

        # Lower information rail
        frame_draw.rectangle((0, height - 46, width, height), fill=(7, 12, 22))
        frame_draw.text(
            (28, height - 35),
            f"{report_type.upper()}  â¢  {datetime.now().strftime('%b %d, %Y')}  â¢  PRIVATE PREVIEW",
            fill=(170, 180, 197),
            font=load_font(19),
        )

        # Scene counter gives the show a cleaner broadcast rhythm.
        frame_draw.text(
            (1110, height - 35),
            f"{scene_index:02d}/{len(scenes):02d}",
            fill=(170, 180, 197),
            font=load_font(19),
        )

        polished_scenes.append(np.asarray(frame))

    scenes = polished_scenes

    silent_path = (
        Path(tempfile.gettempdir())
        / f"market-{report_type.lower()}-{datetime.now():%Y%m%d-%H%M%S}-silent.mp4"
    )
    audio_path = silent_path.with_suffix(".mp3")
    narrated_path = silent_path.with_name(silent_path.stem.replace("-silent", "-narrated") + ".mp4")

    writer = imageio.get_writer(
        silent_path,
        fps=fps,
        codec="libx264",
        quality=6,
        macro_block_size=None,
    )

    try:
        frames_per_scene = scene_seconds * fps
        for scene in scenes:
            for _ in range(frames_per_scene):
                writer.append_data(scene)
    finally:
        writer.close()

    try:
        voice = os.environ.get("TTS_VOICE", "en-US-GuyNeural")
        edge_tts.Communicate(narration, voice=voice, rate="+2%").save_sync(str(audio_path))

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run(
            [
                ffmpeg, "-y",
                "-i", str(silent_path),
                "-i", str(audio_path),
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                str(narrated_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return narrated_path
    except Exception as exc:
        print(f"Narration unavailable; using silent video: {exc}")
        return silent_path


def upload_video(video_path, report_type, markets):
    youtube = youtube_service()
    date_text = datetime.now().strftime("%B %d, %Y")
    lines = []
    for market in markets:
        if "error" in market:
            lines.append(f"{market['name']}: data unavailable")
        else:
            sign = "+" if market["change"] >= 0 else ""
            lines.append(f"{market['name']}: {market['price']:,.2f} ({sign}{market['percent']:.2f}%)")
    body = {"snippet": {"title": f"Stock Market {report_type.title()} - {date_text}",
                        "description": f"Daily stock market {report_type.lower()} update.\n\n" + "\n".join(lines) + "\n\nFor informational purposes only. Not financial advice.",
                        "categoryId": "25"},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
    upload = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = upload.next_chunk()
    return response


HOME_TEMPLATE = '''<!doctype html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stock Market YouTube Bot</title><style>
body{background:#0b1220;color:white;font-family:Arial,sans-serif;margin:0;padding:24px}.dashboard{max-width:900px;margin:auto;background:#111827;padding:24px;border-radius:16px}h1{margin-top:0}p{color:#aab4c5}table{width:100%;border-collapse:collapse;margin-top:20px;background:#182235}th,td{padding:14px;text-align:left;border-bottom:1px solid #334155}th{background:#1f2937}tr:last-child td{border-bottom:none}.buttons{display:flex;flex-wrap:wrap;gap:12px;margin-top:22px}a.button{display:inline-block;padding:12px 16px;border-radius:10px;background:#2563eb;color:white;text-decoration:none;font-weight:bold}a.secondary{background:#374151}.notice{margin-top:20px;padding:14px;border:1px solid #334155;border-radius:10px;color:#d1d5db}
</style></head><body><div class="dashboard"><h1>Stock Market YouTube Bot</h1><p>Latest available market data</p>
<table><tr><th>Index</th><th>Price</th><th>Change</th><th>Percent</th></tr>
{% for market in markets %}{% if market.error %}<tr><td>{{ market.name }}</td><td colspan="3">{{ market.error }}</td></tr>{% else %}<tr><td>{{ market.name }}</td><td>{{ "{:,.2f}".format(market.price) }}</td><td style="color:{{ market.color }}">{{ "{:+,.2f}".format(market.change) }}</td><td style="color:{{ market.color }}">{{ "{:+.2f}%".format(market.percent) }}</td></tr>{% endif %}{% endfor %}
</table><div class="buttons"><a class="button" href="/authorize">Connect YouTube</a><a class="button secondary" href="/youtube/status">Check YouTube</a><a class="button secondary" href="/run/open">Upload Private Open Video</a><a class="button secondary" href="/run/close">Upload Private Close Video</a></div><div class="notice">Videos are uploaded as <strong>Private</strong>. The open and close routes are manual test buttons until scheduling is configured.</div></div></body></html>'''


@app.route("/")
def home():
    return render_template_string(HOME_TEMPLATE, markets=get_market_data())


@app.route("/authorize")
def authorize():
    flow = Flow.from_client_config(client_config(), scopes=YOUTUBE_SCOPE, redirect_uri=REDIRECT_URI)
    authorization_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    flow.redirect_uri = REDIRECT_URI    
    session["oauth_state"] = state
    session["code_verifier"] = flow.code_verifier
    return redirect(authorization_url)

@app.route("/oauth2callback")
def oauth2callback():
    state = session.get("oauth_state")
    if not state:
        return "OAuth state is missing. Return home and tap Connect YouTube again.", 400
    flow = Flow.from_client_config(
    client_config(), scopes=YOUTUBE_SCOPE, state=state, redirect_uri=REDIRECT_URI
    )
    flow.redirect_uri = REDIRECT_URI
    flow.code_verifier = session.get("code_verifier")
    flow.fetch_token(
    authorization_response=request.url
    )
    token = flow.credentials.refresh_token
    if not token:
        return "<h2>No refresh token was returned. Return home and reconnect.</h2>", 400
    return f'''<html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{{font-family:Arial;padding:24px;background:#0b1220;color:white}}code{{display:block;overflow-wrap:anywhere;padding:16px;background:#111827;border-radius:10px}}</style></head><body><h2>YouTube authorization succeeded</h2><p>Copy the token below. In Railway, add a variable named <strong>YOUTUBE_REFRESH_TOKEN</strong>, paste this value, and deploy.</p><code>{token}</code><p>Keep this token private. Do not post a screenshot of it.</p></body></html>'''


@app.route("/youtube/status")
def youtube_status():
    try:
        youtube = youtube_service()
        items = youtube.channels().list(part="snippet", mine=True).execute().get("items", [])
        if not items:
            return "<h2>Connected, but no YouTube channel was found.</h2>", 404
        return f"<h2>YouTube connected successfully</h2><p>Channel: {items[0]['snippet']['title']}</p><p><a href='/'>Return to dashboard</a></p>"
    except Exception as exc:
        print(f"YouTube status error: {type(exc).__name__}: {exc}", flush=True)
        return f"<h2>YouTube connection problem</h2><pre>{exc}</pre>", 500


def run_private_upload(report_type):
    cron_secret = os.environ.get("CRON_SECRET")
    if cron_secret and request.args.get("key") != cron_secret:
        return "Unauthorized", 401
    video_path = None
    try:
        markets = get_market_data()
        video_path = make_market_video(report_type, markets)
        response = upload_video(video_path, report_type, markets)
        return f"<h2>Private {report_type} video uploaded</h2><p>YouTube video ID: {response['id']}</p><p><a href='/'>Return to dashboard</a></p>"
    except Exception as exc:
        return f"<h2>Upload failed</h2><pre>{exc}</pre>", 500
    finally:
        if video_path and video_path.exists():
            video_path.unlink(missing_ok=True)


@app.route("/run/open")
def run_open():
    return run_private_upload("Open")


@app.route("/run/close")
def run_close():
    return run_private_upload("Close")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
