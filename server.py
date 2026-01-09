from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import List
import uuid
from datetime import datetime, timezone
import asyncio
import resend


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Resend Email Configuration
RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'onboarding@resend.dev')
RECIPIENT_EMAIL = os.environ.get('RECIPIENT_EMAIL')

# Configure Resend
resend.api_key = RESEND_API_KEY

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")


# Define Models
class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")  # Ignore MongoDB's _id field
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class StatusCheckCreate(BaseModel):
    client_name: str

# Contact Form Models
class ContactFormRequest(BaseModel):
    name: str
    email: EmailStr
    company: str = ""
    message: str

# Add your routes to the router instead of directly to app
@api_router.get("/")
async def root():
    return {"message": "Hello World"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.model_dump()
    status_obj = StatusCheck(**status_dict)
    
    # Convert to dict and serialize datetime to ISO string for MongoDB
    doc = status_obj.model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    
    _ = await db.status_checks.insert_one(doc)
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    # Exclude MongoDB's _id field from the query results
    status_checks = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    
    # Convert ISO string timestamps back to datetime objects
    for check in status_checks:
        if isinstance(check['timestamp'], str):
            check['timestamp'] = datetime.fromisoformat(check['timestamp'])
    
    return status_checks

@api_router.post("/contact")
async def send_contact_email(request: ContactFormRequest):
    """
    Handle contact form submissions and send email notifications
    """
    try:
        # Create HTML email content
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                .header {{
                    background: linear-gradient(135deg, #8b5cf6 0%, #3b82f6 100%);
                    color: white;
                    padding: 30px;
                    text-align: center;
                    border-radius: 8px 8px 0 0;
                }}
                .content {{
                    background: #f9fafb;
                    padding: 30px;
                    border-radius: 0 0 8px 8px;
                }}
                .field {{
                    margin-bottom: 20px;
                }}
                .field-label {{
                    font-weight: bold;
                    color: #8b5cf6;
                    margin-bottom: 5px;
                }}
                .field-value {{
                    background: white;
                    padding: 12px;
                    border-left: 3px solid #8b5cf6;
                    border-radius: 4px;
                }}
                .footer {{
                    text-align: center;
                    margin-top: 20px;
                    color: #6b7280;
                    font-size: 14px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1 style="margin: 0;">New Contact Form Submission</h1>
                    <p style="margin: 10px 0 0 0;">INVERA Website</p>
                </div>
                <div class="content">
                    <div class="field">
                        <div class="field-label">Name:</div>
                        <div class="field-value">{request.name}</div>
                    </div>
                    <div class="field">
                        <div class="field-label">Email:</div>
                        <div class="field-value">{request.email}</div>
                    </div>
                    {f'''<div class="field">
                        <div class="field-label">Company:</div>
                        <div class="field-value">{request.company}</div>
                    </div>''' if request.company else ''}
                    <div class="field">
                        <div class="field-label">Message:</div>
                        <div class="field-value">{request.message}</div>
                    </div>
                </div>
                <div class="footer">
                    <p>This email was sent from the INVERA contact form.</p>
                    <p>Received at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Prepare email parameters
        params = {
            "from": SENDER_EMAIL,
            "to": [RECIPIENT_EMAIL],
            "subject": f"New Contact Form Submission from {request.name}",
            "html": html_content
        }
        
        # Send email using asyncio.to_thread for non-blocking operation
        email_response = await asyncio.to_thread(resend.Emails.send, params)
        
        logger.info(f"Contact form email sent successfully to {RECIPIENT_EMAIL}")
        
        # Store submission in database for record keeping
        submission = {
            "id": str(uuid.uuid4()),
            "name": request.name,
            "email": request.email,
            "company": request.company,
            "message": request.message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "email_id": email_response.get("id")
        }
        
        await db.contact_submissions.insert_one(submission)
        
        return {
            "status": "success",
            "message": "Your message has been sent successfully! We'll get back to you soon.",
            "email_id": email_response.get("id")
        }
        
    except Exception as e:
        logger.error(f"Failed to send contact form email: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send message. Please try again later or contact us directly at {RECIPIENT_EMAIL}"
        )

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()