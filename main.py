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
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")

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
# AI EXTRACTION (PRIMARY)
# =========================
def ai_extract(text):
    prompt = f"""
You are a CRM data extraction AI. Extract the buyer's details from this B2B lead email.
Ignore platform boilerplate (like "Buy Lead through IndiaMART" or "096-9696-9696").

RULES:
1. name: Extract the buyer's personal name or company name (e.g., "SASHIBHUSAN SAMANTARAY" or "Shubham Medicals").
2. phone: Extract the buyer's direct phone number.
3. email: Extract the buyer's direct email address.
4. product: The main product they want to buy (e.g., "Gelfoam").
5. description: Combine all product specs (Size, Plies, Color) into one readable string.
6. city: Extract city from the address.
7. state: Extract state from the address.
8. country: Infer the country.

You must strictly follow this JSON schema. All values must be strings. If a value is missing, use an empty string "".
Schema: {{"name": str, "phone": str, "email": str, "product": str, "description": str, "city": str, "state": str, "country": str}}

Text to process:
{text}
"""
    try:
        # Force Gemini to return perfect JSON natively
        res = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json"
            )
        )
        
        print("\n--- AI JSON RESPONSE ---")
        print(res.text)
        print("------------------------\n")
        
        return json.loads(res.text)

    except Exception as e:
        print(f"AI EXTRACTION ERROR: {str(e)}")
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
# SECOND AI (FILL MISSING)
# =========================
def ai_fill_missing(text, data):
    missing_fields = [k for k, v in data.items() if not v]
    if not missing_fields:
        return data

    prompt = f"""
Fill ONLY missing fields: {missing_fields}
Existing data: {json.dumps(data)}

Text: {text}

You must respond strictly in JSON format using the missing fields as keys.
"""
    try:
        res = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json"
            )
        )
        extra = json.loads(res.text)
        for k in missing_fields:
            if extra.get(k):
                data[k] = extra[k]
    except Exception as e:
        print(f"SECOND AI ERROR: {str(e)}")

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
    text = clean_html(request.text)

    ai_data = ai_extract(text)
    regex_data = regex_extract(text)
    data = merge(ai_data, regex_data)
    data = validate(data)
    data = ai_fill_missing(text, data)
    data = validate(data)

    print("FINAL DATA:", data)

    # ==========================================
    # GUARD: PREVENT BLANK LEADS
    # ==========================================
    if not data.get("phone") and not data.get("email") and not data.get("name") and not data.get("product"):
        print("SKIPPED: No actionable data found. Odoo lead not created.")
        data["odoo_error"] = "Skipped: Payload empty or no contact/product info extracted."
        return data

    lead_id, error = create_odoo_lead(data)
    data["odoo_lead_id"] = lead_id
    if error:
        data["odoo_error"] = error

    return data
