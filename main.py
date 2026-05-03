from fastapi import FastAPI
from pydantic import BaseModel
import re
import os
import json
import xmlrpc.client
import google.generativeai as genai

app = FastAPI()

class EmailRequest(BaseModel):
    text: str

# =========================
# GEMINI CONFIG
# =========================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-pro")
else:
    model = None

# =========================
# ODOO CONFIG
# =========================
ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USERNAME = os.getenv("ODOO_USERNAME")
ODOO_PASSWORD = os.getenv("ODOO_API_KEY")

# =========================
# CLEAN HTML
# =========================
def clean_html(raw_html):
    text = re.sub('<.*?>', '\n', raw_html)
    text = re.sub(r'\n+', '\n', text)
    return text.strip()

# =========================
# AI EXTRACTION (SMART)
# =========================
def ai_extract(text):
    if not model:
        return {}

    prompt = f"""
You are an expert at extracting CRM lead data from emails.

STRICT RULES:
- Ignore platform emails (IndiaMART, Alibaba, Expo systems)
- Ignore helpline numbers
- Extract ONLY buyer/customer details
- Understand ANY format (not fixed template)

Extract:
- name (person name)
- phone
- email
- product (what buyer wants)
- description (only product requirements/specs)
- city
- state
- country

Return ONLY valid JSON:
{{
  "name": "",
  "phone": "",
  "email": "",
  "product": "",
  "description": "",
  "city": "",
  "state": "",
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
        print("AI ERROR:", e)
        return {}

# =========================
# REGEX FALLBACK (GENERIC)
# =========================
def regex_fallback(text, result):

    if not result.get("phone"):
        phone = re.findall(r'\+?\d[\d\s\-]{8,15}', text)
        if phone:
            result["phone"] = phone[0]

    if not result.get("email"):
        email = re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text)
        if email:
            result["email"] = email[0]

    return result

# =========================
# ODOO CREATE LEAD
# =========================
def create_odoo_lead(data):
    try:
        common = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/common", allow_none=True
        )
        uid = common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD, {})

        models = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/object", allow_none=True
        )

        lead_vals = {
            'name': data.get('product') or "New Lead",
            'contact_name': data.get('name') or "",
            'phone': data.get('phone') or "",
            'email_from': data.get('email') or "",
            'city': data.get('city') or "",
            'description': data.get('description') or "",
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

    # STEP 1: Clean
    text = clean_html(request.text)

    print("CLEAN TEXT:", text[:300])

    # STEP 2: AI extraction
    result = ai_extract(text)

    print("AI RESULT:", result)

    # STEP 3: Fallback
    result = regex_fallback(text, result)

    # STEP 4: Default country if missing
    if not result.get("country"):
        result["country"] = "India"  # you can remove if global

    # STEP 5: Create lead
    lead_id, error = create_odoo_lead(result)

    result["odoo_lead_id"] = lead_id

    if error:
        result["odoo_error"] = error

    print("FINAL RESULT:", result)

    return result
