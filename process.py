import os
import zipfile
import pdfplumber
import re
import time
from docx import Document
import pytesseract
from pdf2image import convert_from_path
from PIL import Image
import subprocess

# Define directories
CONFIG_DIR = "config"
LOCAL_SEARCH_DIR = os.path.join(CONFIG_DIR, "local-search")
PATTERNS_FILE = os.path.join(LOCAL_SEARCH_DIR, "patterns.txt")
MANDATORY_PATTERNS_FILE = os.path.join(LOCAL_SEARCH_DIR, "mandatory_patterns.txt")
MESSAGE_IF_EXISTS = os.path.join(LOCAL_SEARCH_DIR, "message_if_exists.txt")
MESSAGE_IF_NOT_EXISTS = os.path.join(LOCAL_SEARCH_DIR, "message_if_not_exists.txt")
FILTER_TEXT_FILE = os.path.join(LOCAL_SEARCH_DIR, "filter_text.txt")

# Ensure required directories exist
os.makedirs(LOCAL_SEARCH_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

# Ensure message files exist with default messages if missing
if not os.path.exists(MESSAGE_IF_EXISTS):
    with open(MESSAGE_IF_EXISTS, "w") as f:
        f.write("This document contains relevant information.")

if not os.path.exists(MESSAGE_IF_NOT_EXISTS):
    with open(MESSAGE_IF_NOT_EXISTS, "w") as f:
        f.write("No relevant information found in this document.")

# Ensure patterns file exists with default patterns
if not os.path.exists(PATTERNS_FILE):
    with open(PATTERNS_FILE, "w") as f:
        f.write("REPLIES TO STANDARD ENQUIRIES.*?(?=\n[A-Z ]+\n|\Z)\n")

# Ensure filter text file exists with the default keyword
if not os.path.exists(FILTER_TEXT_FILE):
    with open(FILTER_TEXT_FILE, "w") as f:
        f.write("REPLIES TO STANDARD ENQUIRIES")

def timed_function(func):
    """Decorator to measure function execution time."""
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        print(f"⏱ {func.__name__} took {end_time - start_time:.4f} seconds")
        return result
    return wrapper

@timed_function
def load_patterns():
    """Load regex patterns from config file."""
    if os.path.exists(PATTERNS_FILE):
        with open(PATTERNS_FILE, "r") as f:
            return [line.strip() for line in f.readlines() if line.strip()]
    return []

@timed_function
def load_mandatory_patterns():
    """Load patterns that should always be added to the document even if no match is found."""
    if os.path.exists(MANDATORY_PATTERNS_FILE):
        with open(MANDATORY_PATTERNS_FILE, "r") as f:
            return [line.strip() for line in f.readlines() if line.strip()]
    return []

@timed_function
def load_filter_text():
    """Load the filter text from the config file."""
    if os.path.exists(FILTER_TEXT_FILE):
        with open(FILTER_TEXT_FILE, "r") as f:
            return f.read().strip()
    return ""

@timed_function
def extract_text_from_pdf(pdf_path):
    """Extract text from a PDF, using pdftotext first, then pdfplumber, then OCR if needed."""

    # Try using pdftotext first
    result = subprocess.run(['pdftotext', pdf_path, '-'], capture_output=True, text=True)
    text = result.stdout.strip()

    if text:
        print(f"✅ Extracted text using pdftotext: {text[:50]}...")
        return text  # If pdftotext works, return immediately

    print("⚠️ pdftotext failed, trying pdfplumber...")

    # Fallback to pdfplumber
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"

    if text:
        print(f"✅ Extracted text using pdfplumber: {text[:50]}...")
        return text.strip()  # If pdfplumber works, return immediately

    print("⚠️ pdfplumber failed, performing OCR...")

    # Final fallback: Use OCR (slow)
    images = convert_from_path(pdf_path)
    for img in images:
        text += pytesseract.image_to_string(img) + "\n"

    print(f"✅ Extracted text using OCR: {text[:50]}...")
    return text.strip()

@timed_function
def extract_text_from_docx(docx_path):
    """Extract text from a Word document."""
    doc = Document(docx_path)
    return "\n".join([para.text for para in doc.paragraphs])

@timed_function
def extract_matching_sections(text, patterns):
    """Extract relevant sections based on multiple regex patterns."""
    matched_sections = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.DOTALL)  # Find all matching sections
        matched_sections.extend(matches)
    
    return matched_sections

@timed_function
def process_zip(zip_path, output_docx):
    """Extract and process only relevant sections from documents."""
    output_folder = "unzipped_files"
    os.makedirs(output_folder, exist_ok=True)

    if not os.path.exists(zip_path):
        print(f"❌ ERROR: ZIP file does not exist: {zip_path}")
        return

    print(f"📂 Unzipping: {zip_path}")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(output_folder)

    doc = Document()
    doc.add_paragraph(f"ZIP File: {os.path.basename(zip_path)}", style="Heading 1")

    patterns = load_patterns()
    mandatory_patterns = load_mandatory_patterns()
    found_relevant_doc = False

    for file_name in sorted(os.listdir(output_folder)):
        file_path = os.path.join(output_folder, file_name)

        if file_name.endswith(".pdf"):
            extracted_text = extract_text_from_pdf(file_path)
            file_type = "PDF"
        elif file_name.endswith(".docx"):
            extracted_text = extract_text_from_docx(file_path)
            file_type = "Word Document"
        else:
            continue

        doc.add_paragraph(f"Source ({file_type}): {file_name}", style="Heading 2")

        for pattern in patterns:
            matches = re.findall(pattern, extracted_text, re.DOTALL)

            if matches:
                found_relevant_doc = True
                for section in matches:
                    doc.add_paragraph(section)
                    doc.add_page_break()
            elif pattern in mandatory_patterns:
                # If no match, but pattern is mandatory, add a placeholder or custom message
                found_relevant_doc = True
                doc.add_paragraph(f"🔹 Section missing for mandatory pattern: {pattern}")

    # Load appropriate message
    message_file = MESSAGE_IF_EXISTS if found_relevant_doc else MESSAGE_IF_NOT_EXISTS
    with open(message_file, "r") as f:
        extra_message = f.read().strip()
        doc.add_paragraph(extra_message).italic = True

    os.makedirs(os.path.dirname(output_docx), exist_ok=True)
    doc.save(output_docx)
    print(f"✅ Word document saved: {os.path.abspath(output_docx)}")
    print(f"⏱ Saving document took {save_end - save_start:.4f} seconds")

# Automatically find ZIP file and process it
input_folder = "input_files"
zip_file_path = None

for file in os.listdir(input_folder):
    if file.endswith(".zip"):
        zip_file_path = os.path.join(input_folder, file)
        break

output_file = "output_files/processed_doc.docx"
if zip_file_path:
    print(f"📂 Found ZIP file: {zip_file_path}")
    process_zip(zip_file_path, output_file)
else:
    print("❌ No ZIP file found in 'input_files' folder.")
