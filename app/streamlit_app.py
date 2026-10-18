import streamlit as st
import requests
import time
import os
import socket

st.set_page_config(
    page_title="Meeting Minutes Generator",
    layout="centered"
)

st.markdown("""
<style>
    .stApp {
        background: #0f0f0f;
        color: #e0e0e0;
    }
    
    .main-title {
        text-align: center;
        color: #ffffff;
        font-size: 3rem;
        font-weight: 200;
        margin: 4rem 0 1rem 0;
        letter-spacing: 3px;
    }
    
    .subtitle {
        text-align: center;
        color: #888888;
        font-size: 1.1rem;
        margin-bottom: 4rem;
        font-weight: 300;
    }
    
    .upload-area {
        border: 2px dashed #444444;
        border-radius: 12px;
        padding: 3rem;
        text-align: center;
        background: #111111;
        margin: 2rem 0;
        transition: all 0.3s;
    }
    
    .upload-area:hover {
        border-color: #666666;
        background: #151515;
    }
    
    .features-row {
        display: flex;
        justify-content: space-between;
        gap: 1rem;
        margin: 3rem 0;
        flex-wrap: wrap;
    }
    
    .feature-item {
        background: linear-gradient(135deg, #1a1a1a 0%, #222222 100%);
        padding: 1rem;
        border-radius: 8px;
        text-align: center;
        border: 1px solid #333333;
        transition: all 0.3s;
        flex: 1;
        min-width: 180px;
        max-width: 220px;
    }
    
    .feature-item:hover {
        transform: translateY(-3px);
        border-color: #555555;
        box-shadow: 0 8px 25px rgba(0,0,0,0.4);
    }
    
    .feature-title {
        color: #ffffff;
        font-size: 0.95rem;
        font-weight: 500;
        margin-bottom: 0.3rem;
    }
    
    .feature-desc {
        color: #aaaaaa;
        font-size: 0.8rem;
        line-height: 1.3;
    }
    
    .results-content {
        background: #1a1a1a;
        border: 1px solid #333333;
        border-radius: 12px;
        padding: 2rem;
        margin: 2rem 0;
    }
    
    .file-item {
        background: #222222;
        border: 1px solid #444444;
        border-radius: 8px;
        padding: 1rem;
        margin: 0.5rem 0;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    
    .file-name {
        color: #ffffff;
        font-weight: 500;
    }
    
    .file-type {
        color: #888888;
        font-size: 0.9rem;
    }
    
    .download-link {
        background: #333333;
        color: #ffffff;
        padding: 0.5rem 1rem;
        border-radius: 6px;
        text-decoration: none;
        font-size: 0.9rem;
        transition: all 0.3s;
    }
    
    .download-link:hover {
        background: #444444;
        color: #ffffff;
    }
    
    .status-success {
        background: #1a2e1a;
        color: #90ee90;
        padding: 1rem;
        border-radius: 8px;
        border-left: 4px solid #90ee90;
        margin: 1rem 0;
    }
    
    .status-error {
        background: #2e1a1a;
        color: #ff6b6b;
        padding: 1rem;
        border-radius: 8px;
        border-left: 4px solid #ff6b6b;
        margin: 1rem 0;
    }
    
    .status-processing {
        background: #1a1a2e;
        color: #87ceeb;
        padding: 1rem;
        border-radius: 8px;
        border-left: 4px solid #87ceeb;
        margin: 1rem 0;
    }
    
    .stButton > button {
        background: linear-gradient(135deg, #333333 0%, #444444 100%);
        color: #ffffff;
        border: 1px solid #555555;
        padding: 1rem 2rem;
        border-radius: 8px;
        font-weight: 400;
        transition: all 0.3s;
        width: 100%;
        font-size: 1rem;
    }
    
    .stButton > button:hover {
        background: linear-gradient(135deg, #444444 0%, #555555 100%);
        transform: translateY(-2px);
        box-shadow: 0 5px 15px rgba(0,0,0,0.3);
    }
    
    .stTextInput > div > div > input {
        background: #1a1a1a;
        color: #e0e0e0;
        border: 1px solid #333333;
        border-radius: 8px;
        padding: 0.75rem;
    }
    
    .stTextArea > div > div > textarea {
        background: #1a1a1a;
        color: #e0e0e0;
        border: 1px solid #333333;
        border-radius: 8px;
        padding: 0.75rem;
    }
    
    .stRadio > div {
        background: transparent;
    }
    
    .stRadio > div > label {
        color: #e0e0e0;
    }
    
    h2, h3 {
        color: #ffffff;
        font-weight: 300;
    }
    
    .main-container {
        max-width: 900px;
        margin: 0 auto;
        padding: 0 2rem;
    }
</style>
""", unsafe_allow_html=True)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")

if 'job_id' not in st.session_state:
    st.session_state.job_id = None
if 'processing' not in st.session_state:
    st.session_state.processing = False
if 'results' not in st.session_state:
    st.session_state.results = None

def check_backend_health():
    try:
        response = requests.get(f"{BACKEND_URL}/health", timeout=3)
        return response.status_code == 200
    except requests.exceptions.Timeout:
        # Backend is running but slow - check if port is open
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', 8000))
            sock.close()
            return result == 0
        except:
            return False
    except Exception as e:
        st.error(f"Backend connection failed: {str(e)}")
        return False

def submit_meeting(content: str, title: str):
    try:
        response = requests.post(
            f"{BACKEND_URL}/process-s3-meeting",
            json={"content": content, "meeting_title": title},
            timeout=30
        )
        if response.status_code == 200:
            return response.json().get("job_id")
        else:
            st.error(f"Submission failed: {response.text}")
            return None
    except Exception as e:
        st.error(f"Error: {str(e)}")
        return None

def check_job_status(job_id: str):
    try:
        response = requests.get(f"{BACKEND_URL}/job-status/{job_id}", timeout=10)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        st.error(f"Status check failed: {str(e)}")
        return None

def reset_session():
    st.session_state.job_id = None
    st.session_state.processing = False
    st.session_state.results = None
    st.rerun()

# Header
st.markdown('<div class="main-container">', unsafe_allow_html=True)
st.markdown('<h1 class="main-title">Meeting Minutes Generator</h1>', unsafe_allow_html=True)
st.markdown('<p class="subtitle">Transform meeting transcripts into actionable insights</p>', unsafe_allow_html=True)

# Backend check
st.sidebar.write(f"Backend URL: {BACKEND_URL}")
if not check_backend_health():
    st.markdown('<div class="status-error">Backend service unavailable</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="status-error">Trying to connect to: {BACKEND_URL}</div>', unsafe_allow_html=True)
    if st.button("Retry Connection"):
        st.rerun()
    st.stop()
else:
    st.sidebar.success("Backend connected")

# Main flow
if st.session_state.processing:
    st.markdown("## Processing Meeting")
    
    st.markdown(f'<div class="status-processing">Job ID: {st.session_state.job_id}</div>', unsafe_allow_html=True)
    
    progress_bar = st.progress(0)
    status_container = st.empty()
    
    for i in range(100):
        if st.session_state.job_id:
            status = check_job_status(st.session_state.job_id)
            if status:
                if status.get("status") == "completed":
                    st.session_state.results = status
                    st.session_state.processing = False
                    st.rerun()
                elif status.get("status") == "failed":
                    st.markdown('<div class="status-error">Processing failed</div>', unsafe_allow_html=True)
                    if st.button("Try Again"):
                        reset_session()
                    st.stop()
                else:
                    status_container.markdown(f'<div class="status-processing">Status: {status.get("status", "processing").title()}</div>', unsafe_allow_html=True)
        
        progress_bar.progress((i + 1) / 100)
        time.sleep(2)
    
    if st.button("Cancel"):
        reset_session()

elif st.session_state.results:
    st.markdown("## Meeting Minutes Generated")
    
    st.markdown('<div class="status-success">Processing completed successfully</div>', unsafe_allow_html=True)
    
    results = st.session_state.results
    if "output_files" in results and results["output_files"]:
        st.markdown('<div class="results-content">', unsafe_allow_html=True)
        st.markdown("### Your Generated Files")
        
        for file_info in results["output_files"]:
            file_name = file_info.get("filename", "Unknown")
            file_url = file_info.get("download_url")
            
            # Determine file type
            if file_name.endswith('.pdf'):
                file_type = "PDF Report"
            elif file_name.endswith('.mp3') or file_name.endswith('.wav'):
                file_type = "Audio Summary"
            elif file_name.endswith('.md'):
                file_type = "Markdown"
            else:
                file_type = "Document"
            
            if file_url:
                st.markdown(f'''
                <div class="file-item">
                    <div>
                        <div class="file-name">{file_name}</div>
                        <div class="file-type">{file_type}</div>
                    </div>
                    <a href="{file_url}" class="download-link" target="_blank">Download</a>
                </div>
                ''', unsafe_allow_html=True)
            else:
                st.markdown(f'''
                <div class="file-item">
                    <div>
                        <div class="file-name">{file_name}</div>
                        <div class="file-type">{file_type} - Processing</div>
                    </div>
                </div>
                ''', unsafe_allow_html=True)
        
        st.markdown('</div>', unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Process Another Meeting"):
            reset_session()
    with col2:
        if st.button("Refresh Results"):
            if st.session_state.job_id:
                status = check_job_status(st.session_state.job_id)
                if status:
                    st.session_state.results = status
                    st.rerun()

else:
    # Input form
    meeting_title = st.text_input("Meeting Title", value="Meeting Minutes")
    
    input_method = st.radio("Input Method", ["Upload File", "Paste Text"])
    
    content = ""
    
    if input_method == "Upload File":
        st.markdown('<div class="upload-area">Drop your meeting transcript file here</div>', unsafe_allow_html=True)
        uploaded_file = st.file_uploader("Choose file", type=['txt', 'md', 'docx'], label_visibility="collapsed")
        
        if uploaded_file:
            content = uploaded_file.read().decode('utf-8')
            st.markdown(f'<div class="status-success">File loaded: {uploaded_file.name}</div>', unsafe_allow_html=True)
    
    else:
        st.markdown('<div class="upload-area">', unsafe_allow_html=True)
        content = st.text_area("Meeting Transcript", height=200, placeholder="Paste your meeting transcript here...", label_visibility="collapsed")
        st.markdown('</div>', unsafe_allow_html=True)
    
    if st.button("Generate Minutes", type="primary"):
        if not content.strip():
            st.error("Please provide meeting content")
        elif not meeting_title.strip():
            st.error("Please enter a meeting title")
        else:
            with st.spinner("Starting processing..."):
                job_id = submit_meeting(content, meeting_title)
                if job_id:
                    st.session_state.job_id = job_id
                    st.session_state.processing = True
                    st.success(f"Processing started: {job_id}")
                    time.sleep(1)
                    st.rerun()
    
    # Features in one line
    st.markdown("""
    <div class="features-row">
        <div class="feature-item">
            <div class="feature-title">Professional Minutes</div>
            <div class="feature-desc">Structured documentation with key decisions</div>
        </div>
        <div class="feature-item">
            <div class="feature-title">Executive Summary</div>
            <div class="feature-desc">Concise overview of critical points</div>
        </div>
        <div class="feature-item">
            <div class="feature-title">Action Items</div>
            <div class="feature-desc">Clear tasks with owners and deadlines</div>
        </div>
        <div class="feature-item">
            <div class="feature-title">Audio Summary</div>
            <div class="feature-desc">AI-generated voice narration</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)