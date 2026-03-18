"""
Document extraction service — pdfplumber-based.
Replicates the n8n PDF-parsing microservice:
  1. Parse TOC → build section/subsection page ranges
  2. Per-page: inject subsection markers, extract tables → Markdown, embed inline
  3. Return section-wise structured list (sectionName, sectionStart&End, text)
"""
import io
import re
import tempfile
import os
from typing import Dict, Any, List, Optional, Tuple
from app.core.logging import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# TOC Parsing
# --------------------------------------------------------------------------- #
TOC_LINE_PATTERN = re.compile(r"^(.*?)\.{5,}\s*(\d+)$", re.MULTILINE)
SECTION_PREFIX_PATTERN = re.compile(r"^SECTION\s*[IVXLCD]+", re.IGNORECASE)


def _transpose_table(rows: List[List[str]]) -> List[List[str]]:
    if not rows:
        return []
    max_cols = max(len(r) for r in rows)
    return [
        [rows[r][c] if c < len(rows[r]) else "" for r in range(len(rows))]
        for c in range(max_cols)
    ]


def _to_markdown_table(rows: List[List[str]]) -> str:
    if not rows:
        return ""
    header = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join(["---"] * len(rows[0])) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(filter(None, [header, sep, body]))


def _extract_toc_mapping(pdf) -> List[Dict[str, Any]]:
    """
    Scan the first 15 pages for the Table of Contents.
    Returns a list of {name, type, start_page, end_page, range_str}.
    """
    mapping: List[Dict[str, Any]] = []
    found_toc = False
    total_pages = len(pdf.pages)

    for i in range(min(15, total_pages)):
        text = pdf.pages[i].extract_text() or ""
        if "TABLE OF CONTENTS" in text.upper():
            found_toc = True

        if found_toc:
            matches = TOC_LINE_PATTERN.findall(text)
            for name, page_str in matches:
                name = name.strip()
                try:
                    page_num = int(page_str)
                    is_section = bool(SECTION_PREFIX_PATTERN.match(name))
                    mapping.append({
                        "start_page": page_num,
                        "type": "section" if is_section else "subsection",
                        "name": name,
                    })
                except ValueError:
                    continue
            if len(mapping) > 30 and "SECTION" not in text.upper():
                break

    mapping.sort(key=lambda x: x["start_page"])

    for idx, entry in enumerate(mapping):
        next_page = total_pages
        if entry["type"] == "section":
            for next_entry in mapping[idx + 1:]:
                if next_entry["type"] == "section":
                    next_page = next_entry["start_page"] - 1
                    break
        else:
            if idx + 1 < len(mapping):
                next_page = mapping[idx + 1]["start_page"] - 1
        entry["end_page"] = next_page
        entry["range_str"] = f"{entry['start_page']}-{entry['end_page']}"

    return mapping


def _get_metadata_for_page(
    mapping: List[Dict[str, Any]], current_page: int
) -> Tuple[str, str, str, str]:
    """Return (section_name, section_range, subsection_name, subsection_range)."""
    sec_name, sec_range = "General", "1-N"
    sub_name, sub_range = "no subsection", "no subsection"

    for entry in mapping:
        if (
            entry["type"] == "section"
            and entry["start_page"] <= current_page <= entry["end_page"]
        ):
            sec_name, sec_range = entry["name"], entry["range_str"]
        if (
            entry["type"] == "subsection"
            and entry["start_page"] <= current_page <= entry["end_page"]
        ):
            sub_name, sub_range = entry["name"], entry["range_str"]

    return sec_name, sec_range, sub_name, sub_range


# --------------------------------------------------------------------------- #
# Cleaning — mirrors n8n "Cleaned text3" code node
# --------------------------------------------------------------------------- #
def _clean_section_text(text: str) -> str:
    """
    Clean text exactly as the n8n 'Cleaned text3' code node does:
      - Remove TABLE OF CONTENTS text
      - Replace \\n with space
      - Collapse whitespace
      - Remove TOC dot leaders (5+ dots)
      - Remove 'Page N' occurrences
    """
    if not text:
        return ""
    text = re.sub(r"TABLE OF CONTENTS", "", text, flags=re.IGNORECASE)
    text = text.replace("\n", " ")
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\.{5,}", "", text)
    text = re.sub(r"Page\s\d+", "", text, flags=re.IGNORECASE)
    return text.strip()


# --------------------------------------------------------------------------- #
# Main extraction
# --------------------------------------------------------------------------- #
class ExtractionService:
    """
    Service for extracting section-wise text from PDFs using pdfplumber.
    Replicates the n8n PDF-parsing microservice that powers the embedding flow.
    """

    @staticmethod
    def extract_sections_from_pdf(file_content: bytes) -> List[Dict[str, Any]]:
        """
        Parse a PDF and return section-wise structured data:
          [
            {
              "sectionName": str,
              "sectionStart&End": str,
              "text": str   ← cleaned, tables embedded as Markdown inline
            },
            ...
          ]
        """
        import pdfplumber

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(file_content)
                temp_path = tmp.name

            sections_dict: Dict[str, Dict[str, Any]] = {}
            last_sub_tracker: Dict[str, Optional[str]] = {}
            table_global_count = 0

            with pdfplumber.open(temp_path) as pdf:
                toc_map = _extract_toc_mapping(pdf)

                for i, page in enumerate(pdf.pages):
                    curr_page = i + 1
                    sec_n, sec_r, sub_n, sub_r = _get_metadata_for_page(
                        toc_map, curr_page
                    )

                    # Initialise section bucket
                    if sec_n not in sections_dict:
                        sections_dict[sec_n] = {
                            "sectionName": sec_n,
                            "sectionStart&End": sec_r,
                            "text": "",
                        }
                        last_sub_tracker[sec_n] = None

                    # Inject subsection marker when it changes
                    if (
                        sub_n != last_sub_tracker[sec_n]
                        and sub_n != "no subsection"
                    ):
                        sections_dict[sec_n]["text"] += (
                            f"\n\n[subsection: {sub_n}, range: {sub_r}]\n"
                        )
                        last_sub_tracker[sec_n] = sub_n

                    sections_dict[sec_n]["text"] += (
                        f"\n\n--- [Page: {curr_page}] ---\n"
                    )

                    # Find tables sorted by vertical position
                    table_objects = page.find_tables(
                        {
                            "vertical_strategy": "lines",
                            "horizontal_strategy": "lines",
                            "snap_tolerance": 3,
                            "join_tolerance": 3,
                        }
                    )
                    table_objects.sort(key=lambda t: t.bbox[1])

                    last_y = 0
                    for table_obj in table_objects:
                        # Text above the table
                        if table_obj.bbox[1] > last_y:
                            above_bbox = (0, last_y, page.width, table_obj.bbox[1])
                            text_above = page.within_bbox(above_bbox).extract_text()
                            if text_above:
                                sections_dict[sec_n]["text"] += text_above + "\n"

                        # Extract and validate table
                        grid = table_obj.extract()
                        if grid:
                            rows = [
                                [
                                    str(c).replace("\n", " ").strip() if c else ""
                                    for c in row
                                ]
                                for row in grid
                                if any(row)
                            ]
                            if len(rows) >= 2 and len(rows[0]) >= 2:
                                # Heading lookback (100 px)
                                h_top = max(0, table_obj.bbox[1] - 100)
                                h_text = page.within_bbox(
                                    (0, h_top, page.width, table_obj.bbox[1])
                                ).extract_text()
                                description = "No Heading Found"
                                if h_text:
                                    h_lines = [
                                        ln.strip()
                                        for ln in h_text.split("\n")
                                        if len(ln.strip()) > 5
                                    ]
                                    if h_lines:
                                        description = (
                                            " ".join(h_lines[-2:])
                                            if len(h_lines) >= 2
                                            else h_lines[-1]
                                        )

                                table_global_count += 1
                                table_md = _to_markdown_table(rows)
                                sections_dict[sec_n]["text"] += (
                                    f"\n\ntable_{table_global_count}:\n{table_md}\n"
                                    f"table_description: {description}, "
                                    f"subsection: {sub_n}, "
                                    f"subSectionStart&End: {sub_r}\n\n"
                                )

                        last_y = max(last_y, table_obj.bbox[3])

                    # Remaining text after last table
                    if last_y < page.height:
                        remaining = page.within_bbox(
                            (0, last_y, page.width, page.height)
                        ).extract_text()
                        if remaining:
                            sections_dict[sec_n]["text"] += remaining + "\n"

            # Clean each section's text
            result = []
            for sec in sections_dict.values():
                sec["text"] = _clean_section_text(sec["text"])
                if len(sec["text"]) >= 20:
                    result.append(sec)

            logger.info(
                "PDF sections extracted",
                section_count=len(result),
                table_count=table_global_count,
            )
            return result

        except Exception as e:
            logger.error("PDF section extraction failed", error=str(e), exc_info=True)
            raise
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    # ----------------------------------------------------------------------- #
    # Legacy flat-text extraction (kept for backward compatibility)
    # ----------------------------------------------------------------------- #
    @staticmethod
    def clean_text(text: str) -> str:
        if not text:
            return ""
        text = text.replace("\n", " ")
        text = re.sub(r"\s{2,}", " ", text)
        text = re.sub(r"Page\s\d+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*[\-_]{3,}\s*", "", text)
        return text.strip()

    @staticmethod
    def extract_text(
        file_content: bytes,
        file_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Legacy entry-point — returns a flat 'text' string.
        Delegates to section extraction + joins sections.
        """
        logger.info("Starting document extraction (legacy)", file_type=file_type)

        if file_type.lower() == "pdf":
            sections = ExtractionService.extract_sections_from_pdf(file_content)
            combined_text = " ".join(
                s["text"] for s in sections if s.get("text")
            )
        elif file_type.lower() == "txt":
            raw = file_content.decode("utf-8")
            combined_text = ExtractionService.clean_text(raw)
        else:
            raise ValueError(f"Unsupported file type: {file_type}")

        return {
            "text": combined_text,
            "file_type": file_type,
            "char_count": len(combined_text),
            "metadata": metadata or {},
        }


# Global service instance
extraction_service = ExtractionService()
