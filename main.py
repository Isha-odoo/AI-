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
# REGEX EXTRACTION
# =========================
def regex_extract(text):
    phone = re.findall(r'Click to Call:\s*(\+?\d+)', text)
    email = re.findall(r'E-mail:\s*([^\s]+@[^\s]+)', text)

    return {
        "phone": phone[0] if phone else "",
        "email": email[0] if email else ""
    }

# =========================
# PRODUCT NAME
# =========================
def extract_product_name(text):
    match = re.search(r'Buylead Details:\s*\n\s*(.+)', text)
    return match.group(1).strip() if match else ""

# =========================
# PRODUCT DETAILS (CLEAN)
# =========================
def extract_product_details(text):
    try:
        match = re.search(
            r'Buylead Details:\s*(.*?)\s*Reply To This Message',
            text,
            re.DOTALL
        )

        if not match:
            return ""

        block = match.group(1)

        lines = [line.strip() for line in block.split("\n") if line.strip()]

        # Remove product name
        if lines:
            lines = lines[1:]

        cleaned = []
        i = 0
        while i < len(lines) - 1:
            key = lines[i].replace(":", "").strip()
            value = lines[i+1].strip()
            cleaned.append(f"{key}: {value}")
            i += 2

        return "\n".join(cleaned)

    except Exception as e:
        print("❌ Product parsing error:", e)
        return ""

# =========================
# LOCATION EXTRACTION
# =========================
def extract_location(text):
    try:
        address_match = re.search(r'Port Blair.*', text)

        if address_match:
            address = address_match.group(0)

            city_match = re.search(r'([A-Za-z\s]+)-\d{6}', address)
            city = city_match.group(1).strip() if city_match else ""

            return city, "India"

        return "", ""

    except:
        return "", ""

# =========================
# AI EXTRACTION (NAME ONLY)
# =========================
def ai_extract(text):
    if not model:
        return {}

    prompt = f"""
Extract ONLY the buyer name from this text.

Ignore platform names and emails.

Return JSON:
{{
  "name": ""
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
            return {}

    except:
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
            return None, "Authentication failed"

        lead_vals = {
            'name': f"{data.get('product', 'Inquiry')} - {data.get('name', '')}",
            'contact_name': data.get('name') or "",
            'phone': data.get('phone') or "",
            'email_from': data.get('email') or "",
            'description': data.get('description') or "",
            'city': data.get('city') or "",
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

        return lead_id, None

    except Exception as e:
        return None, str(e)

# =========================
# MAIN API
# =========================
@app.post("/extract")
def extract(request: EmailRequest):
    text = request.text

    # Step 1: AI (name only)
    result = ai_extract(text)

    # Step 2: Regex
    regex_data = regex_extract(text)

    result["phone"] = regex_data.get("phone")
    result["email"] = regex_data.get("email")

    # Step 3: Structured extraction
    result["product"] = extract_product_name(text)
    result["description"] = extract_product_details(text)

    city, country = extract_location(text)
    result["city"] = city
    result["country"] = country

    # Step 4: Create lead
    lead_id, error = create_odoo_lead(result)

    return {
        "status": "success" if lead_id else "error",
        "data": result,
        "odoo_lead_id": lead_id,
        "error": error
    }
