import requests
import re

def analyze_news(news, model):
    text = "\n".join(news)

    prompt = f"""
你是外汇交易AI，根据以下新闻判断美元强弱：

{text}

只输出：
USD: 利多
或
USD: 利空
或
USD: 中性
"""

    r = requests.post(
        "http://localhost:11434/api/generate",
        json={"model": model, "prompt": prompt, "stream": False}
    )

    result = r.json()["response"]

    match = re.search(r"USD[:：]\\s*(利多|利空|中性)", result)

    if match:
        return match.group(0)
    else:
        return "USD: 中性"