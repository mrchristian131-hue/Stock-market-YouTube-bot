from flask import Flask
from datetime import datetime

app = Flask(__name__)

@app.route("/")
def home():
    return f"""
    <h1>Stock Market YouTube Bot</h1>
    <p>Bot is running successfully!</p>
    <p>Current server time: {datetime.now()}</p>
    """

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
