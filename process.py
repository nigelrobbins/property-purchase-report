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
import yaml

# Define directories
CONFIG_DIR = "config"
LOCAL_SEARCH_DIR = os.path.join(CONFIG_DIR, "local-search")
PATTERNS_FILE = os.path.join(LOCAL_SEARCH_DIR, "patterns.txt")
PATTERNS_MANDATORY_FILE = os.path.join(LOCAL_SEARCH_DIR, "patterns_mandatory.txt")
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

def load_patterns():
    """Load regex patterns from config file."""
    if os.path.exists(PATTERNS_FILE):
        with open(PATTERNS_FILE, "r") as f:
            return [line.strip() for line in f.readlines() if line.strip()]
    return []

def load_mandatory_patterns():
    """Load patterns that should always be added to the document even if no match is found."""
    if os.path.exists(PATTERNS_MANDATORY_FILE):
        with open(PATTERNS_MANDATORY_FILE, "r") as f:
            return [line.strip() for line in f.readlines() if line.strip()]
    return []

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

def extract_text_from_docx(docx_path):
    """Extract text from a Word document."""
    doc = Document(docx_path)
    return "\n".join([para.text for para in doc.paragraphs])

def extract_matching_sections(text, patterns):
    """Extract relevant sections based on multiple regex patterns."""
    matched_sections = []
    for pattern in patterns:
        print(f"🔍 Searching for pattern: {pattern}")
        matches = re.findall(pattern, text, re.DOTALL)  # Find all matching sections
        matched_sections.extend(matches)
    
    return matched_sections

def add_matched_sections(doc, file_type, file_name, extracted_text, patterns, label):
    """
    Extracts and adds matched sections from the text to the document.
    Returns True if any sections were found.
    """
    matched_sections = extract_matching_sections(extracted_text, patterns)

    if matched_sections:
        doc.add_paragraph(f"Source ({file_type}): {file_name}", style="Heading 2")

        for section in matched_sections:
            if "None" in section:
                print(f"⚠️ Skipping section due to 'None' content: {section[:30]}...")
                continue  # Skip adding this section if it contains 'None'

            print(f"✅ Adding section ({label}): {section[:30]}...")
            doc.add_paragraph(section)
            doc.add_page_break()

        return True  # Sections were found

    return False  # No sections matched

# Load YAML configuration
def load_yaml(yaml_path):
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["groups"]

# Identify question group based on document content
def identify_group(text, groups):
    for group in groups:
        if group["identifier"] in text:
            return group
    return None  # No matching group found

# Extract matching text based on regex
def extract_matching_text(text, pattern):
    matches = re.findall(pattern, text, re.DOTALL)
    return "\n\n".join(matches) if matches else None

def process_zip(zip_path, output_docx, yaml_path):
    """Extract and process only relevant sections from documents that contain filter text."""
    output_folder = "unzipped_files"
    os.makedirs(output_folder, exist_ok=True)
    groups = load_yaml(yaml_path)
    doc = Document()

    if not os.path.exists(zip_path):
        print(f"❌ ERROR: ZIP file does not exist: {zip_path}")
        return

    print(f"📂 Unzipping: {zip_path}")
    unzip_start = time.time()
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(output_folder)
    unzip_end = time.time()
    print(f"⏱ Unzipping took {unzip_end - unzip_start:.4f} seconds")

    doc.add_paragraph(f"ZIP File: {os.path.basename(zip_path)}", style="Heading 1")

    patterns = load_patterns()
    mandatory_patterns = load_mandatory_patterns()
    filter_text = load_filter_text()
    print(f"🔍 Loaded {len(patterns)} patterns and filter text: {filter_text}")
    found_relevant_doc = False

    for file_name in sorted(os.listdir(output_folder)):
        file_path = os.path.join(output_folder, file_name)

        print(f"📄 Processing {file_name}...")
        process_start = time.time()

        if file_name.endswith(".pdf"):
            extracted_text = extract_text_from_pdf(file_path)
            file_type = "PDF"
        elif file_name.endswith(".docx"):
            extracted_text = extract_text_from_docx(file_path)
            file_type = "Word Document"
        else:
            continue
        group = identify_group(extracted_text, groups)

        if not group:
            print(f"⚠️ No matching group found for {file_name}, skipping.")
            continue
        
        doc.add_paragraph(f"📂 Processing: {file_name}", style="Heading 1")
        doc.add_paragraph(f"📄 Document identified as: {group['name']}", style="Heading 2")

        for question in group["questions"]:
            doc.add_paragraph(f"🔍 Checking section: {question['section']}", style="Heading 3")

            if question["search_pattern"] in extracted_text:
                doc.add_paragraph(question["message_found"], style="Normal")

                if question["extract_text"]:
                    extracted_section = extract_matching_text(extracted_text, question["extract_pattern"])
                    if extracted_section:
                        doc.add_paragraph("📌 Extracted Content:", style="Italic")
                        doc.add_paragraph(extracted_section, style="Normal")
                    else:
                        doc.add_paragraph("⚠️ No matching content found.", style="Normal")
            else:
                doc.add_paragraph(question["message_not_found"], style="Normal")

            doc.add_paragraph("")  # Spacing between questions

        doc.add_page_break()

    # Save output Word document
    os.makedirs(os.path.dirname(output_docx), exist_ok=True)
    doc.save(output_docx)


# Automatically find ZIP file and process it
input_folder = "input_files"
zip_file_path = None
yaml_config = "config.yaml"

for file in os.listdir(input_folder):
    if file.endswith(".zip"):
        zip_file_path = os.path.join(input_folder, file)
        break

output_file = "output_files/processed_doc.docx"
if zip_file_path:
    print(f"📂 Found ZIP file: {zip_file_path}")
    process_zip(zip_file_path, output_file, yaml_config)
else:
    print("❌ No ZIP file found in 'input_files' folder.")
