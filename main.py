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
# HOME
# =========================
@app.get("/")
def home():
    return {"message": "FastAPI running ✅"}

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
# EXTRACT NAME
# =========================
def extract_name(text):
    match = re.search(r'\n([A-Z\s]{5,})\n', text)
    return match.group(1).strip() if match else ""

# =========================
# EXTRACT PHONE
# =========================
def extract_phone(text):
    match = re.search(r'Click to Call:\s*(\+?\d+)', text)
    return match.group(1) if match else ""

# =========================
# EXTRACT EMAIL
# =========================
def extract_email(text):
    match = re.search(r'E-mail:\s*([^\s]+)', text)
    return match.group(1) if match else ""

# =========================
# EXTRACT PRODUCT
# =========================
def extract_product(text):
    match = re.search(r'Buylead Details:\s*\n\s*(.+)', text)
    return match.group(1).strip() if match else ""

# =========================
# EXTRACT DESCRIPTION (ONLY SPECS)
# =========================
def extract_description(text):
    match = re.search(r'Buylead Details:(.*?)Reply To This Message', text, re.S)
    
    if not match:
        return ""

    block = match.group(1)

    # remove product line
    lines = [l.strip() for l in block.split('\n') if l.strip()]
    
    if len(lines) < 2:
        return ""

    lines = lines[1:]  # remove product name

    desc = ""
    for i in range(0, len(lines)-1, 2):
        desc += f"{lines[i]}: {lines[i+1]}\n"

    return desc.strip()

# =========================
# LOCATION EXTRACTION
# =========================
def extract_location(text):
    match = re.search(r'([A-Za-z\s]+)-\s*(\d{6}),\s*([A-Z]{2})', text)

    city = ""
    pincode = ""
    state_code = ""

    if match:
        city = match.group(1).strip()
        pincode = match.group(2)
        state_code = match.group(3)

    state_map = {
        "AN": "Andaman and Nicobar Islands",
        "MH": "Maharashtra",
        "GJ": "Gujarat",
        "DL": "Delhi"
    }

    state = state_map.get(state_code, "")

    return {
        "city": city,
        "pincode": pincode,
        "state": state,
        "country": "India"
    }

# =========================
# CREATE ODOO LEAD
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
    raw_text = request.text

    # STEP 1: Clean
    text = clean_html(raw_text)

    # STEP 2: Extract
    name = extract_name(text)
    phone = extract_phone(text)
    email = extract_email(text)
    product = extract_product(text)
    description = extract_description(text)
    location = extract_location(text)

    result = {
        "name": name,
        "phone": phone,
        "email": email,
        "product": product,
        "description": description,
        "city": location["city"],
        "country": location["country"]
    }

    print("FINAL DATA:", result)

    # STEP 3: Odoo
    lead_id, error = create_odoo_lead(result)

    result["odoo_lead_id"] = lead_id

    if error:
        result["odoo_error"] = error

    return result
