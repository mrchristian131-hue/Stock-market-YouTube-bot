from flask import Flask
import yfinance as yf

app = Flask(__name__)

MARKETS = {
    "S&P 500": "^GSPC",
    "Dow Jones": "^DJI",
    "Nasdaq": "^IXIC",
}


def get_market_data():
    results = []

    for name, symbol in MARKETS.items():
        history = yf.download(
            symbol,
            period="5d",
            interval="1d",
            progress=False,
            auto_adjust=False,
            multi_level_index=False,
        )

        if len(history) < 2:
            results.append({
                "name": name,
                "error": "Market data unavailable"
            })
            continue

        close_prices = history["Close"].squeeze()

        latest_close = float(close_prices.iloc[-1])
        previous_close = float(close_prices.iloc[-2])
        change = latest_close - previous_close
        percent_change = (change / previous_close) * 100

        results.append({
            "name": name,
            "price": latest_close,
            "change": change,
            "percent": percent_change,
        })

    return results


@app.route("/")
def home():
    markets = get_market_data()

    rows = ""

    for market in markets:
        color = "green" if market.get("change", 0) >= 0 else "red"
        
        if "error" in market:
            rows += f"<tr><td>{market['name']}</td><td colspan='3'>{market['error']}</td></tr>"
        else:
            rows += f"""
            <tr>
                <td>{market['name']}</td>
                <td>{market['price']:,.2f}</td>
                <td style="color:{color}">{market['change']:+,.2f}</td>
                <td style="color:{color}">{market['percent']:+.2f}%</td>
                
            </tr>
            """

    return f"""
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Stock Market YouTube Bot</title>

    <style>
        body {{
            background: #0b1220;
            color: white;
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 24px;
        }}

        .dashboard {{
            max-width: 800px;
            margin: auto;
            background: #111827;
            padding: 24px;
            border-radius: 16px;
        }}

        h1 {{
            margin-top: 0;
        }}

        p {{
            color: #aab4c5;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            background: #182235;
        }}

        th, td {{
            padding: 14px;
            text-align: left;
            border-bottom: 1px solid #334155;
        }}

        th {{
            background: #1f2937;
        }}

        tr:last-child td {{
            border-bottom: none;
        }}
    </style>
</head>
            
        
        <body>
            <h1>Stock Market YouTube Bot</h1>
            <p>Latest available market data</p>

            <table border="1" cellpadding="8">
                <tr>
                    <th>Index</th>
                    <th>Price</th>
                    <th>Change</th>
                    <th>Percent</th>
                </tr>
                {rows}
            </table>
        </body>
    </html>
    """


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
