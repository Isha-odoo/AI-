from flask import Flask, request, jsonify
import re
import os
import google.generativeai as genai
import json

app = Flask(__name__)

# Configure Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-pro")

def regex_extract(text):
    phone = re.findall(r'\+?\d{10,13}', text)
    email = re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text)

    return {
        "phone": phone[0] if phone else "",
        "email": email[0] if email else ""
    }

@app.route("/extract", methods=["POST"])
def extract():
    data = request.json
    text = data.get("text", "")

    prompt = f"""
    Extract buyer details from the email.

    Ignore:
    - Platform emails (IndiaMART, Alibaba, Expo)
    - Footer, unsubscribe, ads
    - Helpline numbers

    Extract:
    - name
    - phone
    - email (customer email only)
    - product
    - country

    Return ONLY JSON:
    {{
      "name": "",
      "phone": "",
      "email": "",
      "product": "",
      "country": ""
    }}

    Email:
    {text}
    """

    try:
        response = model.generate_content(prompt)
        result = json.loads(response.text)
    except:
        result = {}

    # 🔥 Regex fallback
    regex_data = regex_extract(text)

    if not result.get("phone"):
        result["phone"] = regex_data["phone"]

    if not result.get("email"):
        result["email"] = regex_data["email"]

    return jsonify(result)

if __name__ == "__main__":
    app.run()
