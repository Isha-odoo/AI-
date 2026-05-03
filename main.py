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
model = genai.GenerativeModel("gemini-pro")

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
Extract CRM lead info.

STRICT:
- Ignore platform/system numbers
- Only buyer info
- Detect any country/state globally

Return JSON:
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
        res = model.generate_content(prompt)
        raw = res.text.strip().replace("```json", "").replace("```", "")
        return json.loads(raw)
    except:
        return {}

# =========================
# FIELD VALIDATION
# =========================
def validate(data):

    # phone cleanup
    if data.get("phone"):
        data["phone"] = re.sub(r'[^\d+]', '', data["phone"])

    # email check
    if data.get("email") and "@" not in data["email"]:
        data["email"] = ""

    # remove junk names
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

Existing data:
{json.dumps(data)}

Email:
{text}

Return JSON only.
"""

    try:
        res = model.generate_content(prompt)
        raw = res.text.strip().replace("```json", "").replace("```", "")
        extra = json.loads(raw)

        for k in missing_fields:
            if extra.get(k):
                data[k] = extra[k]

    except:
        pass

    return data

# =========================
# MERGE LOGIC (SMART)
# =========================
def merge(ai_data, regex_data):

    # phone
    if not ai_data.get("phone") and regex_data["phone"]:
        ai_data["phone"] = regex_data["phone"][0]

    # email
    if not ai_data.get("email") and regex_data["email"]:
        ai_data["email"] = regex_data["email"][0]

    return ai_data

# =========================
# CREATE ODOO LEAD
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

    text = clean_html(request.text)

    # 1. AI
    ai_data = ai_extract(text)

    # 2. Regex
    regex_data = regex_extract(text)

    # 3. Merge
    data = merge(ai_data, regex_data)

    # 4. Validate
    data = validate(data)

    # 5. Fill missing
    data = ai_fill_missing(text, data)

    # 6. Final validation
    data = validate(data)

    print("FINAL DATA:", data)

    # 7. Odoo
    lead_id, error = create_odoo_lead(data)

    data["odoo_lead_id"] = lead_id
    if error:
        data["odoo_error"] = error

    return data
