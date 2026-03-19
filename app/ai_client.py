import json
from openai import OpenAI
import app.config as config

def summarize_text(text):
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    prompt = (
        "Read the text below and return valid JSON only with these keys: "
        "title, summary, tags (list of 3-5 lowercase strings), primary_topic.\n\n"
        f"{text[:3000]}"
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content

    try:
        result = json.loads(raw)
        return {
            "title": result.get("title", ""),
            "summary": result.get("summary", ""),
            "tags": result.get("tags", []),
            "primary_topic": result.get("primary_topic", ""),
        }
    except json.JSONDecodeError:
        print("Error: Could not parse the AI response as JSON.")
        print("Raw response was:", raw)
        return None
