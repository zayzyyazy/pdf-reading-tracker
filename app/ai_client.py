import json
from openai import OpenAI
import app.config as config

def summarize_text(text):
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    category_options = [
        "university", "ai", "society", "politics",
        "software", "philosophy", "personal", "other"
    ]
    document_type_options = [
        "article", "essay", "lecture-notes", "tutorial",
        "research-paper", "other"
    ]

    prompt = (
        "Read the text below and return valid JSON only with these keys:\n"
        "- title: short title of the document\n"
        "- summary: 1-2 short sentences, max ~220 characters\n"
        "- tags: list of exactly 2 or 3 simple lowercase tags\n"
        f"- category: exactly one value chosen ONLY from this list: {category_options}\n"
        f"- document_type: exactly one value chosen ONLY from this list: {document_type_options}\n\n"
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

        tags = result.get("tags", [])
        if not isinstance(tags, list) or len(tags) == 0:
            tags = ["general"]

        category = result.get("category", "")
        if not category:
            category = "other"

        document_type = result.get("document_type", "")
        if not document_type:
            document_type = "other"

        return {
            "title": result.get("title", ""),
            "summary": result.get("summary", ""),
            "tags": tags,
            "category": category,
            "document_type": document_type,
        }
    except json.JSONDecodeError:
        print("Error: Could not parse the AI response as JSON.")
        print("Raw response was:", raw)
        return None
