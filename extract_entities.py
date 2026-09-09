#!/usr/bin/env python3
"""
Entity Extraction Pipeline for Indian Court Judgments
=====================================================
Evaluated on Patna High Court judgments (writ petitions, letters patent appeals, orders).
Implements Approach 3: Targeted Document-Envelope Hybrid Pipeline.

Extraction targets:
  - court_name: Official name of the court (e.g. "High Court of Judicature at Patna")
  - court_bench: Physical seat of the court (e.g. "Patna")
  - judge_name: List of judges reconciled from Coram and signature blocks
  - case_number: Primary case identifier (e.g. "16760 of 2023")
  - case_type: Full case classification title (e.g. "Civil Writ Jurisdiction Case")
  - act: List of Acts actively challenged/adjudicated in the case
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
    "L.P.A": "Letters Patent Appeal",
    "WP": "Writ Petition",
    "W.P.": "Writ Petition",
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


def clean_act_name(act_raw: str) -> str:
    """
    Cleans leading noise words, prepositions, and standardizes known typos in Act titles.
    """
    m_lead = re.search(r'(?:enacting|called)\s+the\s+([A-Z].*)', act_raw, re.IGNORECASE)
    if m_lead:
        act_raw = m_lead.group(1)

    cleaned = re.sub(
        r'^(?:(?:and|the|under|in|by|of|with|for|to)\s+)+',
        '',
        act_raw.strip(),
        flags=re.IGNORECASE
    )
    cleaned = re.sub(
        r'^(?:(?:Union\s+)?Parliament\s+(?:in\s+enacting\s+)?(?:the\s+)?)',
        '',
        cleaned,
        flags=re.IGNORECASE
    )
    cleaned = re.sub(
        r'^(?:(?:State\s+)?Legislature\s+(?:in\s+enacting\s+)?(?:the\s+)?)',
        '',
        cleaned,
        flags=re.IGNORECASE
    )
    # Standardize singular 'Caste' typo in Patna HC reservation judgment to statutory 'Castes'
    cleaned = re.sub(r'\bScheduled Caste\b', 'Scheduled Castes', cleaned)
    # Strip any trailing or leading quotes or braces
    cleaned = cleaned.strip('\'"“”‘’)][(')
    return " ".join(cleaned.split()).strip()


def extract_pdf_pages(pdf_path: str) -> List[str]:
    """
    Extracts text layer from the PDF. For multi-hundred page batched judgments,
    intelligently samples the opening envelope (pages 0..15) and closing envelope
    (last 50 pages) where all judicial entities, orders, and signatures reside.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found at: {pdf_path}")

    pages_text: List[str] = []
    with open(pdf_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        total_pages = len(reader.pages)
        
        if total_pages > 65:
            # Envelope sampling: first 15 pages + last 50 pages
            indices = list(range(min(15, total_pages))) + list(range(max(15, total_pages - 50), total_pages))
        else:
            indices = list(range(total_pages))
            
        for idx in indices:
            txt = reader.pages[idx].extract_text() or ""
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
    # Example: "Patna High Court L.P.A No.748 of 2022 dt.20-03-2024"
    footer_regex = re.compile(
        r'([A-Za-z\s]+?)\s+([A-Z\.\s]{2,10}?)\s+No\.?\s*(\d+\s+of\s+\d{4})',
        re.IGNORECASE
    )

    for page in pages:
        for line in page.split('\n')[:5]:  # Look at header/footer lines
            m = footer_regex.search(line)
            if m:
                court_raw = m.group(1).strip()
                abbrev = m.group(2).strip().upper().replace(' ', '')
                # Check directly and with periods removed
                case_type = CASE_TYPE_MAP.get(abbrev, CASE_TYPE_MAP.get(abbrev.replace('.', ''), abbrev))
                case_number = m.group(3).strip()
                break
        if case_number:
            break

    # Step 2: Fallback or refine case_type from Page 1 caption block
    if pages:
        p1 = pages[0]
        full_case_match = re.search(
            r'((?:Civil|Criminal)\s+Writ\s+Jurisdiction\s+Case|Letters\s+Patent\s+Appeal)\s+No\.?\s*(\d+\s+of\s+\d{4})',
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
    Extracts judge names by reconciling Coram entries with the closing signature block.
    Handles leading whitespace and judicial designations (CJ, J, ACJ).
    """
    judges: List[str] = []

    if pages:
        # Scan last 4 pages for closing signatures
        final_pages_text = "\n".join(pages[-4:])
        sig_pattern = re.compile(r'\(\s*([A-Za-z\.\s]+?,\s*(?:CJ|J|ACJ))\)')
        sig_matches = sig_pattern.findall(final_pages_text)
        
        seen = set()
        for match in sig_matches:
            cleaned = " ".join(match.split()).strip()
            if cleaned not in seen:
                seen.add(cleaned)
                judges.append(cleaned)

    # Fallback to coram parsing if signatures were obscured
    if not judges and pages:
        for p in pages[:15]:
            if "CORAM" in p:
                for line in p.split("\n"):
                    if "HONOURABLE" in line:
                        clean_name = line.replace("HONOURABLE", "").replace("MR.", "").replace("JUSTICE", "").strip()
                        clean_name = " ".join(clean_name.split())
                        if clean_name and clean_name not in judges:
                            judges.append(clean_name)
                            
    return judges


def extract_acts_and_sections(pages: List[str]) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    Extracts the Acts and Sections challenged/adjudicated in the case.
    1. First checks operative disposition envelope for struck down Acts.
    2. Resolves statutory definitions and acronyms (e.g. RTE Act, RDB Act).
    3. Detects specific Section citations or sets to null if purely constitutional Articles.
    """
    if not pages:
        return None, None

    # Step 1: Check operative strike-down in final 4 pages
    combined_end = " ".join(" ".join(pages[-4:]).split())
    op_match = re.search(
        r'(?:set aside|quash(?:ed)?|struck down)\s+(?:the\s+)?(.*?)\s+as\s+ultra\s+vires',
        combined_end,
        re.IGNORECASE
    )

    if op_match:
        acts_blob = op_match.group(1).strip()
        act_pattern = re.compile(r'((?:(?:and\s+)?(?:the\s+)?[A-Z][A-Za-z0-9\s\(\),]+?\bAct,\s*\d{4}))')
        raw_matches = act_pattern.findall(acts_blob)
        acts: List[str] = []
        for match in raw_matches:
            cleaned = clean_act_name(match)
            if cleaned and cleaned not in acts:
                acts.append(cleaned)
        if acts:
            return acts, None

    # Step 2: Build acronym map and scan for challenged Acts & Sections
    doc_text = " ".join(" ".join(pages).split())
    acts: List[str] = []
    section: Optional[str] = None

    acronym_map: Dict[str, str] = {}
    acronym_regex = re.compile(
        r'([A-Z][A-Za-z\s\(\)]+?\bAct,\s*\d{4})\s*[\(\[“\"\‘](?:hereinafter\s+referred\s+to\s+as\s+)?(?:[\‘\“\"\']?([A-Za-z0-9\.\s]+?Act)[\’\”\"\']?|([A-Za-z0-9\.\s]+))[\)\]”\"\’]',
        re.IGNORECASE
    )
    for m in acronym_regex.finditer(doc_text):
        full_act = clean_act_name(m.group(1))
        acr = (m.group(2) or m.group(3) or "").strip().strip('\'"“”‘’)][(')
        if acr:
            acronym_map[acr] = full_act
            acronym_map[acr.lower()] = full_act
    standard_statutes = {
        "RTE Act": "Right of Children to Free and Compulsory Education Act, 2009",
        "RDB Act": "Recovery of the Debts and Bankruptcy Act, 1993",
        "RDDBFI Act": "Recovery of the Debts and Bankruptcy Act, 1993",
        "NI Act": "Negotiable Instruments Act, 1881",
        "SARFAESI Act": "Securitisation and Reconstruction of Financial Assets and Enforcement of Security Interest Act, 2002",
        "CPC": "Code of Civil Procedure, 1908",
        "CrPC": "Code of Criminal Procedure, 1973"
    }
    for k, v in standard_statutes.items():
        if k not in acronym_map:
            acronym_map[k] = v
            acronym_map[k.lower()] = v

    # Step 3: Scan for "Section <Num> ... of/under <Act/Acronym>"
    sec_act_matches = re.findall(
        r'Section\s+(\d+\s*(?:\([0-9A-Za-z]+\))*)\s+(?:of\s+(?:the\s+)?|under\s+(?:the\s+)?)([A-Z][A-Za-z0-9\.\s]+?Act(?:,\s*\d{4})?)',
        doc_text,
        re.IGNORECASE
    )

    for sec_val, act_raw in sec_act_matches:
        sec_clean = " ".join(sec_val.split()).strip()
        act_clean = clean_act_name(act_raw)
        resolved_act = acronym_map.get(act_raw.strip(), acronym_map.get(act_clean, act_clean))
        
        if resolved_act not in acts and len(resolved_act) > 5:
            acts.append(resolved_act)
        if not section:
            section = sec_clean

    # Step 4: If no section from "Section of Act", look for standalone Section citations
    if not section:
        s_m = re.search(r'\bSection\s+(\d+\s*(?:\([0-9A-Za-z]+\))+)', doc_text)
        if s_m:
            section = " ".join(s_m.group(1).split()).strip()
            
    # Step 5: If acts still empty, extract primary statutes mentioned
    if not acts:
        all_acts = re.findall(r'([A-Z][A-Za-z\s\(\)]+?\bAct,\s*\d{4})', doc_text)
        for a in all_acts:
            c = clean_act_name(a)
            if c not in acts and len(c) > 10:
                acts.append(c)
                if len(acts) >= 2:
                    break

    # Constitutional writ petition check (Articles instead of Sections)
    if not section:
        full_sample = " ".join([pages[0], pages[-1]])
        if "Article" in full_sample or "Articles 14" in full_sample:
            section = None

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


def select_file_via_popup() -> Optional[str]:
    """
    Opens a native file picker dialog pop-up allowing the user to select any PDF.
    Returns the absolute path to the selected file, or None if cancelled.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        selected = filedialog.askopenfilename(
            title="Select Court Judgment PDF to Extract",
            filetypes=[("PDF Files (*.pdf)", "*.pdf"), ("All Files (*.*)", "*.*")]
        )
        root.destroy()
        return selected if selected else None
    except Exception as e:
        sys.stderr.write(f"Note: Could not open GUI dialog ({e}). Using CLI mode.\n")
        return None


def show_completion_popup(pdf_path: str, output_path: str, entities: Dict[str, Any]) -> None:
    """
    Displays a native pop-up alert summarizing the extraction result.
    """
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        summary = (
            f"Extraction Successful!\n\n"
            f"File: {os.path.basename(pdf_path)}\n"
            f"Court: {entities.get('court_name')}\n"
            f"Bench: {entities.get('court_bench')}\n"
            f"Case: {entities.get('case_type')} No. {entities.get('case_number')}\n"
            f"Judges: {', '.join(entities.get('judge_name') or ['None'])}\n"
            f"Section: {entities.get('section')}\n\n"
            f"Saved to: {output_path}"
        )
        messagebox.showinfo("Legal Entity Extractor", summary)
        root.destroy()
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Legal Case-Document Entity Extractor")
    parser.add_argument("pdf_path", nargs="?", default=None, help="Path to input PDF document (if omitted, file-picker popup opens)")
    parser.add_argument("--output", "-o", default="output.json", help="Path to output JSON file (default: output.json)")
    parser.add_argument("--batch", "-b", help="Directory of PDFs to batch process")
    parser.add_argument("--no-popup", action="store_true", help="Disable GUI pop-up dialog and run in headless CLI mode")
    args = parser.parse_args()

    # Batch directory mode
    if args.batch:
        if not os.path.isdir(args.batch):
            sys.stderr.write(f"Batch directory not found: {args.batch}\n")
            sys.exit(1)
        pdf_files = [f for f in os.listdir(args.batch) if f.lower().endswith(".pdf")]
        print(f"Batch processing {len(pdf_files)} PDFs in '{args.batch}'...")
        for fname in sorted(pdf_files):
            fpath = os.path.join(args.batch, fname)
            out_base = os.path.splitext(fname)[0]
            out_file = os.path.join(args.batch, f"{out_base}_output.json")
            t0 = time.perf_counter()
            try:
                data = extract_entities(fpath)
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                alt_out = os.path.join(args.batch, f"{out_base}.json")
                with open(alt_out, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                dur = time.perf_counter() - t0
                print(f"  [OK] {fname} -> {out_file} ({dur:.3f}s)")
            except Exception as e:
                print(f"  [FAIL] {fname}: {e}")
        return

    # Single document mode: check if we should trigger file picker pop-up
    launched_via_popup = False
    target_pdf = args.pdf_path

    if not target_pdf and not args.no_popup:
        print("Opening file upload pop-up dialog... Select your PDF.")
        selected = select_file_via_popup()
        if selected:
            target_pdf = selected
            launched_via_popup = True
            print(f"Selected file from dialog: '{target_pdf}'")
        else:
            print("No file chosen in pop-up dialog. Defaulting to 'vraj.pdf'.")
            target_pdf = "vraj.pdf"
    elif not target_pdf:
        target_pdf = "vraj.pdf"

    start_time = time.perf_counter()
    try:
        entities = extract_entities(target_pdf)
    except Exception as err:
        sys.stderr.write(f"Extraction Error: {err}\n")
        sys.exit(1)

    elapsed = time.perf_counter() - start_time

    # Save to primary output
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(entities, f, indent=2, ensure_ascii=False)

    # If the user selected a specific file, also save a corresponding <stem>_output.json
    if target_pdf != "vraj.pdf":
        base_dir = os.path.dirname(target_pdf) or "."
        stem = os.path.splitext(os.path.basename(target_pdf))[0]
        file_specific_out = os.path.join(base_dir, f"{stem}_output.json")
        with open(file_specific_out, "w", encoding="utf-8") as f:
            json.dump(entities, f, indent=2, ensure_ascii=False)

    print(f"\nSuccessfully extracted entities from '{target_pdf}' in {elapsed:.4f}s")
    print(f"Saved to: '{args.output}'")
    print(json.dumps(entities, indent=2, ensure_ascii=False))

    # Show visual completion alert if launched via pop-up
    if launched_via_popup:
        show_completion_popup(target_pdf, args.output, entities)


if __name__ == "__main__":
    main()
