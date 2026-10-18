"""
Corrected S3 utility functions with fixed upload logic to prevent NoneType errors
"""

import os
import glob
import logging
import time
from typing import Dict, Optional, List
from uuid import uuid4

# Disable X-Ray tracing before importing boto3
os.environ['_X_AMZN_TRACE_ID'] = ''
os.environ['AWS_XRAY_TRACING_NAME'] = ''
os.environ['AWS_XRAY_CONTEXT_MISSING'] = 'LOG_ERROR'

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

logger = logging.getLogger(__name__)

# AWS S3 Configuration
try:
    s3_client = boto3.client('s3')
    INPUT_BUCKET = os.environ.get('INPUT_BUCKET_NAME')
    OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET_NAME')
    
    # Test AWS credentials
    try:
        s3_client.list_buckets()
        logger.info("AWS credentials validated successfully")
    except NoCredentialsError:
        logger.error("AWS credentials not found")
        s3_client = None
    except Exception as e:
        logger.error(f"AWS credential validation failed: {e}")
        
except Exception as e:
    logger.error(f"AWS S3 client initialization failed: {e}")
    s3_client = None
    INPUT_BUCKET = None
    OUTPUT_BUCKET = None

def validate_s3_configuration() -> bool:
    """Validate S3 configuration and access"""
    if not s3_client or not INPUT_BUCKET or not OUTPUT_BUCKET:
        logger.warning("S3 not properly configured")
        return False
    
    try:
        # Test bucket access
        s3_client.head_bucket(Bucket=INPUT_BUCKET)
        s3_client.head_bucket(Bucket=OUTPUT_BUCKET)
        logger.info("S3 buckets validated successfully")
        return True
    except ClientError as e:
        logger.error(f"S3 bucket access failed: {e}")
        return False
    except Exception as e:
        logger.error(f"S3 validation error: {e}")
        return False

def generate_presigned_upload_url(filename: str, content_type: str = "text/plain", expires_in: int = 3600) -> Dict:
    """Generate presigned URL for direct frontend S3 upload with enhanced validation"""
    try:
        if not validate_s3_configuration():
            raise Exception("S3 not properly configured")
        
        if not filename or not filename.strip():
            raise Exception("Filename is required")
        
        # Sanitize filename
        safe_filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.')).strip()
        if not safe_filename:
            safe_filename = f"upload_{int(time.time())}.txt"
        
        job_id = str(uuid4())
        s3_key = f"inputs/{job_id}/{safe_filename}"
        
        # Generate presigned POST URL for direct browser upload
        presigned_post = s3_client.generate_presigned_post(
            Bucket=INPUT_BUCKET,
            Key=s3_key,
            Fields={
                "Content-Type": content_type,
                "x-amz-meta-job-id": job_id,
                "x-amz-meta-original-filename": safe_filename,
                "x-amz-meta-upload-timestamp": str(int(time.time()))
            },
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, 100 * 1024 * 1024]  # 1 byte to 100MB
            ],
            ExpiresIn=expires_in
        )
        
        logger.info(f"Generated presigned URL for {safe_filename}, job_id: {job_id}")
        
        return {
            "presigned_url": presigned_post["url"],
            "fields": presigned_post["fields"],
            "s3_key": s3_key,
            "job_id": job_id,
            "expires_in": expires_in,
            "safe_filename": safe_filename
        }
        
    except Exception as e:
        logger.error(f"Error generating presigned URL: {e}")
        raise Exception(f"Failed to generate upload URL: {str(e)}")

def generate_presigned_download_url(s3_key: str, expires_in: int = 3600) -> Optional[str]:
    """Generate presigned URL for file download with validation"""
    try:
        if not validate_s3_configuration():
            logger.error("S3 not configured for download URL generation")
            return None
        
        if not s3_key or not s3_key.strip():
            logger.error("S3 key is required for download URL")
            return None
        
        # Verify object exists
        try:
            s3_client.head_object(Bucket=OUTPUT_BUCKET, Key=s3_key)
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                logger.error(f"Object not found: {s3_key}")
                return None
            raise
        
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': OUTPUT_BUCKET, 'Key': s3_key},
            ExpiresIn=expires_in
        )
        
        logger.info(f"Generated download URL for {s3_key}")
        return url
        
    except Exception as e:
        logger.error(f"Error generating download URL for {s3_key}: {e}")
        return None

def read_s3_file_content(s3_key: str) -> str:
    """Read content from S3 input file with enhanced error handling"""
    try:
        if not validate_s3_configuration():
            raise Exception("S3 not properly configured")
        
        if not s3_key or not s3_key.strip():
            raise Exception("S3 key is required")
        
        logger.info(f"Reading S3 file: {s3_key}")
        
        # Get object with error handling
        try:
            response = s3_client.get_object(Bucket=INPUT_BUCKET, Key=s3_key)
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                raise Exception(f"File not found: {s3_key}")
            elif e.response['Error']['Code'] == 'AccessDenied':
                raise Exception(f"Access denied to file: {s3_key}")
            else:
                raise Exception(f"AWS error reading file: {e}")
        
        content = response['Body'].read()
        
        if not content:
            raise Exception(f"File is empty: {s3_key}")
        
        # Try to decode as UTF-8 first
        try:
            decoded_content = content.decode('utf-8')
        except UnicodeDecodeError:
            # Try other common encodings
            encodings = ['latin-1', 'cp1252', 'iso-8859-1']
            decoded_content = None
            
            for encoding in encodings:
                try:
                    decoded_content = content.decode(encoding)
                    logger.info(f"Successfully decoded with {encoding}")
                    break
                except UnicodeDecodeError:
                    continue
            
            if decoded_content is None:
                # Last resort - use chardet if available
                try:
                    import chardet
                    detected = chardet.detect(content)
                    encoding = detected.get('encoding', 'utf-8')
                    decoded_content = content.decode(encoding, errors='replace')
                    logger.warning(f"Used chardet detection: {encoding}")
                except ImportError:
                    # Fallback with error replacement
                    decoded_content = content.decode('utf-8', errors='replace')
                    logger.warning("Used UTF-8 with error replacement")
        
        if not decoded_content or not decoded_content.strip():
            raise Exception(f"No readable content in file: {s3_key}")
        
        logger.info(f"Successfully read {len(decoded_content)} characters from {s3_key}")
        return decoded_content
        
    except Exception as e:
        logger.error(f"Error reading S3 file {s3_key}: {e}")
        raise Exception(f"Could not read input file: {str(e)}")

async def upload_outputs_to_s3(job_id: str) -> Dict:
    """CORRECTED: Upload generated outputs to S3 with proper validation to prevent NoneType errors"""
    outputs = {}
    
    if not validate_s3_configuration():
        logger.error("S3 not configured, cannot upload outputs")
        return outputs
    
    try:
        file_patterns = [
            ("minutes", "outputs/meeting_minutes_*.txt"),
            ("audio_script", "outputs/meeting_audio_script_*.txt"),
            ("audio_fallback", "outputs/meeting_audio_fallback_*.txt"),  # Include fallback files
            ("minutes_fallback", "outputs/meeting_minutes_fallback_*.txt"),
            ("report_fallback", "outputs/meeting_report_fallback_*.txt"),
            ("audio", "outputs/meeting_audio_*.mp3"),
            ("report", "outputs/meeting_report_*.pdf")
        ]
        
        current_time = time.time()
        
        for file_type, pattern in file_patterns:
            try:
                files = glob.glob(pattern)
                if not files:
                    logger.info(f"No files found for pattern: {pattern}")
                    continue
                
                # Get the most recent file
                latest_file = max(files, key=os.path.getctime)
                
                # CRITICAL FIX: Comprehensive file validation before reading
                if not os.path.exists(latest_file):
                    logger.warning(f"File does not exist: {latest_file}")
                    continue
                    
                try:
                    file_size = os.path.getsize(latest_file)
                except OSError as e:
                    logger.warning(f"Cannot get file size for {latest_file}: {e}")
                    continue
                    
                if file_size == 0:
                    logger.warning(f"File is empty: {latest_file}")
                    continue
                
                # Check if file is recent (within last 10 minutes)
                try:
                    file_age = current_time - os.path.getctime(latest_file)
                except OSError as e:
                    logger.warning(f"Cannot get creation time for {latest_file}: {e}")
                    continue
                    
                if file_age > 600:  # 10 minutes
                    logger.info(f"Old file skipped: {latest_file} (age: {file_age:.1f}s)")
                    continue
                
                filename = os.path.basename(latest_file)
                s3_key = f"outputs/{job_id}/{filename}"
                
                logger.info(f"Processing {file_type} file: {latest_file} (size: {file_size} bytes)")
                
                # CRITICAL FIX: Enhanced content reading with explicit validation
                content = None
                content_valid = False
                
                try:
                    if filename.endswith(('.pdf', '.mp3')):
                        # Binary files
                        logger.info(f"Reading binary file: {latest_file}")
                        with open(latest_file, 'rb') as f:
                            content = f.read()
                        
                        # Explicit validation for binary content
                        if content is not None and isinstance(content, bytes) and len(content) > 0:
                            content_valid = True
                            logger.info(f"Binary file read successfully: {len(content)} bytes")
                        else:
                            logger.warning(f"Binary file read failed or empty: {latest_file}")
                            continue
                    else:
                        # Text files - CRITICAL FIX
                        logger.info(f"Reading text file: {latest_file}")
                        try:
                            with open(latest_file, 'r', encoding='utf-8') as f:
                                content_str = f.read()
                        except UnicodeDecodeError:
                            # Try alternate encoding
                            with open(latest_file, 'r', encoding='latin-1') as f:
                                content_str = f.read()
                        
                        # EXPLICIT VALIDATION - this was the source of NoneType errors
                        if content_str is not None:
                            # Check if content is meaningful (not just whitespace)
                            if len(content_str.strip()) > 0:
                                content = content_str.encode('utf-8')
                                content_valid = True
                                logger.info(f"Text file read successfully: {len(content_str)} characters")
                            else:
                                logger.warning(f"Text file contains only whitespace: {latest_file}")
                                continue
                        else:
                            logger.warning(f"Text file read returned None: {latest_file}")
                            continue
                            
                except Exception as read_error:
                    logger.error(f"Failed to read file {latest_file}: {read_error}")
                    continue
                
                # FINAL VALIDATION before upload
                if not content_valid or content is None:
                    logger.warning(f"Content validation failed for {latest_file}")
                    continue
                
                # Additional safety check
                if isinstance(content, bytes) and len(content) == 0:
                    logger.warning(f"Content is empty bytes for {latest_file}")
                    continue
                
                if isinstance(content, str) and len(content.strip()) == 0:
                    logger.warning(f"Content is empty string for {latest_file}")
                    continue
                
                # Upload to S3 with metadata
                try:
                    extra_args = {
                        'Metadata': {
                            'job-id': job_id,
                            'file-type': file_type,
                            'original-filename': filename,
                            'file-size': str(file_size),
                            'upload-timestamp': str(int(time.time()))
                        },
                        'ContentType': get_content_type(filename)
                    }
                    
                    logger.info(f"Uploading to S3: {s3_key} ({len(content)} bytes)")
                    s3_client.put_object(
                        Bucket=OUTPUT_BUCKET,
                        Key=s3_key,
                        Body=content,
                        **extra_args
                    )
                    
                    # Verify upload
                    try:
                        head_response = s3_client.head_object(Bucket=OUTPUT_BUCKET, Key=s3_key)
                        uploaded_size = head_response['ContentLength']
                        
                        if uploaded_size != len(content):
                            logger.error(f"Size mismatch for {s3_key}: expected {len(content)}, got {uploaded_size}")
                            continue
                        else:
                            logger.info(f"Upload verified: {s3_key} ({uploaded_size} bytes)")
                            
                    except Exception as verify_error:
                        logger.error(f"Upload verification failed for {s3_key}: {verify_error}")
                        continue
                    
                    # Generate download URL
                    download_url = generate_presigned_download_url(s3_key, expires_in=86400)  # 24 hours
                    
                    if download_url:
                        outputs[file_type] = {
                            "filename": filename,
                            "s3_key": s3_key,
                            "size_bytes": file_size,
                            "download_url": download_url,
                            "upload_timestamp": int(time.time())
                        }
                        
                        logger.info(f"Successfully uploaded and cataloged {file_type}: {filename}")
                        
                        # Clean up local file after successful upload
                        try:
                            os.remove(latest_file)
                            logger.info(f"Cleaned up local file: {latest_file}")
                        except Exception as cleanup_error:
                            logger.warning(f"Could not clean up {latest_file}: {cleanup_error}")
                    else:
                        logger.error(f"Could not generate download URL for {s3_key}")
                        
                except Exception as upload_error:
                    logger.error(f"Upload failed for {latest_file}: {upload_error}")
                    
            except Exception as pattern_error:
                logger.error(f"Error processing pattern {pattern}: {pattern_error}")
        
        logger.info(f"Upload completed for job {job_id}: {len(outputs)} files uploaded successfully")
        return outputs
        
    except Exception as e:
        logger.error(f"General upload error for job {job_id}: {e}")
        return outputs

def list_s3_objects(bucket: str, prefix: str = "") -> List[Dict]:
    """List objects in S3 bucket with error handling"""
    try:
        if not s3_client:
            return []
        
        response = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix,
            MaxKeys=1000
        )
        
        objects = []
        if 'Contents' in response:
            for obj in response['Contents']:
                objects.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                    'etag': obj['ETag'].strip('"')
                })
        
        return objects
        
    except Exception as e:
        logger.error(f"Error listing S3 objects: {e}")
        return []

def delete_s3_object(bucket: str, s3_key: str) -> bool:
    """Delete object from S3 with error handling"""
    try:
        if not s3_client:
            return False
        
        s3_client.delete_object(Bucket=bucket, Key=s3_key)
        logger.info(f"Deleted S3 object: {s3_key}")
        return True
        
    except Exception as e:
        logger.error(f"Error deleting S3 object {s3_key}: {e}")
        return False

def get_content_type(filename: str) -> str:
    """Get content type based on file extension with enhanced mapping"""
    if not filename:
        return 'application/octet-stream'
    
    ext = os.path.splitext(filename)[1].lower()
    content_types = {
        '.pdf': 'application/pdf',
        '.mp3': 'audio/mpeg',
        '.wav': 'audio/wav',
        '.m4a': 'audio/mp4',
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.md': 'text/markdown',
        '.txt': 'text/plain',
        '.json': 'application/json',
        '.csv': 'text/csv',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    }
    return content_types.get(ext, 'application/octet-stream')

def cleanup_old_s3_objects(bucket: str, prefix: str, max_age_hours: int = 24) -> int:
    """Clean up old S3 objects"""
    try:
        if not s3_client:
            return 0
        
        objects = list_s3_objects(bucket, prefix)
        current_time = time.time()
        deleted_count = 0
        
        for obj in objects:
            try:
                from datetime import datetime
                last_modified = datetime.fromisoformat(obj['last_modified'].replace('Z', '+00:00'))
                age_hours = (current_time - last_modified.timestamp()) / 3600
                
                if age_hours > max_age_hours:
                    if delete_s3_object(bucket, obj['key']):
                        deleted_count += 1
                        
            except Exception as e:
                logger.warning(f"Error processing object {obj['key']}: {e}")
        
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old S3 objects from {bucket}/{prefix}")
            
        return deleted_count
        
    except Exception as e:
        logger.error(f"S3 cleanup error: {e}")
        return 0

# Health check function
def s3_health_check() -> Dict:
    """Comprehensive S3 health check"""
    health = {
        "s3_client_initialized": bool(s3_client),
        "input_bucket_configured": bool(INPUT_BUCKET),
        "output_bucket_configured": bool(OUTPUT_BUCKET),
        "buckets_accessible": False,
        "credentials_valid": False
    }
    
    if s3_client:
        try:
            # Test credentials
            s3_client.list_buckets()
            health["credentials_valid"] = True
            
            # Test bucket access
            if INPUT_BUCKET and OUTPUT_BUCKET:
                try:
                    s3_client.head_bucket(Bucket=INPUT_BUCKET)
                    s3_client.head_bucket(Bucket=OUTPUT_BUCKET)
                    health["buckets_accessible"] = True
                except Exception as bucket_error:
                    health["bucket_error"] = str(bucket_error)
                    
        except Exception as creds_error:
            health["credentials_error"] = str(creds_error)
    
    return health