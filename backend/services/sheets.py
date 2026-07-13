import httpx
import logging
import os
from dotenv import load_dotenv

from backend.services.net import is_safe_outbound_url

load_dotenv()
logger = logging.getLogger(__name__)

GOOGLE_SHEETS_WEBHOOK_URL = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL")

async def log_booking_to_sheets(
    action: str, 
    name: str, 
    phone: str, 
    date: str, 
    time: str, 
    doctor: str, 
    appointment_id: str = "N/A", 
    status: str = "confirmed", 
    notes: str = "N/A", 
    webhook_url: str = None
) -> bool:
    """
    Sends booking/reschedule/cancel details to a Google Apps Script Webhook.
    Prioritizes the passed `webhook_url` (from Tenant) over the global .env URL.
    Supports the 10-column layout:
    1. Appointment_ID
    2. Timestamp (generated in script)
    3. Action
    4. Name
    5. Phone
    6. Date
    7. Time
    8. Doctor
    9. Status
    10. Notes
    """
    final_url = webhook_url or GOOGLE_SHEETS_WEBHOOK_URL
    if not final_url:
        logger.warning("No GOOGLE_SHEETS_WEBHOOK_URL provided. Skipping Google Sheets integration.")
        return False
    if not is_safe_outbound_url(final_url):
        logger.warning("Refusing to POST to unsafe/internal Sheets webhook URL: %s", final_url)
        return False

    payload = {
        # Lowercase keys
        "appointment_id": appointment_id,
        "action": action,
        "name": name,
        "phone": phone,
        "date": date,
        "time": time,
        "doctor": doctor,
        "status": status,
        "notes": notes,

        # PascalCase keys (matching Google Sheet column headers)
        "Appointment_ID": appointment_id,
        "Action": action,
        "Name": name,
        "Phone": phone,
        "Date": date,
        "Time": time,
        "Doctor": doctor,
        "Status": status,
        "Notes": notes
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(final_url, json=payload, follow_redirects=False)
            response.raise_for_status()
            logger.info("Successfully logged booking to Google Sheets.")
            return True
    except Exception as e:
        logger.error(f"Failed to log booking to Google Sheets: {e}")
        return False
