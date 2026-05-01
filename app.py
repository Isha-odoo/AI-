from flask import Flask, request, jsonify
import re
import os
import json
import xmlrpc.client
import google.generativeai as genai

app = Flask(__name__)

# =========================
# HOME ROUTE
# =========================
@app.route("/")
def home():
    return "Server is running ✅"

# =========================
# SAFE GEMINI CONFIG
# =========================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-pro")
        print("✅ Gemini configured")
    except Exception as e:
        print("❌ Gemini error:", e)
        model = None
else:
    print("❌ GEMINI_API_KEY missing")
    model = None

# =========================
# ODOO CONFIG
# =========================
ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USERNAME = os.getenv("ODOO_USERNAME")
ODOO_PASSWORD = os.getenv("ODOO_API_KEY")

if not all([ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD]):
    print("❌ Odoo credentials missing")

# =========================
# REGEX FALLBACK
# =========================
def regex_extract(text):
    phone = re.findall(r'\+?\d{10,13}', text)
    email = re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text)

    return {
        "phone": phone[0] if phone else "",
        "email": email[0] if email else ""
    }

# =========================
# AI EXTRACTION (SAFE)
# =========================
def ai_extract(text):
    if not model:
        return {}

    prompt = f"""
    Extract buyer details from this email.

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
        raw = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(raw)
    except Exception as e:
        print("❌ AI error:", e)
        return {}

# =========================
# ODOO CREATE LEAD (SAFE)
# =========================
def create_odoo_lead(data):
    if not all([ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD]):
        return "Odoo not configured"

    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD, {})

        if not uid:
            return "Authentication failed"

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

        lead_vals = {
            'name': data.get('product') or "New Lead",
            'contact_name': data.get('name'),
            'phone': data.get('phone'),
            'email_from': data.get('email'),
        }

        # Country mapping
        if data.get("country"):
            country_ids = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'res.country', 'search',
                [[('name', 'ilike', data.get("country"))]],
                {'limit': 1}
            )
            if country_ids:
                lead_vals['country_id'] = country_ids[0]

        lead_id = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'crm.lead', 'create',
            [lead_vals]
        )

        return lead_id

    except Exception as e:
        return str(e)   # ✅ Properly inside function
# =========================
# MAIN API
# =========================
@app.route("/extract", methods=["POST"])
def extract():
    try:
        data = request.json
        text = data.get("text", "")

        # Step 1: AI
        result = ai_extract(text)

        # Step 2: Regex fallback
        regex_data = regex_extract(text)

        if not result.get("phone"):
            result["phone"] = regex_data["phone"]

        if not result.get("email"):
            result["email"] = regex_data["email"]

        # Step 3: Create lead
        lead_id = create_odoo_lead(result)

        result["odoo_lead_id"] = lead_id

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)})

# =========================
# RUN APP (RENDER FIX)
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
