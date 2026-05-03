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
# AI EXTRACTION
# =========================
def ai_extract(text):
    if not model:
        return {}

    prompt = f"""
Extract buyer details from this text.

Rules:
- Product must be SPECIFIC (e.g., "silver ring", "gold chain")
- Do NOT return generic words like "product"
- If missing, return empty string ""

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

        try:
            return json.loads(raw)
        except:
            print("❌ JSON parse error:", raw)
            return {}

    except Exception as e:
        print("❌ AI error:", e)
        return {}

# =========================
# ODOO CREATE LEAD
# =========================
def create_odoo_lead(data):
    try:
        print("🔐 Connecting to Odoo...")

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
            return None, "Authentication failed"

        lead_vals = {
            'name': f"{data.get('product', 'Inquiry')} - {data.get('name', '')}",
            'contact_name': data.get('name') or "",
            'phone': data.get('phone') or "",
            'email_from': data.get('email') or "",
            'description': data.get('raw_text') or ""   # FULL inquiry
        }

        print("📤 Sending to Odoo:", lead_vals)

        lead_id = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'crm.lead', 'create',
            [lead_vals]
        )

        print("✅ Lead created:", lead_id)

        return lead_id, None

    except Exception as e:
        print("❌ Odoo error:", e)
        return None, str(e)

# =========================
# MAIN API
# =========================
@app.post("/extract")
def extract(request: EmailRequest):
    text = request.text

    print("📩 Incoming text:", text)

    # Step 1: AI extraction
    result = ai_extract(text)

    # Step 2: Regex fallback
    regex_data = regex_extract(text)

    if not result.get("phone") and regex_data.get("phone"):
        result["phone"] = regex_data["phone"]

    if not result.get("email") and regex_data.get("email"):
        result["email"] = regex_data["email"]

    # Step 3: Keep full inquiry
    result["raw_text"] = text

    print("🤖 Final extracted data:", result)

    # Step 4: Create Odoo lead
    lead_id, error = create_odoo_lead(result)

    # Step 5: Response
    return {
        "status": "success" if lead_id else "error",
        "data": result,
        "odoo_lead_id": lead_id,
        "error": error
    }
