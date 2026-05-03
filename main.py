from fastapi import FastAPI
from pydantic import BaseModel
import re
import os
import json
import xmlrpc.client
import google.generativeai as genai

app = FastAPI()

# =========================
# REQUEST MODEL
# =========================
class EmailRequest(BaseModel):
    text: str

# =========================
# HOME ROUTE
# =========================
@app.get("/")
def home():
    return {"message": "FastAPI server running ✅"}

# =========================
# GEMINI CONFIG
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
# AI EXTRACTION
# =========================
def ai_extract(text):
    if not model:
        return {}

    prompt = f"""
Extract buyer details from this text.

Return ONLY JSON:
{{
  "name": "",
  "phone": "",
  "email": "",
  "product": "",
  "country": ""
}}

Text:
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
# ODOO CREATE LEAD
# =========================
def create_odoo_lead(data):
    try:
        common = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/common",
            allow_none=True
        )

        models = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/object",
            allow_none=True
        )

        uid = common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD, {})

        if not uid:
            return None, "Auth failed"

        lead_vals = {
            'name': data.get('product') or "New Lead",
            'contact_name': data.get('name') or "",
            'phone': data.get('phone') or "",
            'email_from': data.get('email') or "",
        }

        lead_id = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'crm.lead', 'create',
            [lead_vals]
        )

        return lead_id, None

    except Exception as e:
        return None, str(e)

# =========================
# MAIN API
# =========================
@app.post("/extract")
def extract(request: EmailRequest):
    text = request.text

    result = ai_extract(text)
    regex_data = regex_extract(text)

    if not result.get("phone"):
        result["phone"] = regex_data["phone"]

    if not result.get("email"):
        result["email"] = regex_data["email"]

    lead_id, error = create_odoo_lead(result)

    result["odoo_lead_id"] = lead_id
    if error:
        result["odoo_error"] = error

    return result
