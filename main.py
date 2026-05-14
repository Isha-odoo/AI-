from fastapi import FastAPI
from pydantic import BaseModel
import re
import os
import json
import xmlrpc.client
import time
from google import genai
from google.genai import types

app = FastAPI()

# =========================
# SCHEMAS
# =========================
class EmailRequest(BaseModel):
    text: str

class LeadSchema(BaseModel):
    name: str
    company_name: str  # Added for Company Name
    phone: str
    email: str
    product: str
    description: str
    address: str
    city: str
    state: str
    pincode: str
    country: str
    source: str  # Added for Tags/Source

# =========================
# INIT NEW GEMINI SDK
# =========================
client = genai.Client()

# =========================
# ODOO CONFIG
# =========================
ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USERNAME = os.getenv("ODOO_USERNAME")
ODOO_PASSWORD = os.getenv("ODOO_API_KEY")

# =========================
# RENDER HEALTH CHECK
# =========================
@app.api_route("/", methods=["GET", "HEAD"])
def health_check():
    return {"status": "Live", "message": "Lead Automation API is running."}

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
# AI EXTRACTION (WITH RETRIES)
# =========================
def ai_extract(text):
    prompt = """
You are a precise CRM data extraction assistant. You process leads from B2B platforms (IndiaMART, Alibaba) AND standard website contact forms. 
Extract the ACTUAL BUYER'S information. Completely ignore platform support numbers and system emails.

RULES:
1. name: Extract the buyer's personal contact name. Look for labels like "Your Name", "Sender", or the sign-off at the end of the message (e.g., "Weston Turner").
2. company_name: Extract the buyer's company name. Look for labels like "Your Company", "Company Name", or text like "I represent [Company]".
3. phone: Extract the buyer's direct phone number. Look for labels like "Phone No", "Mobile", etc.
4. email: Extract the buyer's personal/business email. Look for "Your Email".
5. product: The main item requested. If not explicitly stated, infer from the message. If it's a general inquiry, write "General Inquiry".
6. description: Combine all product specifications or the core message into a string.
7. address: Extract the street address or local area.
8. city: Extract the city.
9. state: Extract the state.
10. pincode: Extract the postal code / zip code.
11. country: Output ONLY the official 2-letter ISO country code (e.g., "US" for United States, "IN" for India).
12. source: Identify the source of the lead based on keywords. Choose ONLY ONE: "IndiaMART", "Alibaba", "Website", "Medical Expo". If the email mentions "contact form on" or "Inquiry form website", output "Website".

If a value is missing or not provided, return an empty string "".
"""
    max_retries = 3
    
    for attempt in range(max_retries):
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
            
            print(f"\n--- AI SUCCESS (Attempt {attempt + 1}) ---")
            print(response.text)
            print("------------------\n")
            
            return json.loads(response.text)

        except Exception as e:
            error_msg = str(e)
            print(f"\n--- AI ERROR (Attempt {attempt + 1}) ---\n{error_msg}\n---------------------------\n")
            
            # If the server is busy (503) or rate limited (429), wait and try again
            if "503" in error_msg or "429" in error_msg:
                if attempt < max_retries - 1:
                    sleep_time = 2 ** attempt  # Waits 1 sec, then 2 secs
                    print(f"Waiting {sleep_time} seconds before retrying...")
                    time.sleep(sleep_time)
                    continue
            
            # If it's a different error or we ran out of retries, fail safely
            return {
                "name": "", "company_name": "", "phone": "", "email": "", "product": "", 
                "description": "", "address": "", "city": "", "state": "", "pincode": "", "country": "", "source": ""
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

        # Build the full address for the description
        desc_text = data.get('description') or ""
        address_parts = [
            data.get('address'), 
            data.get('city'), 
            data.get('state'), 
            data.get('pincode'), 
            data.get('country')
        ]
        # Filter out empty strings so it formats cleanly
        valid_address_parts = [p for p in address_parts if p]
        
        if valid_address_parts:
            desc_text += f"\n\nFull Address: {', '.join(valid_address_parts)}"

        # Formatting the Lead Title with the Source Tag
        source_val = data.get('source')
        source_prefix = f"[{source_val}] " if source_val and source_val != "Other" else ""
        product_name = data.get('product') or "New Platform Lead"

        lead_vals = {
            'name': f"{source_prefix}{product_name}",  # e.g., "[IndiaMART] Gelfoam Sponge"
            'partner_name': data.get('company_name') or "", # Odoo's native field for Company Name
            'contact_name': data.get('name') or "",
            'phone': data.get('phone') or "",
            'email_from': data.get('email') or "",
            'street': data.get('address') or "",  
            'city': data.get('city') or "",       
            'zip': data.get('pincode') or "",     
            'description': desc_text.strip(),
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
    has_contact = bool(data.get("phone") or data.get("email"))
    has_context = bool(data.get("name") or data.get("company_name") or data.get("product"))

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
