"""
Simplified Pydantic Models for Meeting Minutes Generator
"""

from typing import Optional, List
from pydantic import BaseModel
from enum import Enum

class JobStatus(str, Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class ProcessRequest(BaseModel):
    s3_key: Optional[str] = None
    content: Optional[str] = None
    meeting_title: str = "Meeting Minutes"

class ProcessResponse(BaseModel):
    job_id: str
    status: str
    message: str
    meeting_title: str

class OutputFile(BaseModel):
    filename: str
    download_url: str

class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    meeting_title: str
    created_at: str
    completed_at: Optional[str] = None
    processing_time_seconds: Optional[int] = None
    output_files: Optional[List[OutputFile]] = None
    error: Optional[str] = None

class PresignedUrlResponse(BaseModel):
    presigned_url: str
    fields: dict
    s3_key: str
    job_id: str
    expires_in: int