import requests

def fetch_news():
    url = "https://newsapi.org/v2/top-headlines?category=business&apiKey=64f3cf9af79844e5a24e12dba170e58e"
    r = requests.get(url)
    data = r.json()

    news = []

    for article in data.get("articles", [])[:5]:
        news.append(article["title"])

    return news