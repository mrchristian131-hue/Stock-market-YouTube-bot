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
        history = yf.Ticker(symbol).history(period="5d")

        if len(history) < 2:
            results.append({
                "name": name,
                "error": "Market data unavailable"
            })
            continue

        latest_close = float(history["Close"].iloc[-1])
        previous_close = float(history["Close"].iloc[-2])
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
        if "error" in market:
            rows += f"<tr><td>{market['name']}</td><td colspan='3'>{market['error']}</td></tr>"
        else:
            rows += f"""
            <tr>
                <td>{market['name']}</td>
                <td>{market['price']:,.2f}</td>
                <td>{market['change']:+,.2f}</td>
                <td>{market['percent']:+.2f}%</td>
            </tr>
            """

    return f"""
    <html>
        <head>
            <title>Stock Market YouTube Bot</title>
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