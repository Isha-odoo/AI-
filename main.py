from fastapi import FastAPI
from pydantic import BaseModel
import re
import os
import json
import xmlrpc.client
from google import genai
from google.genai import types

app = FastAPI()

class EmailRequest(BaseModel):
    text: str

# Define the exact structure we want Gemini to output
class LeadSchema(BaseModel):
    name: str
    phone: str
    email: str
    product: str
    description: str
    city: str
    state: str
    country: str

# =========================
# INIT NEW GEMINI SDK
# =========================
# It automatically looks for the GEMINI_API_KEY environment variable
client = genai.Client()

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
# STRONG REGEX EXTRACTION
# =========================
def regex_extract(text):
    return {
        "phone": re.findall(r'\+?\d[\d\s\-]{8,15}', text),
        "email": re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text),
        "pincode": re.findall(r'\b\d{5,6}\b', text)
    }

# =========================
# AI EXTRACTION (NEW SDK)
# =========================
def ai_extract(text):
    prompt = """
You are a precise CRM data extraction assistant specializing in B2B platform lead emails (e.g., IndiaMART). 
Extract the ACTUAL BUYER'S information. Completely ignore platform support numbers (like 096-9696-9696) and platform emails (like buyleads@indiamart.com).

RULES:
1. name: Extract the buyer's personal name and/or company name.
2. phone: Extract the buyer's direct phone number.
3. email: Extract the buyer's personal/business email.
4. product: The main item requested (e.g., under "Buylead Details" or "Product").
5. description: Combine all product specifications into a comma-separated string.
6. city, state, country: Extract from the address.

If a value is missing, return an empty string "".
"""
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt + f"\n\nEMAIL TO PROCESS:\n{text}",
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=LeadSchema,
                temperature=0.1
            ),
        )
        
        print("\n--- AI SUCCESS ---")
        print(response.text)
        print("------------------\n")
        
        return json.loads(response.text)

    except Exception as e:
        print(f"\n--- AI EXTRACTION ERROR ---\n{str(e)}\n---------------------------\n")
        return {
            "name": "", "phone": "", "email": "", "product": "", 
            "description": "", "city": "", "state": "", "country": ""
        }

# =========================
# FIELD VALIDATION
# =========================
def validate(data):
    if data.get("phone"):
        data["phone"] = re.sub(r'[^\d+]', '', data["phone"])
    if data.get("email") and "@" not in data["email"]:
        data["email"] = ""
    if data.get("name") and len(data["name"]) < 3:
        data["name"] = ""
    return data

# =========================
# MERGE LOGIC (SMART)
# =========================
def merge(ai_data, regex_data):
    for key in ["phone", "email"]:
        if key not in ai_data:
            ai_data[key] = ""

    if not ai_data.get("phone") and regex_data.get("phone"):
        valid_phones = [p for p in regex_data["phone"] if "96969696" not in p.replace("-", "")]
        if valid_phones:
            ai_data["phone"] = valid_phones[0]
        else:
            ai_data["phone"] = regex_data["phone"][0]

    if not ai_data.get("email") and regex_data.get("email"):
        valid_emails = [e for e in regex_data["email"] if "indiamart.com" not in e.lower()]
        if valid_emails:
            ai_data["email"] = valid_emails[0]
        else:
            ai_data["email"] = regex_data["email"][0]

    return ai_data

# =========================
# CREATE ODOO LEAD
# =========================
def create_odoo_lead(data):
    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
        uid = common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD, {})

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)

        lead_vals = {
            'name': data.get('product') or "New Platform Lead",
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
    text = clean_html(request.text)

    # 1. Extract via AI and Regex
    ai_data = ai_extract(text)
    regex_data = regex_extract(text)
    
    # 2. Merge & Validate
    data = merge(ai_data, regex_data)
    data = validate(data)

    print("FINAL DATA:", data)

    # ==========================================
    # STRICT GUARD: PREVENT BLANK / GHOST LEADS
    # ==========================================
    # Must have contact info AND context (Name or Product)
    has_contact = bool(data.get("phone") or data.get("email"))
    has_context = bool(data.get("name") or data.get("product"))

    if not (has_contact and has_context):
        print("SKIPPED: Not a valid lead. Dropping payload to protect Odoo.")
        data["odoo_error"] = "Skipped: Incomplete lead payload."
        return data

    # 3. Push to Odoo
    lead_id, error = create_odoo_lead(data)
    data["odoo_lead_id"] = lead_id
    if error:
        data["odoo_error"] = error

    return data
