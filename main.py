from fastapi import FastAPI
from pydantic import BaseModel
import re
import os
import xmlrpc.client

app = FastAPI()

# =========================
# REQUEST MODEL
# =========================
class EmailRequest(BaseModel):
    text: str

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
    text = re.sub(r'<.*?>', '\n', raw_html)
    text = re.sub(r'\n+', '\n', text)
    return text.strip()

# =========================
# EXTRACT CONTACT NAME
# =========================
def extract_name(text):
    match = re.search(r'\n([A-Z\s]{5,})\n', text)
    return match.group(1).strip() if match else ""

# =========================
# PHONE
# =========================
def extract_phone(text):
    match = re.search(r'Click to Call:\s*(\+?\d+)', text)
    if match:
        return match.group(1)

    match = re.search(r'\+?\d[\d\s\-]{8,15}', text)
    return match.group(0) if match else ""

# =========================
# EMAIL
# =========================
def extract_email(text):
    match = re.search(r'E-mail:\s*([^\s]+)', text)
    if match:
        return match.group(1)

    match = re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text)
    return match.group(0) if match else ""

# =========================
# PRODUCT
# =========================
def extract_product(text):
    match = re.search(r'Buylead Details:\s*\n\s*(.+)', text)
    return match.group(1).strip() if match else ""

# =========================
# 🔥 DESCRIPTION (TABLE PARSER)
# =========================
def extract_description(text):
    block = re.search(r'Buylead Details:(.*?)Reply To This Message', text, re.S)

    if not block:
        return ""

    content = block.group(1)

    pairs = re.findall(r'([A-Za-z /]+)\s*:\s*([A-Za-z0-9 x\-]+)', content)

    description = ""
    for key, value in pairs:
        description += f"{key.strip()}: {value.strip()}\n"

    return description.strip()

# =========================
# LOCATION
# =========================
def extract_location(text):
    match = re.search(r'([A-Za-z\s]+)-\s*(\d{6}),\s*([A-Z]{2})', text)

    city = ""
    state_code = ""

    if match:
        city = match.group(1).strip()
        state_code = match.group(3)

    state_map = {
        "AN": "Andaman and Nicobar Islands",
        "MH": "Maharashtra",
        "GJ": "Gujarat",
        "DL": "Delhi"
    }

    state = state_map.get(state_code, "")
    country = "India"

    return city, state, country

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

        return lead_id

    except Exception as e:
        return str(e)

# =========================
# MAIN API
# =========================
@app.post("/extract")
def extract(request: EmailRequest):

    text = clean_html(request.text)

    name = extract_name(text)
    phone = extract_phone(text)
    email = extract_email(text)
    product = extract_product(text)
    description = extract_description(text)
    city, state, country = extract_location(text)

    result = {
        "name": name,
        "phone": phone,
        "email": email,
        "product": product,
        "description": description,
        "city": city,
        "state": state,
        "country": country
    }

    print("FINAL RESULT:", result)

    lead_id = create_odoo_lead(result)
    result["odoo_lead_id"] = lead_id

    return result
