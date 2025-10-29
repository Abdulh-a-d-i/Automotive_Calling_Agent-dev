import json
import logging
import os
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import requests
from dotenv import load_dotenv
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse,StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from rich import print
from src.api.base_models import (
    UserLogin,
    UserRegister,
    UserOut,
    LoginResponse,
    UpdateUserProfileRequest,
    Assistant_Payload
)
from src.models.System_Prompt import SystemPrompt_V1
from src.utils.db import PGDB
from src.utils.mail_management import Send_Mail
# from src.models.model import generate_summary
from src.utils.jwt_utils import create_access_token
from src.utils.utils import get_current_user # ,is_admin
from livekit import api

# Add GCS imports
from google.cloud import storage
from google.cloud.exceptions import NotFound
from google.oauth2 import service_account
import httpx

load_dotenv()

router = APIRouter()
mail_obj = Send_Mail()
db = PGDB()
load_dotenv(override=True)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BLANDAI_API_KEY = os.getenv('BLANDAI_API_KEY')
GCS_BUCKET_NAME = os.getenv("GOOGLE_BUCKET_NAME")
GCS_SERVICE_ACCOUNT_KEY = os.getenv("GCS_SERVICE_ACCOUNT_KEY")  


# error response 
def error_response(message, status_code=400):
    return JSONResponse(
        status_code=status_code,
        content={"error": message}
    )


@router.post("/register")
def register_user(user: UserRegister):
    user_dict = user.dict()
    # 🔽 Normalize both email and username
    user_dict["email"] = user_dict["email"].strip().lower()
    user_dict["username"] = user_dict["username"].strip().lower()
    user_dict['is_admin'] = True
    try:
        db.register_user(user_dict)
        return JSONResponse(status_code=201, content={"message": "You are registered successfully."})
    except ValueError as ve:
        return error_response(status_code=400, message=str(ve))
    except Exception as e:
        traceback.print_exc()
        return error_response(status_code=500, message=f"Registration failed: {str(e)}")

@router.post(
    "/login",response_model=LoginResponse,
)
def login_user(user: UserLogin):
    try:
        user_dict = {
        "email": user.email,
        "password": user.password
    }
        logging.info(f"User dict: {user_dict}")
        user_dict["email"] = user_dict["email"].strip().lower()
        result = db.login_user(user_dict)
        if not result:
            return error_response("Invalid username or password", status_code=422)
        
        
        token = create_access_token({"sub": str(result["id"])})
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": result
        }
        
    except ValueError as ve:
        # Return 401 when credentials are invalid
        return error_response(str(ve),status_code=422)

    except Exception as e:
        logging.error(f"Error during login: {str(e)}")
        return error_response(f"Internal server error: {str(e)}",status_code=500)
    


voices = {
    "david": "ff2c405b-3dba-41e0-9261-bc8ee3f91f46",
    "ravi": "0d6d0300-6aa7-4620-bf2e-297c7deff756",

    "emily-british": "9497013c-c348-485b-9ede-9b6e246c9578",
    "alice-british": "dac8fda9-5c55-45e5-b378-ebd311dbb311",
    "julia-british": "d70c223b-c039-4f35-9e93-771b2ca481e1",

    "julio": "db9f650b-1846-4865-aa72-eb5d02bcc402",
    "donato": "331127cc-600e-4f19-955a-0689cd310eef",

    "helena-spanish-6": "642bfa76-18da-4574-857d-4e1a7144db39",
    "rosa": "ecf0f240-3a2a-4d9e-876a-d175108b2e42",
    "mariam": "6432587a-1454-4b3f-820a-7a2962124b7c",
}



@router.post("/assistant-initiate-call")
async def make_call_with_livekit(payload: Assistant_Payload, user=Depends(get_current_user)):
    """
    Initiate outbound call via LiveKit agent
    """
    try:
        room_name = f"call-{user['id']}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        metadata = {
            "phone_number": payload.outbound_number,
            "call_context": payload.context,
            "user_id": user["id"],
            "caller_name": payload.caller_name,
            "caller_email": payload.caller_email,
        }
        metadata_json = json.dumps(metadata)

        async with api.LiveKitAPI(
            url=os.getenv("LIVEKIT_URL", "").replace("wss://", "https://"),
            api_key=os.getenv("LIVEKIT_API_KEY"),
            api_secret=os.getenv("LIVEKIT_API_SECRET"),
        ) as lkapi:
            
            dispatch = await lkapi.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name="outbound-caller",
                    room=room_name,
                    metadata=metadata_json,
                )
            )

        db.insert_call_history(
            user_id=user["id"],
            call_id=room_name,
            status="queued",
            to_number=payload.outbound_number,
            voice_name=getattr(payload, "voice", None),
        )

        return JSONResponse({
            "success": True,
            "room_name": room_name,
            "dispatch_id": dispatch.id,
            "message": "Call initiated successfully"
        })

    except Exception as e:
        logging.error(f"Error initiating LiveKit call: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initiate call: {str(e)}")

import traceback  # ← ADD THIS at top of router.py if not already there

@router.post("/livekit-webhook")
async def livekit_webhook(request: Request):
    """
    Unified webhook endpoint for ALL LiveKit events
    """
    try:
        data = await request.json()
        event = data.get("event")
        room = data.get("room", {})
        call_id = room.get("name")
        
        logging.info(f"📥 Received LiveKit webhook event: {event} for room: {call_id}")

        # Don't update status for every participant event - too noisy
        # Only update for meaningful events
        
        if event == "room_started":
            if call_id:
                db.update_call_history(call_id=call_id, updates={"status": "in_progress"})
                return JSONResponse({"message": f"Room {call_id} started"})
        
        elif event == "participant_joined":
            participant = data.get("participant", {})
            identity = participant.get("identity", "")
            
            # Only update if it's the actual caller (not agent or egress)
            if call_id and identity.startswith("sip-"):
                db.update_call_history(call_id=call_id, updates={"status": "connected"})
                return JSONResponse({"message": "Caller connected"})
            
            # Don't update for agent/egress joins
            return JSONResponse({"message": "Participant joined (ignored)"})
        
        elif event == "participant_left":
            participant = data.get("participant", {})
            identity = participant.get("identity", "")
            
            # Only care if caller leaves
            if call_id and identity.startswith("sip-"):
                db.update_call_history(call_id=call_id, updates={"status": "ended"})
                return JSONResponse({"message": "Call ended"})
            
            return JSONResponse({"message": "Participant left (ignored)"})
        
        elif event in ["room_finished", "room_ended"]:
            if call_id:
                # Calculate duration
                started_at = room.get("creation_time") or room.get("created_at")
                ended_at = room.get("end_time") or room.get("ended_at")
                duration = None
                
                if started_at and ended_at:
                    try:
                        if isinstance(started_at, (int, float)):
                            start_dt = datetime.utcfromtimestamp(started_at)
                        else:
                            start_dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))

                        if isinstance(ended_at, (int, float)):
                            end_dt = datetime.utcfromtimestamp(ended_at)
                        else:
                            end_dt = datetime.fromisoformat(str(ended_at).replace("Z", "+00:00"))

                        duration = max(0, (end_dt - start_dt).total_seconds())
                    except Exception as e:
                        logging.error(f"Error calculating duration: {e}")
                
                updates = {
                    "status": "completed",
                    "duration": duration,
                    "ended_at": ended_at
                }
                
                # Remove None values
                updates = {k: v for k, v in updates.items() if v is not None}
                
                db.update_call_history(call_id=call_id, updates=updates)
                return JSONResponse({"message": f"Room {call_id} completed"})
        
        elif event == "egress_ended":
            egress_info = data.get("egress_info", {}) or data.get("egressInfo", {})
            room_name = egress_info.get("room_name") or egress_info.get("roomName")
            
            # Try to get recording URL from webhook
            file_results = egress_info.get("file_results", []) or egress_info.get("fileResults", [])
            
            if file_results and room_name:
                file_info = file_results[0] if isinstance(file_results, list) else file_results
                recording_url = (
                    file_info.get("download_url") or 
                    file_info.get("downloadUrl") or
                    file_info.get("location") or 
                    file_info.get("filename")
                )
                
                if recording_url:
                    # If it's not a full URL, construct it
                    if not recording_url.startswith("http"):
                        bucket_name = os.getenv("GOOGLE_BUCKET_NAME")
                        recording_url = f"https://storage.googleapis.com/{bucket_name}/{recording_url}"
                    
                    db.update_call_history(
                        call_id=room_name,
                        updates={"recording_url": recording_url}
                    )
                    logging.info(f"✅ Recording URL stored: {recording_url}")
                    return JSONResponse({"message": "Recording URL stored"})
        
        # Ignore noisy events
        elif event in ["track_published", "track_unpublished", "egress_started", "egress_updated"]:
            return JSONResponse({"message": f"Event {event} ignored"})
        
        # Log unhandled events
        else:
            logging.info(f"ℹ️ Unhandled event type: {event}")
            return JSONResponse({"message": f"Event {event} ignored"})

    except Exception as e:
        logging.error(f"❌ Error handling LiveKit webhook: {e}")
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)

# Keep the separate egress endpoint as an alias (optional)
@router.post("/livekit-egress-webhook")
async def livekit_egress_webhook(request: Request):
    """Alias endpoint for egress-specific webhooks"""
    return await livekit_webhook(request)

    

@router.get("/call-history")
async def get_user_call_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    user = Depends(get_current_user)
):
    try:
        call_history = db.get_call_history_by_user_id(user["id"], page, page_size)
        return JSONResponse(content=jsonable_encoder({
            "user_id": user["id"],
            "pagination": {
                "page": call_history["page"],
                "page_size": call_history["page_size"],
                "total": call_history["total"],
                "completed_calls": call_history["completed_calls"],
                "not_completed_calls": call_history["not_completed_calls"]
            },
            "calls": call_history["calls"]
        }))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching call history: {str(e)}")




@router.get("/call-status/{call_id}")
async def get_call_status(call_id: str, user=Depends(get_current_user)):
    """
    Get comprehensive call status and data
    """
    try:
        call_data = db.get_call_by_id(call_id, user["id"])
        if not call_data:
            raise HTTPException(status_code=404, detail="Call not found")
        
        return JSONResponse({
            "success": True,
            "call_id": call_id,
            "status": call_data.get("status"),
            "duration": call_data.get("duration"),
            "started_at": str(call_data.get("started_at")) if call_data.get("started_at") else None,
            "ended_at": str(call_data.get("ended_at")) if call_data.get("ended_at") else None,
            "created_at": str(call_data.get("created_at")) if call_data.get("created_at") else None,
            "recording_url": call_data.get("recording_url"),
            "transcript_url": call_data.get("transcript_url"),
            "has_transcript": bool(call_data.get("transcript")),
            "has_recording": bool(call_data.get("recording_url")),
            "to_number": call_data.get("to_number"),
            "from_number": call_data.get("from_number")
        })
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching call status: {e}")
        raise HTTPException(status_code=500, detail=str(e))






@router.get("/agent/get-appointments/{user_id}")
async def get_appointments(user_id: int, from_date: str = None):
    """
    API for LiveKit agent to get all appointments for checking conflicts
    """
    try:
        appointments = db.get_user_appointments(user_id, from_date)
        
        return JSONResponse({
            "success": True,
            "user_id": user_id,
            "appointments": [
                {
                    "id": apt["id"],
                    "date": str(apt["appointment_date"]),
                    "start_time": str(apt["start_time"]),
                    "end_time": str(apt["end_time"]),
                    "attendee_email": apt["attendee_email"],
                    "attendee_name": apt["attendee_name"],
                    "title": apt["title"],
                    "description": apt["description"],
                    "status": apt["status"]
                }
                for apt in appointments
            ]
        })
        
    except Exception as e:
        logging.error(f"Error fetching appointments: {e}")
        return error_response(f"Failed to fetch appointments: {str(e)}", status_code=500)


@router.post("/agent/check-availability")
async def check_availability(request: Request):
    """
    API for LiveKit agent to check if a time slot is available
    """
    try:
        data = await request.json()
        
        user_id = data.get("user_id")
        appointment_date = data.get("appointment_date")
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        
        has_conflict = db.check_appointment_conflict(
            user_id=user_id,
            appointment_date=appointment_date,
            start_time=start_time,
            end_time=end_time
        )
        
        return JSONResponse({
            "success": True,
            "available": not has_conflict,
            "message": "Time slot available" if not has_conflict else "Time slot already booked"
        })
        
    except Exception as e:
        logging.error(f"Error checking availability: {e}")
        return error_response(f"Failed to check availability: {str(e)}", status_code=500)
    

from src.utils.mail_management import Send_Mail

mail_obj = Send_Mail()

@router.post("/agent/book-appointment")
async def book_appointment(request: Request):
    """
    API for LiveKit agent to book an appointment
    """
    try:
        data = await request.json()
        
        user_id = data.get("user_id")
        appointment_date = data.get("appointment_date") 
        start_time = data.get("start_time")  # HH:MM
        end_time = data.get("end_time")  # HH:MM
        attendee_name = data.get("attendee_name", "Valued Customer")
        title = data.get("title", "Appointment")
        description = data.get("description", "")
        organizer_name = data.get("organizer_name")
        organizer_email = data.get("organizer_email")
        
        # Validate required fields
        if not all([user_id, appointment_date, start_time, end_time, organizer_email]):
            return error_response("Missing required fields", status_code=400)
        
        # Check for conflicts
        has_conflict = db.check_appointment_conflict(
            user_id=user_id,
            appointment_date=appointment_date,
            start_time=start_time,
            end_time=end_time
        )
        
        if has_conflict:
            return JSONResponse(
                status_code=409,
                content={
                    "success": False,
                    "message": "Time slot already booked",
                    "conflict": True
                }
            )
        
        # Create appointment
        appointment_id = db.create_appointment(
            user_id=user_id,
            appointment_date=appointment_date,
            start_time=start_time,
            end_time=end_time,
            attendee_name=attendee_name,
            attendee_email=organizer_email,
            title=title,
            description=description
        )
        
        # Send email with calendar invite to organizer (ourself)
        email_sent = await mail_obj.send_email_with_calendar_event(
            attendee_email=organizer_email,
            attendee_name=organizer_name,
            appointment_date=appointment_date,
            start_time=start_time,
            end_time=end_time,
            title=title,
            description=description,
            organizer_name=organizer_name,
            organizer_email=organizer_email
        )
        
        return JSONResponse({
            "success": True,
            "appointment_id": appointment_id,
            "email_sent": email_sent,
            "message": "Appointment booked successfully"
        })
        
    except Exception as e:
        logging.error(f"Error booking appointment: {e}")
        return error_response(f"Failed to book appointment: {str(e)}", status_code=500)
    
@router.get("/calls/{call_id}/transcript")
async def get_call_transcript(call_id: str, user=Depends(get_current_user)):
        """
        Get transcript for a specific call
        Returns both the URL and parsed content if available
        """
        try:
            call_data = db.get_call_by_id(call_id, user["id"])
            
            if not call_data:
                raise HTTPException(status_code=404, detail="Call not found")
            
            return JSONResponse({
                "success": True,
                "call_id": call_id,
                "transcript_url": call_data.get("transcript_url"),
                "transcript_blob": call_data.get("transcript_blob"),
                "transcript_content": call_data.get("transcript"),  # Parsed JSONB
                "uploaded_at": str(call_data.get("created_at"))
            })
            
        except HTTPException:
            raise
        except Exception as e:
            logging.error(f"Error fetching transcript: {e}")
            raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.get("/calls/{call_id}/recording")
async def get_call_recording(call_id: str, user=Depends(get_current_user)):
    """
    Get recording URL for a specific call
    """
    try:
        call_data = db.get_call_by_id(call_id, user["id"])
        
        if not call_data:
            raise HTTPException(status_code=404, detail="Call not found")
        
        recording_url = call_data.get("recording_url")
        
        if not recording_url:
            return JSONResponse({
                "success": False,
                "message": "Recording not available yet"
            }, status_code=404)
        
        return JSONResponse({
            "success": True,
            "call_id": call_id,
            "recording_url": recording_url,
            "duration": call_data.get("duration"),
            "started_at": str(call_data.get("started_at")),
            "ended_at": str(call_data.get("ended_at"))
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching recording: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
    
# @router.post("/call-transcript-uploaded")
# async def call_transcript_uploaded(request: Request):
#         """
#         Endpoint to receive transcript upload notification from LiveKit agent
#         Stores transcript URL and optionally downloads & parses the content
#         """
#         try:
#             data = await request.json()
            
#             user_id = data.get("user_id")
#             call_id = data.get("call_id")
#             transcript_url = data.get("transcript_url")
#             transcript_blob = data.get("transcript_blob")
#             uploaded_at = data.get("uploaded_at")
            
#             logging.info(f"📝 Transcript uploaded for call {call_id}")
            
#             # Update call history with transcript URL
#             updates = {
#                 "transcript_url": transcript_url,
#                 "transcript_blob": transcript_blob,
#             }
            
#             # Optional: Download and parse transcript content
#             try:
#                 async with httpx.AsyncClient(timeout=30) as client:
#                     response = await client.get(transcript_url)
#                     if response.status_code == 200:
#                         transcript_data = response.json()
#                         # Store the parsed transcript in JSONB column
#                         updates["transcript"] = transcript_data
#                         logging.info(f"✅ Transcript content downloaded and parsed for {call_id}")
#                     else:
#                         logging.warning(f"Failed to download transcript: {response.status_code}")
#             except Exception as e:
#                 logging.error(f"Error downloading transcript content: {e}")
#                 # Continue anyway - we still have the URL
            
#             # Update database
#             db.update_call_history(call_id=call_id, updates=updates)
            
#             return JSONResponse({
#                 "success": True,
#                 "message": "Transcript URL stored successfully",
#                 "call_id": call_id
#             })
            
#         except Exception as e:
#             logging.error(f"Error handling transcript upload: {e}")
#             traceback.print_exc()
#             return JSONResponse(
#                 status_code=500,
#                 content={"error": f"Failed to process transcript: {str(e)}"}
#             )


@router.post("/agent/save-call-data")
async def save_call_data(request: Request):
    """
    Receive call data from LiveKit agent after call ends
    Automatically fetches and stores transcript content
    """
    try:
        data = await request.json()
        
        user_id = data.get("user_id")
        call_id = data.get("call_id")
        transcript_url = data.get("transcript_url")
        transcript_blob = data.get("transcript_blob")
        recording_url = data.get("recording_url")
        recording_blob = data.get("recording_blob")
        uploaded_at = data.get("uploaded_at")
        
        logging.info(f"📝 Received call data from agent for call {call_id}")
        logging.info(f"   Transcript blob: {transcript_blob}")
        logging.info(f"   Recording blob: {recording_blob}")
        
        # First, store URLs and blob paths
        updates = {
            "transcript_url": transcript_url,
            "transcript_blob": transcript_blob,
            "recording_blob": recording_blob,
            "status": "completed"
        }
        
        if recording_url:
            updates["recording_url"] = recording_url
        
        db.update_call_history(call_id=call_id, updates=updates)
        logging.info(f"✅ URLs saved for {call_id}")
        
        # Then, fetch and store the actual transcript content
        transcript_data = await fetch_and_store_transcript(
            call_id=call_id,
            transcript_url=transcript_url,
            transcript_blob=transcript_blob
        )
        
        if transcript_data:
            logging.info(f"✅ Transcript content stored for {call_id}")
        else:
            logging.warning(f"⚠️ Could not fetch transcript content for {call_id}")
        
        return JSONResponse({
            "success": True,
            "message": "Call data saved successfully",
            "transcript_fetched": transcript_data is not None
        })
        
    except Exception as e:
        logging.error(f"❌ Error saving call data: {e}")
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )
    

import base64
from google.cloud import storage
from google.oauth2 import service_account

def get_gcs_client():
    """Initialize GCS client with service account"""
    gcp_key_b64 = os.getenv("GCS_SERVICE_ACCOUNT_KEY")
    if not gcp_key_b64:
        raise RuntimeError("GCS_SERVICE_ACCOUNT_KEY not set")
    
    decoded = base64.b64decode(gcp_key_b64).decode("utf-8")
    key_json = json.loads(decoded)
    credentials = service_account.Credentials.from_service_account_info(key_json)
    return storage.Client(credentials=credentials, project=key_json.get("project_id"))


async def fetch_and_store_transcript(call_id: str, transcript_url: str = None, transcript_blob: str = None):
    """
    Fetch transcript from GCS and store in database
    
    Args:
        call_id: Call identifier
        transcript_url: Signed URL (optional)
        transcript_blob: Blob path in GCS (e.g., "transcripts/call-xxx.json")
    
    Returns:
        dict with transcript data or None
    """
    try:
        transcript_data = None
        
        # Method 1: Download from signed URL
        if transcript_url:
            logging.info(f"📥 Downloading transcript from URL for {call_id}")
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(transcript_url)
                if response.status_code == 200:
                    transcript_data = response.json()
                    logging.info(f"✅ Transcript downloaded from URL for {call_id}")
                else:
                    logging.error(f"Failed to download transcript: {response.status_code}")
        
        # Method 2: Download directly from GCS using blob path (more reliable)
        if not transcript_data and transcript_blob:
            logging.info(f"📥 Downloading transcript from GCS blob for {call_id}")
            gcs = get_gcs_client()
            bucket_name = os.getenv("GOOGLE_BUCKET_NAME")
            bucket = gcs.bucket(bucket_name)
            blob = bucket.blob(transcript_blob)
            
            if blob.exists():
                transcript_json = blob.download_as_text()
                transcript_data = json.loads(transcript_json)
                logging.info(f"✅ Transcript downloaded from GCS blob for {call_id}")
            else:
                logging.error(f"Transcript blob not found: {transcript_blob}")
        
        # Store in database
        if transcript_data:
            db.update_call_history(
                call_id=call_id,
                updates={"transcript": transcript_data}
            )
            logging.info(f"✅ Transcript stored in database for {call_id}")
            return transcript_data
        
        return None
        
    except Exception as e:
        logging.error(f"Error fetching/storing transcript for {call_id}: {e}")
        traceback.print_exc()
        return NoneUpdate