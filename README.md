# AI Document Organizer

Automatically classify documents from an input folder and sort them into
category sub-folders — with keyword scoring, OCR support, and an optional
LLM fallback.

---

## Features

| Capability | Detail |
|---|---|
| **File types** | PDF, DOCX, XLSX, TXT, PNG, JPG, JPEG, TIFF |
| **OCR** | Scanned PDFs and images via Tesseract |
| **Keyword scoring** | Fuzzy matching with rapidfuzz |
| **LLM fallback** | Claude claude-sonnet-4-6 (optional, configurable) |
| **Report** | CSV summary of every processed file |
| **Logging** | Rotating file log + console output |
| **Dry-run** | Preview without touching files |
| **Move or copy** | Configurable per run |

---

## Project Structure

```
document_organizer/
├── main.py           # Entry point & pipeline orchestration
├── config.py         # Configuration loader (JSON → typed dataclass)
├── config.json       # Example configuration
├── extractor.py      # Multi-format text extraction (PDF/DOCX/XLSX/TXT/Image)
├── classifier.py     # Keyword + fuzzy scoring; LLM fallback
├── organizer.py      # Move/copy files into categorised folders
├── logger.py         # Rotating file + console logging
├── report.py         # CSV report generator
├── utils.py          # Shared helpers (hash, truncate, sanitise, …)
├── requirements.txt
├── README.md
└── tests/
    ├── conftest.py
    ├── test_classifier.py
    ├── test_config.py
    ├── test_organizer.py
    └── test_utils.py
```

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Tesseract OCR binary (for OCR support)

```bash
# macOS
brew install tesseract

# Ubuntu / Debian
sudo apt-get install tesseract-ocr

# Windows — download installer from:
# https://github.com/UB-Mannheim/tesseract/wiki
```

### 2. Install Python dependencies

```bash
cd document_organizer
pip install -r requirements.txt
```

### 3. Configure

Edit `config.json` to set your input/output folders and categories:

```json
{
  "input_folder": "./my_documents",
  "output_folder": "./sorted",
  "ocr_enabled": true,
  "llm_enabled": false,
  "confidence_threshold": 0.10,
  "copy_mode": false,
  "log_level": "INFO",
  "categories": {
    "Bank Statement": ["beginning balance", "ending balance", "account summary"],
    "Vendor Invoice": ["vendor invoice", "bill to", "invoice number"]
  }
}
```

### 4. Run

```bash
# Use config.json for all settings
python main.py

# Override input/output via CLI
python main.py --input ./inbox --output ./sorted --config config.json

# Preview without moving files
python main.py --dry-run
```

---

## Configuration Reference

| Key | Type | Default | Description |
|---|---|---|---|
| `input_folder` | string | — | Source directory path |
| `output_folder` | string | — | Destination root path |
| `categories` | object | — | `{"Category Name": ["keyword", …]}` |
| `ocr_enabled` | bool | `true` | Run OCR on images and scanned PDFs |
| `llm_enabled` | bool | `false` | Use LLM for low-confidence docs |
| `confidence_threshold` | float | `0.10` | Minimum score to assign a category |
| `copy_mode` | bool | `false` | Copy instead of move |
| `log_level` | string | `"INFO"` | DEBUG / INFO / WARNING / ERROR |

### Category format

Two formats are supported. **Nested** (recommended):

```json
{
  "categories": {
    "Bank Statement": ["beginning balance", "ending balance"]
  }
}
```

**Flat** (legacy / simple):

```json
{
  "Bank Statement": ["beginning balance", "ending balance"]
}
```

---

## Output

### Folder structure

```
sorted/
├── Bank Statement/
│   └── march_2024_statement.pdf
├── Vendor Invoice/
│   └── acme_inv_001.pdf
├── Others/
│   └── unknown_doc.png
└── classification_report.csv
```

### CSV Report (`classification_report.csv`)

```
file_name,original_path,destination_folder,category,confidence,processing_time,error
march_2024_statement.pdf,/inbox/march_2024_statement.pdf,/sorted/Bank Statement,Bank Statement,0.75,0.312,
acme_inv_001.pdf,/inbox/acme_inv_001.pdf,/sorted/Vendor Invoice,Vendor Invoice,0.5,0.201,
```

### Log file (`logs/document_organizer.log`)

```
2024-03-15 10:23:01 | INFO     | main | === AI Document Organizer ===
2024-03-15 10:23:01 | INFO     | main | Input  : /inbox
2024-03-15 10:23:01 | INFO     | main | Found 42 file(s) to process.
2024-03-15 10:23:02 | INFO     | main | [march_2024_statement.pdf] → Bank Statement (confidence=0.75, text_len=3214)
```

---

## Running Tests

```bash
pytest tests/ -v
```

With coverage:

```bash
pytest tests/ -v --cov=. --cov-report=term-missing
```

---

## LLM Fallback

Set `"llm_enabled": true` in `config.json` and provide your Anthropic API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Documents whose keyword confidence score falls below `confidence_threshold`
will be sent to `claude-sonnet-4-6` for classification. This adds latency and
cost; use it selectively.

---

## Extending the System

The modular architecture makes it easy to add new capabilities:

| Goal | Where to add it |
|---|---|
| New file type | `extractor.py` → add `_extract_<ext>()` method |
| New classifier (e.g. vector search) | subclass / replace `DocumentClassifier` |
| Cloud storage source | new module implementing the same `place()` interface |
| Email attachments | pre-processing step before `main.py` pipeline |
| Web dashboard | consume `classification_report.csv` |
| Duplicate detection | use `utils.file_md5()` before `organizer.place()` |

---

## Licence

MIT
