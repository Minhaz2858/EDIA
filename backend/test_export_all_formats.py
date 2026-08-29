"""
Export tests for /api/reports/export-professional
Run with: python3 backend/test_export_all_formats.py
"""
import json
import sys
import urllib.request
import urllib.error
import datetime

BASE_URL = "http://127.0.0.1:3000"
ENDPOINT = f"{BASE_URL}/api/reports/export-professional"

EXPECTED_CONTENT_TYPES = {
    "pdf":  "application/pdf",
    "html": "text/html",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

EXPECTED_MIN_SIZE = {
    "pdf":  1_000,
    "html": 500,
    "docx": 1_000,
    "pptx": 1_000,
}

# ---------- helpers ----------

def _post(payload: dict) -> tuple[int, str, bytes]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, "", body


def _make_payload(fmt: str, blocks=None) -> dict:
    if blocks is None:
        blocks = _default_blocks()
    return {
        "title": "Test Report",
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "format": fmt,
        "blocks": blocks,
    }


def _default_blocks():
    return [
        {
            "type": "kpi_cards",
            "title": "Key Indicators",
            "definition": {"products": ["naphtha", "crude_oil"]},
            "order": 0,
        },
        {
            "type": "data_table",
            "title": "Price Table",
            "definition": {
                "products": ["isoprene", "piperylene"],
                "date_range": {"type": "relative", "relative": {"value": 30, "unit": "days"}},
            },
            "order": 1,
        },
        {
            "type": "chart",
            "title": "Price Chart",
            "definition": {
                "products": ["sis", "c5_resin"],
                "chart_type": "line",
                "date_range": {"type": "relative", "relative": {"value": 30, "unit": "days"}},
            },
            "order": 2,
        },
    ]


# ---------- test cases ----------

passed = 0
failed = 0


def run(name: str, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS  {name}")
        passed += 1
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        failed += 1
    except Exception as e:
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
        failed += 1


# --- 1. All four formats return HTTP 200 with correct content-type ---
def test_format(fmt: str):
    status, content_type, body = _post(_make_payload(fmt))
    assert status == 200, f"Expected 200, got {status}: {body[:200]}"
    expected_ct = EXPECTED_CONTENT_TYPES[fmt]
    assert expected_ct in content_type, (
        f"Expected content-type containing '{expected_ct}', got '{content_type}'"
    )
    assert len(body) >= EXPECTED_MIN_SIZE[fmt], (
        f"Response too small: {len(body)} bytes (expected >= {EXPECTED_MIN_SIZE[fmt]})"
    )


# --- 2. generated_at as ISO string (the actual browser format) ---
def test_generated_at_string():
    payload = _make_payload("pdf")
    payload["generated_at"] = "2026-04-28T09:30:00.000Z"
    status, _, body = _post(payload)
    assert status == 200, f"Expected 200, got {status}: {body[:200]}"


# --- 3. generated_at omitted (optional) ---
def test_generated_at_optional():
    payload = _make_payload("pdf")
    del payload["generated_at"]
    status, _, body = _post(payload)
    assert status == 200, f"Expected 200, got {status}: {body[:200]}"


# --- 4. Empty blocks list ---
def test_empty_blocks():
    payload = _make_payload("pdf", blocks=[])
    status, content_type, body = _post(payload)
    assert status == 200, f"Expected 200, got {status}: {body[:200]}"
    assert "application/pdf" in content_type, f"Wrong content-type: {content_type}"


# --- 5. Blocks with no rendered data (definition only) ---
def test_blocks_no_rendered():
    blocks = [
        {"type": "kpi_cards", "title": "KPIs", "definition": {"products": ["naphtha"]}, "order": 0},
        {"type": "data_table", "title": "Table", "definition": {"products": ["isoprene"]}, "order": 1},
    ]
    payload = _make_payload("pdf", blocks=blocks)
    status, _, body = _post(payload)
    assert status == 200, f"Expected 200, got {status}: {body[:200]}"


# --- 6. generated_at as {} (old broken browser payload) must return 422 ---
def test_generated_at_object_returns_422():
    data = json.dumps({
        "title": "Test",
        "generated_at": {},          # this was the bug — Map serialized as {}
        "format": "pdf",
        "blocks": [],
    }).encode()
    req = urllib.request.Request(
        ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 422, f"Expected 422 for object generated_at, got {status}"


# --- 7. Invalid format name returns 400 ---
def test_invalid_format():
    payload = _make_payload("xlsx")        # not a supported format
    status, _, body = _post(payload)
    assert status == 400, f"Expected 400 for unsupported format, got {status}: {body[:100]}"


# --- 8. Missing title returns 422 ---
def test_missing_title():
    payload = _make_payload("pdf")
    del payload["title"]
    status, _, body = _post(payload)
    assert status == 422, f"Expected 422 for missing title, got {status}: {body[:100]}"


# --- 9. PDF magic bytes (starts with %PDF) ---
def test_pdf_magic_bytes():
    status, _, body = _post(_make_payload("pdf"))
    assert status == 200, f"Expected 200, got {status}"
    assert body[:4] == b"%PDF", f"PDF does not start with %PDF: {body[:20]}"


# --- 10. HTML is valid-ish (contains <html) ---
def test_html_contains_html_tag():
    status, _, body = _post(_make_payload("html"))
    assert status == 200, f"Expected 200, got {status}"
    assert b"<html" in body.lower() or b"<!doctype" in body.lower(), (
        "HTML response does not contain <html tag"
    )


# --- 11. DOCX magic bytes (ZIP: PK\x03\x04) ---
def test_docx_magic_bytes():
    status, _, body = _post(_make_payload("docx"))
    assert status == 200, f"Expected 200, got {status}"
    assert body[:2] == b"PK", f"DOCX does not start with PK: {body[:10]}"


# --- 12. PPTX magic bytes (ZIP: PK\x03\x04) ---
def test_pptx_magic_bytes():
    status, _, body = _post(_make_payload("pptx"))
    assert status == 200, f"Expected 200, got {status}"
    assert body[:2] == b"PK", f"PPTX does not start with PK: {body[:10]}"


# ---------- run ----------

print(f"\nRunning export tests against {ENDPOINT}\n")

run("PDF format returns 200 + application/pdf",      lambda: test_format("pdf"))
run("HTML format returns 200 + text/html",           lambda: test_format("html"))
run("DOCX format returns 200 + correct content-type",lambda: test_format("docx"))
run("PPTX format returns 200 + correct content-type",lambda: test_format("pptx"))
run("generated_at as ISO string (browser fix)",      test_generated_at_string)
run("generated_at omitted (optional field)",         test_generated_at_optional)
run("empty blocks list",                             test_empty_blocks)
run("blocks with no rendered data",                  test_blocks_no_rendered)
run("generated_at as {} returns 422 (old bug)",      test_generated_at_object_returns_422)
run("invalid format returns 400",                    test_invalid_format)
run("missing title returns 422",                     test_missing_title)
run("PDF starts with %PDF magic bytes",              test_pdf_magic_bytes)
run("HTML contains <html> tag",                      test_html_contains_html_tag)
run("DOCX starts with PK (ZIP) magic bytes",         test_docx_magic_bytes)
run("PPTX starts with PK (ZIP) magic bytes",         test_pptx_magic_bytes)

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
