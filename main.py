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
You are a precise CRM data extraction assistant specializing in B2B platform lead emails (e.g., IndiaMART, Alibaba). 
Your exact goal is to extract the ACTUAL BUYER'S information and completely ignore the platform's automated text.

### EXTRACTION RULES:
1. **name**: Extract the buyer's personal name and/or company name. (Usually found right above the address block).
2. **phone**: Extract the buyer's direct phone number (often near "Click to Call"). CRITICAL: DO NOT extract platform support numbers like "096-9696-9696".
3. **email**: Extract the buyer's personal/business email. CRITICAL: DO NOT extract platform emails like "buyleads@indiamart.com".
4. **product**: Look directly under "Buylead Details:" or "Product:" to find the main item requested.
5. **description**: Combine all the product specifications (Size, Plies, Application, Color, etc.) into a clean, comma-separated string.
6. **city** & **state**: Extract from the buyer's address line.
7. **country**: Infer from the address or state.

Respond ONLY with a valid JSON object. Do not include markdown formatting or conversational text. Use empty strings ("") if a value is missing.

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

Email to process:
{text}
"""

    try:
        res = model.generate_content(prompt)
        
        # LOGGING TO RENDER
        print("\n--- RAW AI RESPONSE ---")
        print(res.text)
        print("-----------------------\n")
        
        raw_text = res.text.strip()
        
        # Bulletproof JSON extraction
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if match:
            clean_json_string = match.group(0)
            return json.loads(clean_json_string)
        else:
            print("ERROR: Could not find {} in AI response.")
            raise ValueError("No JSON found in response")

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
Email: {text}
Return JSON only without markdown formatting. Do not wrap in ```json.
"""
    try:
        res = model.generate_content(prompt)
        raw_text = res.text.strip()
        
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if match:
            extra = json.loads(match.group(0))
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
