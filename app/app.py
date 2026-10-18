"""
Enhanced Meeting Minutes Generator - FastAPI Application with Better Error Handling
"""

import os
import json
import asyncio
import io
import time
import logging
from datetime import datetime
from typing import Dict, Optional, List
from uuid import uuid4
import traceback

# Disable X-Ray tracing before importing boto3
os.environ['_X_AMZN_TRACE_ID'] = ''
os.environ['AWS_XRAY_TRACING_NAME'] = ''
os.environ['AWS_XRAY_CONTEXT_MISSING'] = 'LOG_ERROR'

from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from strands import Agent, tool
from strands_tools import speak
import boto3
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors

from models import ProcessRequest, ProcessResponse, JobStatusResponse, OutputFile, JobStatus, PresignedUrlResponse
from s3_utils import (
    generate_presigned_upload_url,
    read_s3_file_content,
    upload_outputs_to_s3,
    get_content_type
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



app = FastAPI(title="Meeting Minutes Generator", version="2.0.0")

# CORS Configuration - Why allow_origins=["*"] is necessary:
# 
# 1. DEVELOPMENT ENVIRONMENT:
#    - Streamlit frontend runs on localhost:8501
#    - FastAPI backend runs on localhost:8000
#    - Different ports = different origins, requiring CORS
#
# 2. PRODUCTION DEPLOYMENT:
#    - Frontend may be deployed on different domain/subdomain than backend
#    - ALB (Application Load Balancer) may use different DNS names
#    - CDN or proxy services may introduce additional origin variations
#
# 3. INTEGRATION FLEXIBILITY:
#    - Allows third-party applications to integrate with the API
#    - Supports various deployment architectures (microservices, etc.)
#    - Enables testing from different development environments
#
# SECURITY NOTE: allow_credentials=False is safe because:
# - This is a stateless public API with no authentication system
# - No cookies or authorization headers are used
# - No sensitive user data is transmitted via credentials
# - Wildcard origins with credentials=False prevents credential-based attacks
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"]
)



# AWS Configuration
try:
    s3_client = boto3.client('s3')
    INPUT_BUCKET = os.environ.get('INPUT_BUCKET_NAME')
    OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET_NAME')
    
    if not INPUT_BUCKET or not OUTPUT_BUCKET:
        logger.info("S3 buckets not configured - running in local mode")
except Exception as e:
    logger.error(f"AWS configuration error: {e}")
    s3_client = None

# Job storage (in production, use Redis or database)
jobs = {}

# Ensure outputs directory exists
os.makedirs("outputs", exist_ok=True)

@app.get('/')
def read_root():
    return {"message": "Meeting Minutes Generator API", "version": "2.0.0", "status": "running"}

@app.get('/health')
def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "s3_configured": bool(s3_client and INPUT_BUCKET and OUTPUT_BUCKET)
    }

@app.post('/upload-url')
async def get_upload_url(filename: str, content_type: str = "text/plain"):
    """Generate presigned URL for direct S3 upload"""
    try:
        if not s3_client or not INPUT_BUCKET:
            raise HTTPException(status_code=503, detail="S3 not configured")
        
        result = generate_presigned_upload_url(filename, content_type)
        return PresignedUrlResponse(**result)
    except Exception as e:
        logger.error(f"Upload URL generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post('/upload-direct', response_model=ProcessResponse)
async def upload_direct(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    meeting_title: str = "Meeting Minutes"
):
    """Direct file upload and processing"""
    try:
        job_id = str(uuid4())
        
        # Read file content
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file uploaded")
        
        # Decode content
        try:
            content_str = content.decode('utf-8')
            # Add this logging:
            logger.info(f"File upload content length: {len(content_str)}")
            logger.info(f"File upload content preview: {content_str[:200]}...")
            logger.info(f"Content type detected: {type(content_str)}")
        except UnicodeDecodeError:
            # Try other common encodings
            try:
                content_str = content.decode('latin-1')
            except UnicodeDecodeError:
                raise HTTPException(status_code=400, detail="Unable to decode file content")
        
        if not content_str.strip():
            raise HTTPException(status_code=400, detail="File contains no readable content")
        
        # Initialize job
        jobs[job_id] = {
            "job_id": job_id,
            "status": JobStatus.PROCESSING,
            "meeting_title": meeting_title,
            "created_at": datetime.now().isoformat(),
            "filename": file.filename
        }
        
        # Start background processing
        background_tasks.add_task(process_meeting_background, job_id, content_str, meeting_title)
        
        return ProcessResponse(
            job_id=job_id,
            status=JobStatus.PROCESSING,
            message="Meeting processing started",
            meeting_title=meeting_title
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Direct upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@app.post('/process-s3-meeting', response_model=ProcessResponse)
async def process_s3_meeting(background_tasks: BackgroundTasks, request: ProcessRequest):
    """Process document from S3 or direct content"""
    try:
        job_id = str(uuid4())
        
        # Get content
        content = None
        if request.s3_key:
            if not s3_client or not INPUT_BUCKET:
                raise HTTPException(status_code=503, detail="S3 not configured")
            content = read_s3_file_content(request.s3_key)
        elif request.content:
            content = request.content
        else:
            raise HTTPException(status_code=400, detail="Either s3_key or content required")
        
        if not content or not content.strip():
            raise HTTPException(status_code=400, detail="No valid content provided")
        
        # Initialize job
        jobs[job_id] = {
            "job_id": job_id,
            "status": JobStatus.PROCESSING,
            "meeting_title": request.meeting_title,
            "created_at": datetime.now().isoformat()
        }
        
        # Start background processing
        background_tasks.add_task(process_meeting_background, job_id, content, request.meeting_title)
        
        return ProcessResponse(
            job_id=job_id,
            status=JobStatus.PROCESSING,
            message="Meeting processing started",
            meeting_title=request.meeting_title
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"S3 processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

async def process_meeting_background(job_id: str, content: str, title: str):
    """Enhanced background processing with better error handling"""
    start_time = time.time()
    
    try:
        logger.info(f"Starting processing for job {job_id}")
        
        # Process meeting content
        result = await process_meeting_content(content, title, job_id)
        
        # Small delay to ensure file operations complete
        await asyncio.sleep(2)
        
        if result["status"] == "completed":
            # Upload outputs
            if s3_client and OUTPUT_BUCKET:
                try:
                    outputs_dict = await upload_outputs_to_s3(job_id)
                    output_files = [
                        OutputFile(filename=info["filename"], download_url=info["download_url"])
                        for info in outputs_dict.values()
                    ]
                except Exception as upload_error:
                    logger.error(f"S3 upload failed, using local files: {upload_error}")
                    output_files = get_local_files()
            else:
                output_files = get_local_files()
            
            processing_time = int(time.time() - start_time)
            
            jobs[job_id] = {
                "job_id": job_id,
                "status": JobStatus.COMPLETED,
                "meeting_title": title,
                "created_at": jobs[job_id]["created_at"],
                "completed_at": datetime.now().isoformat(),
                "processing_time_seconds": processing_time,
                "output_files": output_files
            }
            logger.info(f"Job {job_id} completed successfully")
        else:
            processing_time = int(time.time() - start_time)
            jobs[job_id] = {
                "job_id": job_id,
                "status": JobStatus.FAILED,
                "meeting_title": title,
                "created_at": jobs[job_id]["created_at"],
                "completed_at": datetime.now().isoformat(),
                "processing_time_seconds": processing_time,
                "error": result.get("error", "Processing failed")
            }
            logger.error(f"Job {job_id} failed: {result.get('error')}")
    
    except Exception as e:
        processing_time = int(time.time() - start_time)
        error_msg = f"Background processing error: {str(e)}"
        logger.error(f"Job {job_id} failed with exception: {e}\n{traceback.format_exc()}")
        
        jobs[job_id] = {
            "job_id": job_id,
            "status": JobStatus.FAILED,
            "meeting_title": title,
            "created_at": jobs[job_id]["created_at"],
            "completed_at": datetime.now().isoformat(),
            "processing_time_seconds": processing_time,
            "error": error_msg
        }
    
    finally:
        pass  # Cleanup completed

@app.get('/job-status/{job_id}', response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    logger.info(f"Status check for job {job_id}")
    if job_id not in jobs:
        logger.warning(f"Job {job_id} not found in jobs dict")
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = jobs[job_id]
    logger.info(f"Job {job_id} status: {job_data.get('status')}")
    return JobStatusResponse(**job_data)

@app.get('/jobs')
async def list_jobs():
    """List all jobs (for debugging)"""
    return {"jobs": list(jobs.keys()), "total": len(jobs)}

async def process_meeting_content(content: str, title: str, job_id: str) -> Dict:
    """2-Agent sequential processing using agents-as-tools pattern"""
    try:
        logger.info(f"Starting 2-agent sequential processing for job {job_id}")
        cleanup_old_files()
        
        if not content or len(content.strip()) < 10:
            raise ValueError("No valid content to process")
        
        # Step 1: Generate structured minutes directly
        minutes_result = generate_structured_minutes(content, title)
        logger.info("Step 1: Minutes generation completed")
        
        # Step 2: Generate concise minutes using the actual content
        concise_result = concise_minutes_agent(str(minutes_result))
        concise_minutes = str(concise_result)[:2900]
        logger.info(f"Step 2: Concise minutes generated ({len(concise_minutes)} chars)")
        
        # Step 3: Generate audio from concise minutes
        audio_result = audio_generator_agent(concise_minutes)
        logger.info("Step 3: Audio generation completed")
        
        # Step 4: Generate PDF directly from the content
        pdf_result = create_comprehensive_pdf(str(minutes_result))
        logger.info("Step 4: PDF generation completed")
        
        return {
            "job_id": job_id,
            "status": "completed",
            "meeting_title": title,
            "processing_result": f"2-Agent processing completed: Minutes, Concise: {len(concise_minutes)} chars, Audio, PDF"
        }
        
    except Exception as e:
        logger.error(f"Content processing failed for job {job_id}: {e}")
        return {
            "job_id": job_id,
            "status": "failed",
            "error": f"Processing error: {str(e)}"
        }



@tool
def concise_minutes_agent(structured_minutes: str) -> str:
    """Convert structured minutes to concise version under 3000 chars"""
    try:
        logger.info("Starting concise minutes generation")
        agent = Agent(
            system_prompt="""Convert meeting minutes to concise version under 3000 characters.
            Focus only on key decisions, action items, and outcomes. Remove detailed discussions.
            Output plain text without markdown formatting."""
        )
        result = str(agent(f"Make this concise under 3000 chars: {structured_minutes}"))
        logger.info("Concise minutes generation completed")
        return result
    except Exception as e:
        logger.error(f"Concise minutes generation failed: {e}")
        return f"Concise minutes error: {str(e)}"



@tool
def audio_generator_agent(concise_minutes: str) -> str:
    """Generate audio from concise meeting minutes"""
    try:
        logger.info("Starting audio generation from concise minutes")
        agent = Agent(
            system_prompt="""Generate audio files from concise text using speak tool with polly mode.
            Use the provided text directly for audio generation.""",
            tools=[speak]
        )
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"outputs/meeting_audio_{timestamp}.mp3"
        
        result = str(agent(f"Use speak tool to generate audio: text='{concise_minutes}', mode='polly', output_path='{output_path}', play_audio=True"))
        logger.info("Audio generation completed")
        return result
    except Exception as e:
        logger.error(f"Audio generation failed: {e}")
        return f"Audio generation error: {str(e)}"

@tool
def generate_structured_minutes(content: str, title: str = "Meeting Minutes") -> str:
    """Enhanced structured minutes generation with better validation"""
    try:
        logger.info(f"Generating structured minutes for: {title}")
        
        # Validate input content
        if not content or not content.strip():
            logger.warning("No content provided for minutes generation")
            content = "No meeting content was available for processing."
        
        agent = Agent(
            system_prompt="""You are a professional meeting minutes generator. 
            Create comprehensive, well-structured meeting minutes with:
            - Clear meeting header with title, date, time, attendees
            - Organized discussion topics
            - Specific action items with responsible parties and deadlines
            - Professional formatting suitable for business use
            
            Structure with these sections:
            1. Meeting Information (title, date, time, duration)
            2. Attendees (list all participants)
            3. Agenda Items (main topics discussed)
            4. Discussion Summary (key points and decisions)
            5. Action Items (tasks, assignees, deadlines)
            6. Next Steps (follow-up plans)
            
            IMPORTANT: Output PLAIN TEXT ONLY. Do NOT use markdown formatting like # ## ** - or any other markdown syntax.
            Use simple text formatting with clear section headers and bullet points using regular text.
            If the content is limited, create a basic structure and note where information is incomplete.
            Always ensure the output is complete and meaningful."""
        )
        
        prompt = f"""Create comprehensive meeting minutes from this content:
        
        Meeting Title: {title}
        Content: {content}
        
        Generate complete, professional meeting minutes with all required sections.
        If some information is missing from the content, create appropriate placeholders and note what information should be added."""
        
        try:
            result = agent(prompt)
            minutes_content = str(result) if result else ""
        except Exception as agent_error:
            logger.error(f"Agent processing failed: {agent_error}")
            minutes_content = f"Agent processing encountered an error. Basic minutes structure created from available content.\n\nContent: {content[:500]}..."
        
        # Ensure we have some content
        if not minutes_content.strip():
            minutes_content = f"""{title}

Meeting Information
Title: {title}
Date: {datetime.now().strftime('%Y-%m-%d')}
Time: [To be filled]
Duration: [To be filled]

Attendees
[To be filled from meeting records]

Agenda Items
Main topics discussed during the meeting

Discussion Summary
{content[:800]}{'...' if len(content) > 800 else ''}

Action Items
[To be extracted from detailed meeting review]

Next Steps
Review and distribute meeting minutes
Follow up on action items identified
"""
        
        # Create timestamped filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"outputs/meeting_minutes_{timestamp}.txt"
        
        # Write file with plain text content (no markdown)
        full_content = f"""{title}

Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Document Type: Meeting Minutes
Status: Generated

{minutes_content}

This document was automatically generated from meeting transcript data.
"""
        
        try:
            # Ensure the outputs directory exists
            os.makedirs("outputs", exist_ok=True)
            
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(full_content)
            
            # Verify file creation with detailed checks
            if os.path.exists(filename):
                file_size = os.path.getsize(filename)
                if file_size > 0:
                    logger.info(f"Minutes file created successfully: {filename} (Size: {file_size} bytes)")
                    # Return the actual content, not just the filename
                    return full_content
                else:
                    logger.error(f"Minutes file is empty: {filename}")
                    raise Exception(f"Minutes file was created but is empty: {filename}")
            else:
                logger.error(f"Minutes file was not created: {filename}")
                raise Exception(f"Failed to create minutes file: {filename}")
                
        except Exception as file_error:
            logger.error(f"File creation failed: {file_error}")
            raise Exception(f"Could not create minutes file: {str(file_error)}")
            
    except Exception as e:
        logger.error(f"Minutes generation failed: {e}")
        # Create a fallback file even if everything fails
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            fallback_filename = f"outputs/meeting_minutes_fallback_{timestamp}.txt"
            fallback_content = f"""{title if title else 'Meeting Minutes'}

Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Status: Fallback Generation (Error in main processing)
Error: {str(e)}

Raw Content:
{content if content else 'No content available'}

This is a fallback document created due to processing errors.
"""
            os.makedirs("outputs", exist_ok=True)
            with open(fallback_filename, 'w', encoding='utf-8') as f:
                f.write(fallback_content)
            # Return the actual content for fallback too
            return fallback_content
        except:
            raise Exception(f"Complete minutes generation failure: {str(e)}")



@tool
def create_comprehensive_pdf(minutes: str) -> str:
    """Enhanced PDF generation with better error handling"""
    try:
        logger.info("Creating comprehensive PDF report")
        
        # Validate input
        if not minutes or not minutes.strip():
            logger.warning("No minutes content provided for PDF generation")
            minutes = "No meeting minutes content was available for PDF report generation."
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"outputs/meeting_report_{timestamp}.pdf"
        
        try:
            # Create PDF document with error handling
            os.makedirs("outputs", exist_ok=True)
            doc = SimpleDocTemplate(filename, pagesize=A4, leftMargin=72, rightMargin=72, topMargin=72, bottomMargin=72)
            styles = getSampleStyleSheet()
            story = []
            
            # Custom styles
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=24,
                spaceAfter=30,
                textColor=colors.darkblue,
                alignment=1  # Center alignment
            )
            
            subtitle_style = ParagraphStyle(
                'CustomSubtitle',
                parent=styles['Normal'],
                fontSize=12,
                spaceAfter=20,
                textColor=colors.grey,
                alignment=1
            )
            
            header_style = ParagraphStyle(
                'CustomHeader',
                parent=styles['Heading2'],
                fontSize=16,
                spaceBefore=12,
                spaceAfter=6,
                textColor=colors.darkblue
            )
            
            # Add content with error handling for each element
            try:
                story.append(Paragraph("Meeting Minutes Report", title_style))
                story.append(Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}", subtitle_style))
                story.append(Spacer(1, 20))
            except Exception as header_error:
                logger.warning(f"PDF header creation error: {header_error}")
                # Continue with basic content
            
            # Process minutes content safely
            content_to_add = str(minutes) if minutes else "No meeting content available"
            
            # Add content as plain text (no markdown parsing needed)
            try:
                lines = content_to_add.split('\n')
                for line in lines:
                    line_stripped = line.strip()
                    if not line_stripped:
                        story.append(Spacer(1, 6))
                        continue
                    
                    # Escape problematic characters for ReportLab
                    safe_line = line_stripped.replace('<', '&lt;').replace('>', '&gt;')
                    story.append(Paragraph(safe_line, styles['Normal']))
                    story.append(Spacer(1, 3))
                            
            except Exception as content_error:
                logger.warning(f"PDF content processing error: {content_error}")
                # Add raw content as fallback
                story.append(Paragraph("Content:", header_style))
                story.append(Paragraph(content_to_add[:1000], styles['Normal']))
            
            # Add footer
            try:
                story.append(Spacer(1, 30))
                story.append(Paragraph("---", styles['Normal']))
                story.append(Paragraph("This document was automatically generated from meeting transcript data.", 
                                      subtitle_style))
            except Exception as footer_error:
                logger.warning(f"PDF footer creation error: {footer_error}")
            
            # Build PDF with error handling
            try:
                doc.build(story)
            except Exception as build_error:
                logger.error(f"PDF build error: {build_error}")
                raise Exception(f"PDF build failed: {str(build_error)}")
            
            # Verify file creation
            if os.path.exists(filename):
                file_size = os.path.getsize(filename)
                if file_size > 0:
                    logger.info(f"PDF created successfully: {filename} (Size: {file_size} bytes)")
                    return f"PDF report created: {filename} (Size: {file_size} bytes)"
                else:
                    raise Exception(f"PDF file was created but is empty: {filename}")
            else:
                raise Exception(f"PDF file was not created: {filename}")
                
        except Exception as pdf_error:
            logger.error(f"PDF creation error: {pdf_error}")
            raise Exception(f"PDF creation failed: {str(pdf_error)}")
            
    except Exception as e:
        logger.error(f"PDF generation failed: {e}")
        # Create fallback text file instead of PDF
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            fallback_filename = f"outputs/meeting_report_fallback_{timestamp}.txt"
            fallback_content = f"""Meeting Report - Text Fallback

Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Status: Fallback (PDF generation failed)
Error: {str(e)}

---

MEETING CONTENT:

{minutes if minutes else 'No content available'}

---

*This is a fallback text document created due to PDF generation errors.*
"""
            os.makedirs("outputs", exist_ok=True)
            with open(fallback_filename, 'w', encoding='utf-8') as f:
                f.write(fallback_content)
            return f"Fallback text report created: {fallback_filename}"
        except:
            raise Exception(f"Complete PDF generation failure: {str(e)}")

def get_local_files(request_host: str = None) -> List[OutputFile]:
    """Enhanced local file retrieval with environment-aware URLs"""
    outputs = []
    import glob
    import time
    
    patterns = [
        "outputs/meeting_minutes_*.txt",
        "outputs/meeting_audio_*.*",
        "outputs/meeting_report_*.pdf"
    ]
    current_time = time.time()
    
    # Determine base URL based on environment
    base_url = os.environ.get('API_BASE_URL')
    if not base_url:
        if request_host:
            base_url = f"http://{request_host}"
        else:
            base_url = "http://localhost:8000"  # Fallback for local dev
    
    for pattern in patterns:
        files = glob.glob(pattern)
        for file_path in files:
            try:
                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    file_age = current_time - os.path.getctime(file_path)
                    if file_age <= 300:  # Recent files only (5 minutes)
                        filename = os.path.basename(file_path)
                        outputs.append(OutputFile(
                            filename=filename,
                            download_url=f"{base_url}/download-local/{filename}"
                        ))
                        logger.info(f"Found local file: {filename} (Size: {os.path.getsize(file_path)} bytes)")
            except Exception as e:
                logger.warning(f"Error processing local file {file_path}: {e}")
    
    return outputs

def cleanup_old_files():
    """Enhanced cleanup with better logging"""
    try:
        import glob
        import time
        
        patterns = [
            "outputs/meeting_minutes_*.txt",
            "outputs/meeting_audio_*.mp3",
            "outputs/meeting_report_*.pdf"
        ]
        
        current_time = time.time()
        cleaned_count = 0
        
        for pattern in patterns:
            files = glob.glob(pattern)
            for file_path in files:
                try:
                    file_age = current_time - os.path.getctime(file_path)
                    if file_age > 600:  # 10 minutes
                        os.remove(file_path)
                        cleaned_count += 1
                        logger.info(f"Cleaned up old file: {file_path}")
                except Exception as e:
                    logger.warning(f"Could not clean up {file_path}: {e}")
        
        if cleaned_count > 0:
            logger.info(f"Cleanup completed: {cleaned_count} files removed")
            
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

@app.get('/download-local/{filename}')
async def download_local_file(filename: str):
    """Enhanced local file download with validation"""
    try:
        # Security check - only allow files in outputs directory
        if not filename or '..' in filename or '/' in filename:
            raise HTTPException(status_code=400, detail="Invalid filename")
        
        file_path = os.path.join("outputs", filename)
        
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File not found")
        
        if os.path.getsize(file_path) == 0:
            raise HTTPException(status_code=404, detail="File is empty")
        
        return FileResponse(
            path=file_path,
            filename=filename,
            media_type=get_content_type(filename)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Download failed for {filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")

@app.get('/download/{s3_key:path}')
async def download_file(s3_key: str):
    """Enhanced S3 file download"""
    try:
        if not s3_client or not OUTPUT_BUCKET:
            raise HTTPException(status_code=503, detail="S3 not configured")
        
        response = s3_client.get_object(Bucket=OUTPUT_BUCKET, Key=s3_key)
        content = response['Body'].read()
        
        if not content:
            raise HTTPException(status_code=404, detail="File is empty")
        
        filename = os.path.basename(s3_key)
        content_type = get_content_type(filename)
        
        return StreamingResponse(
            io.BytesIO(content),
            media_type=content_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"S3 download failed for {s3_key}: {e}")
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
