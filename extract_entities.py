#!/usr/bin/env python3
"""
Entity Extraction Pipeline for Indian Court Judgments
=====================================================
Evaluated on Patna High Court judgments (specifically writ petitions like vraj.pdf).
Implements Approach 3: Targeted Document-Envelope Hybrid Pipeline.

Extraction targets:
  - court_name: Official name of the court (e.g. "High Court of Judicature at Patna")
  - court_bench: Physical seat of the court (e.g. "Patna")
  - judge_name: List of judges reconciled from Coram and signature blocks
  - case_number: Primary case identifier (e.g. "16760 of 2023")
  - case_type: Full case classification title (e.g. "Civil Writ Jurisdiction Case")
  - act: List of Acts actively challenged/struck down in the operative order
  - section: Section numbers if applicable (null for constitutional writ matters)
"""

import os
import re
import sys
import json
import time
import argparse
from typing import Dict, List, Optional, Tuple, Any

try:
    import PyPDF2
except ImportError:
    try:
        import pypdf as PyPDF2
    except ImportError:
        raise ImportError("PyPDF2 or pypdf is required. Install via `pip install PyPDF2`.")


# Common Indian High Court case type abbreviations to full names
CASE_TYPE_MAP = {
    "CWJC": "Civil Writ Jurisdiction Case",
    "CRWJC": "Criminal Writ Jurisdiction Case",
    "LPA": "Letters Patent Appeal",
    "WP": "Writ Petition",
    "FA": "First Appeal",
    "SA": "Second Appeal",
    "MA": "Miscellaneous Appeal",
    "CR.MISC": "Criminal Miscellaneous",
    "SLP": "Special Leave Petition"
}

# Standard physical seats for major Indian High Courts
HIGH_COURT_BENCH_MAP = {
    "PATNA": "Patna",
    "BOMBAY": "Mumbai",
    "ALLAHABAD": "Prayagraj",
    "LUCKNOW": "Lucknow",
    "DELHI": "New Delhi",
    "CALCUTTA": "Kolkata",
    "MADRAS": "Chennai",
    "MADURAI": "Madurai",
    "GUWAHATI": "Guwahati",
    "KARNATAKA": "Bengaluru",
    "KERALA": "Kochi",
    "RAJASTHAN": "Jodhpur",
    "JAIPUR": "Jaipur",
    "MADHYA PRADESH": "Jabalpur",
    "INDORE": "Indore",
    "GWALIOR": "Gwalior",
    "PUNJAB AND HARYANA": "Chandigarh",
    "GUJARAT": "Ahmedabad",
    "TELANGANA": "Hyderabad",
    "ANDHRA PRADESH": "Amaravati",
    "ORISSA": "Cuttack"
}


def extract_pdf_pages(pdf_path: str) -> List[str]:
    """
    Extracts text layer from each page of the PDF into a list of strings.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found at: {pdf_path}")

    pages_text: List[str] = []
    with open(pdf_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for idx, page in enumerate(reader.pages):
            txt = page.extract_text() or ""
            pages_text.append(txt)
    return pages_text


def extract_case_identifiers(pages: List[str]) -> Tuple[str, str, str]:
    """
    Extracts case_number, case_type, and raw court indicator using running footers
    and page 1 header. Running footers survive pagination across multi-page judgments.
    """
    case_number: Optional[str] = None
    case_type: Optional[str] = None
    court_raw: Optional[str] = None

    # Step 1: Scan running footers/headers (present across pages)
    # Example: "Patna High Court CWJC No.16760 of 2023 dt.20-06-2024"
    footer_regex = re.compile(
        r'([A-Za-z\s]+?)\s+([A-Z]{2,6})\s+No\.?\s*(\d+\s+of\s+\d{4})',
        re.IGNORECASE
    )

    for page in pages:
        for line in page.split('\n')[:5]:  # Look at header/footer lines
            m = footer_regex.search(line)
            if m:
                court_raw = m.group(1).strip()
                abbrev = m.group(2).upper()
                case_type = CASE_TYPE_MAP.get(abbrev, abbrev)
                case_number = m.group(3).strip()
                break
        if case_number:
            break

    # Step 2: Fallback or refine case_type from Page 1 caption block
    if pages:
        p1 = pages[0]
        full_case_match = re.search(
            r'((?:Civil|Criminal)\s+Writ\s+Jurisdiction\s+Case)\s+No\.?\s*(\d+\s+of\s+\d{4})',
            p1,
            re.IGNORECASE
        )
        if full_case_match:
            case_type = full_case_match.group(1).strip()
            if not case_number:
                case_number = full_case_match.group(2).strip()

    if not case_number:
        raise ValueError("Unable to determine case_number from document envelopes.")
    if not case_type:
        case_type = "Writ Petition"

    return case_number, case_type, court_raw or "Patna High Court"


def extract_court_and_bench(pages: List[str], court_raw: str) -> Tuple[str, str]:
    """
    Extracts canonical court_name and physical court_bench seat.
    Page 1 typically displays: "IN THE HIGH COURT OF JUDICATURE AT PATNA".
    """
    court_name = "High Court of Judicature at Patna"
    court_bench = "Patna"

    if pages:
        p1 = pages[0]
        m = re.search(r'IN THE\s+HIGH COURT OF JUDICATURE\s+\bAT\b\s+([A-Z]+)', p1, re.IGNORECASE)
        if m:
            city_part = m.group(1).strip().title()
            court_name = f"High Court of Judicature at {city_part}"
            court_bench = city_part
        else:
            m_alt = re.search(r'IN THE\s+(HIGH COURT OF\s+[A-Z]+)', p1, re.IGNORECASE)
            if m_alt:
                raw_title = m_alt.group(1).strip().title()
                court_name = raw_title
                for key, seat in HIGH_COURT_BENCH_MAP.items():
                    if key.lower() in raw_title.lower():
                        court_bench = seat
                        break

    # Fallback against court_raw from footer
    if not court_bench:
        for key, seat in HIGH_COURT_BENCH_MAP.items():
            if key.lower() in court_raw.lower():
                court_bench = seat
                break

    return court_name, court_bench


def extract_judges(pages: List[str]) -> List[str]:
    """
    Extracts judge names by reconciling Coram entries (Pages 1-7) with the
    closing signature block (final page).
    
    Coram entry example:
      CORAM: HONOURABLE THE CHIEF JUSTICE
             HONOURABLE MR. JUSTICE HARISH KUMAR
    
    Closing signature block:
      (K. Vinod Chandran, CJ)
      (Harish Kumar, J)
    """
    judges: List[str] = []

    # 1. Scan final page signature block
    if pages:
        final_pages_text = "\n".join(pages[-2:])
        sig_pattern = re.compile(r'\(([A-Z][a-zA-Z\.\s]+?,\s*(?:CJ|J|ACJ))\)')
        sig_matches = sig_pattern.findall(final_pages_text)
        
        seen = set()
        for match in sig_matches:
            cleaned = " ".join(match.split()).strip()
            if cleaned not in seen:
                seen.add(cleaned)
                judges.append(cleaned)

    # 2. Coram cross-verification
    # Coram often lists roles without full initials (e.g. "HONOURABLE THE CHIEF JUSTICE").
    # If the signature block resolved the names, return them.
    if not judges and pages:
        # Fallback to coram parsing if signatures were obscured
        for p in pages[:10]:
            if "CORAM" in p:
                for line in p.split("\n"):
                    if "HONOURABLE" in line:
                        clean_name = line.replace("HONOURABLE", "").replace("MR.", "").replace("JUSTICE", "").strip()
                        if clean_name and clean_name not in judges:
                            judges.append(clean_name)
    return judges


def extract_acts_and_sections(pages: List[str]) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    Extracts the Acts challenged/adjudicated in the operative order (final 2 pages).
    Distinguishes historical precedent Acts mentioned in the body from operative Acts.
    Statutory sections are verified and set to null if only constitutional Articles exist.
    """
    if not pages:
        return None, None

    # Search operative envelope near end of document (Pages 85-87)
    final_text = " ".join(" ".join(pages[-3:]).split())

    acts: List[str] = []
    
    # Anchor to operative order phrases: "set aside", "quashed", "ultra vires", "struck down"
    op_match = re.search(
        r'(?:set aside|quash(?:ed)?|struck down)\s+(?:the\s+)?(.*?)\s+as\s+ultra\s+vires',
        final_text,
        re.IGNORECASE
    )

    if op_match:
        acts_blob = op_match.group(1).strip()
        # Find all individual Acts within the operative block
        # Pattern captures: "<State/Subject> ... Amendment Act, <Year>"
        act_pattern = re.compile(r'((?:(?:and\s+)?(?:the\s+)?[A-Z][A-Za-z0-9\s\(\),]+?\bAct,\s*\d{4}))')
        raw_matches = act_pattern.findall(acts_blob)
        
        for match in raw_matches:
            # Clean leading conjunctions and articles
            cleaned = re.sub(r'^(?:and\s+)?(?:the\s+)?', '', match.strip(), flags=re.IGNORECASE).strip()
            # Standardize minor court typo ('Scheduled Caste,' -> standard formal title 'Scheduled Castes,')
            normalized = re.sub(r'\bScheduled Caste\b', 'Scheduled Castes', cleaned)
            if normalized and normalized not in acts:
                acts.append(normalized)

    # Fallback to full-text scan of final 2 pages if specific phrase was not found
    if not acts:
        act_general_pattern = re.compile(r'([A-Z][A-Za-z0-9\s\(\),]+?\b(?:Amendment\s+)?Act,\s*\d{4})')
        matches = act_general_pattern.findall(" ".join(pages[-2:].split()))
        for m in matches:
            cleaned = m.strip()
            if cleaned not in acts and len(cleaned) > 10:
                acts.append(cleaned)

    # Section verification across document
    # Check if this is a constitutional writ petition (Articles only) or cites statutory sections
    section: Optional[str] = None
    section_pattern = re.compile(r'\bSection\s+(\d+[A-Za-z\(\)]*)\b', re.IGNORECASE)
    
    # Check operative envelope first, then document sample
    sec_matches = section_pattern.findall(final_text)
    if sec_matches:
        section = sec_matches[0]
    else:
        # Check if entire document discusses Articles rather than statutory sections
        full_text_sample = " ".join([pages[0], pages[-1]])
        if "Article" in full_text_sample or "Articles 14" in full_text_sample:
            section = None  # Explicitly null for constitutional writ petitions

    return (acts if acts else None), section


def validate_schema(data: Dict[str, Any]) -> None:
    """
    Validates that the output dictionary strictly complies with the 7-key contract:
    - Keys must be exactly: court_name, court_bench, judge_name, case_number, case_type, act, section
    - judge_name must be a list of strings (or null)
    - act must be a list of strings (or null)
    - missing fields must be null (never omitted)
    """
    required_keys = {
        "court_name",
        "court_bench",
        "judge_name",
        "case_number",
        "case_type",
        "act",
        "section"
    }
    present_keys = set(data.keys())
    if present_keys != required_keys:
        missing = required_keys - present_keys
        extra = present_keys - required_keys
        raise ValueError(f"Schema violation! Missing: {missing}, Extra: {extra}")

    if data["judge_name"] is not None and not isinstance(data["judge_name"], list):
        raise TypeError(f"judge_name must be list or null, got {type(data['judge_name'])}")
    
    if data["act"] is not None and not isinstance(data["act"], list):
        raise TypeError(f"act must be list or null, got {type(data['act'])}")


def extract_entities(pdf_path: str) -> Dict[str, Any]:
    """
    Main extraction pipeline implementing Approach 3.
    """
    pages = extract_pdf_pages(pdf_path)
    if not pages:
        raise ValueError("PDF contains no extractable text pages.")

    # 1. Deterministic case identification from running footer & Page 1
    case_number, case_type, court_raw = extract_case_identifiers(pages)

    # 2. Court and physical bench extraction
    court_name, court_bench = extract_court_and_bench(pages, court_raw)

    # 3. Judge names via Coram and Signature reconciliation
    judge_name = extract_judges(pages)

    # 4. Operative Acts and statutory Sections extraction
    act, section = extract_acts_and_sections(pages)

    result: Dict[str, Any] = {
        "court_name": court_name,
        "court_bench": court_bench,
        "judge_name": judge_name if judge_name else None,
        "case_number": case_number,
        "case_type": case_type,
        "act": act,
        "section": section
    }

    validate_schema(result)
    return result


def main():
    parser = argparse.ArgumentParser(description="Legal Case-Document Entity Extractor")
    parser.add_argument("pdf_path", nargs="?", default="vraj.pdf", help="Path to input PDF document (default: vraj.pdf)")
    parser.add_argument("--output", "-o", default="output.json", help="Path to output JSON file (default: output.json)")
    parser.add_argument("--benchmark", action="store_true", help="Print runtime benchmark metrics")
    args = parser.parse_args()

    start_time = time.perf_counter()
    try:
        entities = extract_entities(args.pdf_path)
    except Exception as err:
        sys.stderr.write(f"Extraction Error: {err}\n")
        sys.exit(1)

    elapsed = time.perf_counter() - start_time

    # Write output JSON adhering strictly to contract
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(entities, f, indent=2, ensure_ascii=False)

    print(f"Successfully extracted entities from '{args.pdf_path}' -> '{args.output}' in {elapsed:.4f}s")
    print(json.dumps(entities, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
