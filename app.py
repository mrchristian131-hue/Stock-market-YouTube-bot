rimport os
import tempfile
from datetime import datetime
from pathlib import Path

import imageio.v2 as imageio
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
    width, height, fps, seconds = 1280, 720, 24, 6
    image = Image.new("RGB", (width, height), (11, 18, 32))
    draw = ImageDraw.Draw(image)
    draw.text(
    (70, 55),
    "Mr. Christian - Daily Market Update",
    fill=(245, 247, 250),
    font=load_font(64)
)

draw.text(
    (70, 140),
    f"Stock Market {report_type.title()} - {datetime.now().strftime('%A, %B %d, %Y')}",
    fill=(170, 180, 197),
    font=load_font(34)
)
y = 250
for market in markets:
    if "error" in market:
            draw.text((80, y), f"{market['name']}: data unavailable", fill=(170, 180, 197), font=load_font(36))
    else:
            value_color = (55, 163, 27) if market["change"] >= 0 else (255, 51, 43)
            sign = "+" if market["change"] >= 0 else ""
            draw.text((80, y), market["name"], fill=(245, 247, 250), font=load_font(36))
            draw.text((460, y), f"{market['price']:,.2f}", fill=(245, 247, 250), font=load_font(36))
            draw.text((760, y), f"{sign}{market['change']:,.2f}", fill=value_color, font=load_font(36))
            draw.text((990, y), f"{sign}{market['percent']:.2f}%", fill=value_color, font=load_font(36))
    y += 105
draw.text((70, 665), "Market data for informational purposes only.", fill=(170, 180, 197), font=load_font(26))
frame = np.asarray(image)
summary_image = Image.new("RGB", (width, height), (11, 18, 32))
summary_draw = ImageDraw.Draw(summary_image)

summary_draw.text(
    (70, 55),
    "Market Snapshot",
    fill=(245, 247, 250),
    font=load_font(64)
)

summary_draw.text(
    (70, 150),
    "Major Index Performance",
    fill=(170, 180, 197),
    font=load_font(34)
)

y2 = 260
for market in markets:
    if "error" not in market:
        sign = "+" if market["change"] >= 0 else ""
        summary_draw.text(
            (80, y2),
            f"{market['name']}: {sign}{market['percent']:.2f}%",
            fill=(245, 247, 250),
            font=load_font(40)
        )
        y2 += 90

summary_frame = np.asarray(summary_image)
output_path = Path(tempfile.gettempdir()) / f"market-{report_type.lower()}-{datetime.now():%Y%m%d-%H%M%S}.mp4"
writer = imageio.get_writer(output_path, fps=fps, codec="libx264", quality=8, macro_block_size=None)
try:
        for i in range(seconds * fps):
            if i < (seconds * fps) // 2:
                writer.append_data(frame)
            else:
                writer.append_data(summary_frame)
       
finally:
        writer.close()
return output_path


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

