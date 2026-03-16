"""
Markdown Converter for Summary Pipeline
Converts Agent JSON outputs to markdown format
Matches n8n workflow conversion nodes
"""
import re
from typing import Dict, Any, List, Optional
from app.services.summarization.prompts import TARGET_INVESTORS


class MarkdownConverter:
    """
    Converts JSON outputs from agents to markdown format.
    Replicates n8n JavaScript conversion nodes.
    """
    
    def _safe_get_dict(self, data: Any, key: str) -> Dict[str, Any]:
        """Safely extract a dictionary from a potential dictionary."""
        if not isinstance(data, dict):
            return {}
        val = data.get(key)
        return val if isinstance(val, dict) else {}

    def _safe_get_list(self, data: Any, key: str) -> List[Any]:
        """Safely extract a list from a potential dictionary."""
        if not isinstance(data, dict):
            return []
        val = data.get(key)
        return val if isinstance(val, list) else []
    
    def convert_investor_json_to_markdown(
        self,
        investor_json: Dict[str, Any],
        target_investors: List[str] = None,
        investor_match_only: bool = False
    ) -> str:
        """
        Converts Agent 1 JSON output to markdown tables.
        Replicates: investors data calculation + investors data MDN converter nodes

        Steps (matching n8n exactly):
          1. Extract investors + total_share_issue
          2. Add 'Others' row if extracted shares < total
          3. Recalculate percentages with full precision (no rounding)
          4. Match against TARGET_INVESTORS list (case-insensitive exact match)
          5. Build Section A table (all investors) + Section B table (matched only)
        """
        if not investor_json or not isinstance(investor_json, dict):
            return ""

        company_name = investor_json.get("company_name", "Company Name Not Found")
        total_share_issue = investor_json.get("total_share_issue", 0)
        investors = self._safe_get_list(investor_json, "section_a_extracted_investors")

        # -- Step 1: Build processed list --
        processed_investors = [inv for inv in investors if isinstance(inv, dict)]

        # -- Step 2: Add Others row if needed (matches n8n addOthersRowIfNeeded) --
        if total_share_issue > 0:
            total_extracted = sum(
                inv.get("number_of_equity_shares", 0) for inv in processed_investors
            )
            if total_extracted < total_share_issue:
                others_shares = total_share_issue - total_extracted
                processed_investors.append(
                    {
                        "investor_name": "Others",
                        "number_of_equity_shares": others_shares,
                        "investor_category": "Public",
                        "is_others_row": True,
                    }
                )

        # -- Step 3: Recalculate percentages with FULL precision (no rounding) --
        # Matches n8n recalculatePercentages: toFixed(10).replace(/\.?0+$/, '') + "%"
        if total_share_issue > 0:
            for inv in processed_investors:
                shares = inv.get("number_of_equity_shares", 0)
                pct_value = (shares / total_share_issue) * 100
                # Full precision string (strip trailing zeroes, same as JS toFixed(10).replace...)
                pct_str = f"{pct_value:.10f}".rstrip("0").rstrip(".") + "%"
                inv["percentage_of_pre_issue_capital"] = pct_str
                inv["_calculated_percentage"] = pct_value  # raw numeric

        # -- Section A totals --
        total_extracted_shares = sum(
            inv.get("number_of_equity_shares", 0) for inv in processed_investors
        )
        total_pct_numeric = sum(
            inv.get("_calculated_percentage", 0) for inv in processed_investors
        )
        total_pct_str = f"{total_pct_numeric:.2f}%"

        # -- Step 4: Match against TARGET_INVESTORS (case-insensitive exact match) --
        active_targets = target_investors if target_investors else TARGET_INVESTORS
        target_lower = {name.lower().strip() for name in active_targets}

        matched_investors = []
        for inv in processed_investors:
            if inv.get("is_others_row"):
                continue
            name = inv.get("investor_name", "")
            if not name:
                continue
            if str(name).lower().strip() in target_lower:
                matched_investors.append(
                    {
                        "investor_name": name,
                        "number_of_equity_shares": inv.get("number_of_equity_shares", 0),
                        "percentage_of_capital": inv.get("percentage_of_pre_issue_capital", "0%"),
                        "investor_category": inv.get("investor_category", "Unknown"),
                    }
                )

        # -- Step 5: Build markdown --
        # Summary header
        markdown = f"""## Matched Investors & Analysis
        
**Company Name:** {company_name}

**Total Share Issue:** {total_share_issue:,}

**Total Investors Extracted:** {len(processed_investors)}

**Total Extracted Shares:** {total_extracted_shares:,}

**Total Extracted %:** {total_pct_str}

---
"""
        if not investor_match_only:
            markdown += f"""
## SECTION A: COMPLETE INVESTOR LIST FROM DRHP

| Investor Name | Number of Equity Shares | % of Pre-Issue Capital | Investor Category |
|---|---|---|---|
"""
            if not processed_investors:
                markdown += "| No investors found | - | - | - |\n"
            else:
                for inv in processed_investors:
                    name = inv.get("investor_name", "N/A")
                    shares = inv.get("number_of_equity_shares", 0)
                    pct = inv.get("percentage_of_pre_issue_capital", "0%")
                    cat = inv.get("investor_category", "N/A")
                    markdown += f"| {name} | {shares:,} | {pct} | {cat} |\n"
                markdown += f"| **TOTAL** | **{total_extracted_shares:,}** | **{total_pct_str}** | - |\n"

            markdown += "\n"

        # -- Section B: Matched Target Investors --
        matched_total_shares = sum(
            m["number_of_equity_shares"] for m in matched_investors
        )
        matched_total_pct_numeric = sum(
            float(str(m["percentage_of_capital"]).replace("%", ""))
            for m in matched_investors
            if m["percentage_of_capital"]
        )
        matched_total_pct_str = f"{matched_total_pct_numeric:.2f}%"

        markdown += "## SECTION B: MATCHED TARGET INVESTORS\n\n"

        if matched_investors:
            matched_status = "MATCH_FOUND"
            markdown += (
                f"**Matched Status:** {matched_status}  \n"
                f"**Total Matched Investors:** {len(matched_investors)}\n\n"
            )
            markdown += (
                "| Investor Name | Number of Equity Shares "
                "| % of Capital | Investor Category |\n"
                "|---|---|---|---|\n"
            )
            for m in matched_investors:
                markdown += (
                    f"| {m['investor_name']} "
                    f"| {m['number_of_equity_shares']:,} "
                    f"| {m['percentage_of_capital']} "
                    f"| {m['investor_category']} |\n"
                )
            markdown += (
                f"| **TOTAL** | **{matched_total_shares:,}** "
                f"| **{matched_total_pct_str}** | - |\n"
            )
        else:
            markdown += (
                "**Matched Status:** NO_MATCH_FOUND  \n"
                "No investors from the TARGET_INVESTORS list were found "
                "in the extracted investor list.\n"
            )

        markdown += "\n"
        return markdown
    
    def convert_capital_json_to_markdown(
        self,
        capital_json: Dict[str, Any],
        include_valuation_analysis: bool = True
    ) -> str:
        """
        Converts Agent 2 JSON output to markdown tables.
        Replicates: valuation MDN conveter node
        """
        if not capital_json or not isinstance(capital_json, dict):
            return ""
        
        calc_params = self._safe_get_dict(capital_json, "calculation_parameters")
        premium_rounds = self._safe_get_list(calc_params, "premium_rounds")
        table_info = self._safe_get_dict(calc_params, "table_data")
        markdown_table = table_info.get("markdown_table")
        share_capital_history = self._safe_get_list(capital_json, "share_capital_history")
        
        markdown = ""
        
        def clean_number(val: Any) -> float:
            if not val:
                return 0.0
            s = str(val).replace(",", "").replace("₹", "").replace("/-", "").replace("/ -", "")
            s = s.replace("N.A", "").replace("NA", "").replace("Nil", "").strip()
            import re
            s = re.sub(r'[^\d.]', '', s).strip()
            if not s:
                return 0.0
            try:
                return float(s)
            except ValueError:
                return 0.0

        def format_num(val: Any) -> str:
            if not val or val == 0 or val == 0.0:
                return "-"
            try:
                # Use standard comma formatting
                return f"{float(val):,.2f}".replace(".00", "")
            except Exception:
                return str(val)

        # Build full table matching n8n logic
        if share_capital_history:
            header = [
              "Sr No", "Date of Allotment", "Nature of Allotment", "Shares Allotted",
              "Face Value", "Issue Price", "Nature of Consideration", "Cumulative Equity Shares",
              "Cumulative Paid-up Capital", "Round Raised (\u20b9)", "Dilution (Decimal)",
              "Dilution (%)", "Post Money Valuation (\u20b9)"
            ]
            
            markdown += "## Share Capital History With Valuation\n\n"
            markdown += "| " + " | ".join(header) + " |\n"
            markdown += "|" + "|".join(["---"] * len(header)) + "|\n"
            
            for r in share_capital_history:
                if not isinstance(r, dict):
                    continue
                
                shares = clean_number(r.get("shares_allotted", ""))
                face = clean_number(r.get("face_value", ""))
                price = clean_number(r.get("issue_price", ""))
                cumulative = clean_number(r.get("cumulative_equity_shares", ""))
                
                roundRaised = "-"
                dilutionDecimal = "-"
                dilutionPercent = "-"
                postMoney = "-"
                
                if shares > 0 and face > 0 and price > face:
                    raised = shares * price
                    dilution = shares / cumulative if cumulative > 0 else 0
                    valuation = raised / dilution if dilution > 0 else 0
                    
                    roundRaised = format_num(raised)
                    dilutionDecimal = f"{dilution:.4f}"
                    dilutionPercent = f"{float(dilution) * 100:.2f}%"
                    postMoney = format_num(valuation)
                
                row = [
                    str(r.get("sr_no", "")).replace("\n", " ") or "",
                    str(r.get("date_of_allotment", "")).replace("\n", " ") or "",
                    str(r.get("nature_of_allotment", "")).replace("\n", " ") or "",
                    str(r.get("shares_allotted", "")).replace("\n", " ") or "",
                    str(r.get("face_value", "")).replace("\n", " ") or "",
                    str(r.get("issue_price", "")).replace("\n", " ") or "",
                    str(r.get("nature_of_consideration", "")).replace("\n", " ") or "",
                    str(r.get("cumulative_equity_shares", "")).replace("\n", " ") or "",
                    str(r.get("cumulative_paid_up_capital", "")).replace("\n", " ") or "",
                    roundRaised,
                    dilutionDecimal,
                    dilutionPercent,
                    postMoney
                ]
                markdown += "| " + " | ".join(row) + " |\n"
                
            markdown += "\n---\n\n"
        elif markdown_table:
            # Add Part 1: Share Capital History Table fallback
            markdown += "### PART 1: CAPTURED SHARE CAPITAL HISTORY\n\n"
            markdown += markdown_table + "\n\n---\n\n"
        
        # Add Part 2: Premium Rounds (Valuation Analysis)
        if include_valuation_analysis and premium_rounds:
            markdown += "### PART 2: PREMIUM ROUNDS & VALUATION ANALYSIS\n\n"
            for idx, round_data in enumerate(premium_rounds, 1):
                if not isinstance(round_data, dict):
                    continue
                    
                shares = clean_number(round_data.get("shares_allotted", ""))
                price = clean_number(round_data.get("issue_price", ""))
                face = clean_number(round_data.get("face_value", ""))
                cumulative = clean_number(round_data.get("cumulative_equity_shares", ""))
                
                # Recalculations as per n8n "calculatoer valuation" node
                round_raised = shares * price
                dilution = shares / cumulative if cumulative > 0 else 0
                post_money = round_raised / dilution if dilution > 0 else 0
                
                markdown += f"""#### Premium Round {idx}

| Field | Value |
|---|---|
| Row Number | {round_data.get('row_number', 'N/A')} |
| Date of Allotment | {round_data.get('date_of_allotment', 'N/A')} |
| Nature of Allotment | {round_data.get('nature_of_allotment', 'N/A')} |
| Shares Allotted | {shares:,.0f} |
| Face Value (\u20b9) | {face:,.2f} |
| Issue Price (\u20b9) | {price:,.2f} |
| Cumulative Equity Shares | {cumulative:,.0f} |
| Round Raised (\u20b9) | {round_raised:,.2f} |
| Dilution (Decimal) | {dilution:.4f} |
| Dilution (%) | {dilution * 100:.2f}% |
| Post Money Valuation (\u20b9) | {post_money:,.2f} |

"""
        
        if not markdown:
            return "\n### No share capital history or premium rounds found.\n"
            
        return markdown

    # ------------------------------------------------------------------
    # Adverse Findings Markdown Converter
    # Exact Python port of n8n "convert in mdn3" JavaScript code node
    # ------------------------------------------------------------------

    def convert_research_json_to_markdown(self, research_json: Dict[str, Any]) -> str:
        """
        Converts OpenAI web-search JSON to a Compliance Investigation Report.
        Matches n8n 'convert in mdn3' code node output character-for-character.
        """
        if not research_json or not isinstance(research_json, dict):
            return ""

        metadata        = research_json.get("metadata", {}) or {}
        exec_sum        = research_json.get("executive_summary", {}) or {}
        detailed        = research_json.get("detailed_findings", {}) or {}
        entity_network  = research_json.get("entity_network", {}) or {}
        risk_assessment = research_json.get("risk_assessment", {}) or {}
        gaps            = research_json.get("gaps_and_limitations", []) or []
        next_steps      = research_json.get("next_steps", []) or []

        # ---- metadata fields ----
        company                = metadata.get("company", "Unknown Company")
        promoters              = metadata.get("promoters", "Not Available")
        investigation_date     = metadata.get("investigation_date", "N/A")
        jurisdictions_searched = metadata.get("jurisdictions_searched", []) or []
        total_sources_checked  = metadata.get("total_sources_checked", 0)

        # ---- executive_summary fields ----
        adverse_flag        = exec_sum.get("adverse_flag", False)
        risk_level          = exec_sum.get("risk_level", "Not Rated")
        confidence_overall  = exec_sum.get("confidence_overall", 0)
        key_findings        = exec_sum.get("key_findings", "No findings available.")
        red_flags_count     = exec_sum.get("red_flags_count", {}) or {}
        recommended_action  = exec_sum.get("recommended_action", "N/A")

        # ---- risk_assessment fields ----
        financial_crime_risk      = risk_assessment.get("financial_crime_risk", "N/A")
        regulatory_compliance_risk = risk_assessment.get("regulatory_compliance_risk", "N/A")
        reputational_risk         = risk_assessment.get("reputational_risk", "N/A")
        sanctions_risk            = risk_assessment.get("sanctions_risk", "N/A")
        litigation_risk           = risk_assessment.get("litigation_risk", "N/A")
        overall_risk_score        = risk_assessment.get("overall_risk_score", 0)
        risk_factors              = risk_assessment.get("risk_factors", []) or []

        # ---- detailed_findings ----
        layer1 = detailed.get("layer1_sanctions", []) or []
        layer2 = detailed.get("layer2_legal_regulatory", []) or []
        layer3 = detailed.get("layer3_osint_media", []) or []

        # ---- entity_network ----
        associated_companies              = entity_network.get("associated_companies", []) or []
        associated_persons                = entity_network.get("associated_persons", []) or []
        beneficial_owners_identified      = entity_network.get("beneficial_owners_identified", []) or []
        related_entities_in_adverse_actions = entity_network.get("related_entities_in_adverse_actions", []) or []

        # ---- Helper: formatRiskLevel ----
        risk_map = {"Low": "🟢 Low", "Moderate": "🟡 Moderate", "High": "🔴 High", "Critical": "🔴 Critical"}
        formatted_risk = risk_map.get(risk_level, risk_level)

        # ---- Helper: getActionBadge ----
        badge_map = {
            "proceed":                            "✅ Proceed",
            "proceed_with_caution":               "⚠️ Proceed with Caution",
            "enhanced_due_diligence":             "🔍 Enhanced Due Diligence Required",
            "enhanced_monitoring_and_verification": "🔍 Enhanced Monitoring & Verification",
            "do_not_proceed":                     "❌ Do Not Proceed",
        }
        action_badge = badge_map.get(str(recommended_action).lower(), recommended_action)

        # ---- Helper: promoterList ----
        if isinstance(promoters, list):
            def _fmt_p(p):
                if isinstance(p, dict):
                    return f"{p.get('name', p.get('full_name', 'Unknown'))} ({p.get('role', 'Unknown Role')})"
                return str(p)
            promoter_list = ", ".join(_fmt_p(p) for p in promoters)
        elif isinstance(promoters, str):
            promoter_list = promoters
        else:
            promoter_list = "Not Available"

        # ---- Layer 1: Sanctions ----
        def _layer1_md(items):
            if not items:
                return "✅ **Result:** No sanctions or international debarment records found.\n"
            return "\n".join(
                f"- **{i.get('list_name', 'Unknown List')}**: {i.get('summary', 'No details')}"
                for i in items
            )

        # ---- Layer 2: Legal & Regulatory ----
        def _layer2_md(items):
            if not items:
                return "**Result:** No legal or regulatory enforcement actions found.\n\n"
            md = ""
            for item in items:
                authority      = item.get("authority", "Unknown Authority")
                document_id    = item.get("document_id", item.get("case_id", "N/A"))
                document_type  = item.get("document_type", "Legal Document")
                date_of_order  = item.get("date_of_order", "N/A")
                summary        = item.get("summary", "No summary available")
                case_status    = item.get("case_status", "Unknown")
                final_judgment = item.get("final_judgment", "Not determined")
                doc_anchor     = re.sub(r"[^\w]", "-", str(document_id))

                md += f"#### ⚖️ {document_type}\n\n"
                md += f"**Authority:** {authority}\n\n"
                md += f"**Document ID:** [{document_id}](#{doc_anchor})\n\n"
                md += f"**Date:** {date_of_order}\n\n"
                md += f"**Summary:** {summary}\n\n"
                md += f"**Status:** {case_status}\n\n"
                md += f"**Judgment:** {final_judgment}\n\n"

                entities = item.get("entities_mentioned", []) or []
                if entities and isinstance(entities, list):
                    md += "**Entities Mentioned:**\n"
                    for e in entities:
                        md += f"- {e}\n"
                    md += "\n"
                md += "---\n\n"
            return md

        # ---- Layer 3: OSINT / Media ----
        def _layer3_md(items):
            if not items:
                return "**Result:** No adverse media coverage found.\n\n"
            md = ""
            for item in items:
                source    = item.get("source", "Unknown Source")
                date      = item.get("date", "N/A")
                summary   = item.get("summary", "No summary available")
                relevance = item.get("relevance", "N/A")
                md += f"#### 📰 {source}\n\n"
                md += f"**Date:** {date}\n\n"
                md += f"**Content:** {summary}\n\n"
                md += f"**Relevance:** {relevance}\n\n"
                md += "---\n\n"
            return md

        # ---- Helper: formatPersonWithIdentifiers ----
        def _fmt_person(person):
            if isinstance(person, str):
                return person
            name = person.get("name", "")
            if not name:
                return str(person)
            line = f"**{name}**"
            if person.get("role"):
                line += f" - {person['role']}"
            line += "\n"
            identifiers = person.get("identifiers", {}) or {}
            if isinstance(identifiers, dict):
                for k, v in identifiers.items():
                    if v and k != "role_source":
                        line += f"- {k}: {v}\n"
            return line

        # ---- Helper: formatCompanyWithRelationship ----
        def _fmt_company(company_item):
            if isinstance(company_item, str):
                return company_item
            name = company_item.get("name", "")
            if not name:
                return str(company_item)
            line = f"**{name}**"
            if company_item.get("relationship"):
                line += f" - {company_item['relationship']}"
            line += "\n"
            if company_item.get("notes"):
                line += f"  *{company_item['notes']}*\n"
            return line

        # ---- Helper: beneficial owners ----
        def _fmt_owner(owner):
            if isinstance(owner, str):
                return f"- {owner}"
            name = owner.get("name", "Unknown")
            ownership = owner.get("ownership", "Ownership stake identified")
            return f"- **{name}**: {ownership}"

        # ---- Helper: related entities in adverse actions ----
        def _fmt_related_entity(entity):
            if isinstance(entity, str):
                return f"- {entity}"
            ename  = entity.get("entity", "Unknown")
            action = entity.get("adverse_action", "Adverse action identified")
            return f"- **{ename}**: {action}"

        # ---- Entity network block ----
        has_network = any([associated_companies, associated_persons,
                           beneficial_owners_identified, related_entities_in_adverse_actions])
        entity_block = ""
        if has_network:
            assoc_co_str = (
                "\n".join(_fmt_company(c) for c in associated_companies)
                if associated_companies else "No associated companies identified."
            )
            assoc_per_str = (
                "\n".join(_fmt_person(p) for p in associated_persons)
                if associated_persons else "No associated persons identified."
            )
            beneficial_str = (
                "\n".join(_fmt_owner(o) for o in beneficial_owners_identified)
                if beneficial_owners_identified else "No beneficial owners identified."
            )
            related_str = (
                "\n".join(_fmt_related_entity(e) for e in related_entities_in_adverse_actions)
                if related_entities_in_adverse_actions
                else "No entities identified in adverse actions."
            )
            entity_block = f"""

### Associated Companies

{assoc_co_str}

---

### Associated Persons & Key Personnel

{assoc_per_str}

---

### Beneficial Owners

{beneficial_str}

---

### Entities in Adverse Actions

{related_str}

"""

        # ---- Risk factors block ----
        risk_factors_block = ""
        if risk_factors:
            rf_lines = "\n".join(f"- {f}" for f in risk_factors)
            risk_factors_block = f"""
### Contributing Risk Factors

{rf_lines}

"""

        # ---- Next Steps ----
        next_steps_md = (
            "\n\n".join(f"{i+1}. {step}" for i, step in enumerate(next_steps))
            if next_steps else "No specific recommendations at this time."
        )

        # ---- Gaps ----
        gaps_md = (
            "\n".join(f"- **Note:** {gap}" for gap in gaps)
            if gaps else "- No significant gaps identified."
        )

        # ---- Confidence ----
        confidence_pct = int(round(float(confidence_overall) * 100))
        jurisdictions_str = ", ".join(jurisdictions_searched) if jurisdictions_searched else "N/A"

        report = f"""# Compliance Investigation Report

## Executive Summary

| Field | Value |
|-------|-------|
| **Company** | {company} |
| **Investigation Date** | {investigation_date} |
| **Adverse Flag** | {"⚠️ YES" if adverse_flag else "✅ NO"} |
| **Overall Risk Level** | {formatted_risk} |
| **Confidence Score** | {confidence_pct}% |
| **Recommended Action** | {action_badge} |

### Key Findings

{key_findings}

**Promoters/Key Persons:** {promoter_list}

---

## Red Flags Summary

| Category | Count |
|----------|-------|
| Sanctions/Debarments | {red_flags_count.get("sanctions", 0)} |
| Enforcement Actions | {red_flags_count.get("enforcement_actions", 0)} |
| Criminal Cases | {red_flags_count.get("criminal_cases", 0)} |
| High-Risk Media | {red_flags_count.get("high_risk_media", 0)} |

---

## Investigation Scope

**Jurisdictions Searched:** {jurisdictions_str}

**Total Sources Checked:** {total_sources_checked}

---

## Detailed Findings

### Layer 1: Sanctions & International Debarment Lists

{_layer1_md(layer1)}

---

### Layer 2: Legal & Regulatory Actions

{_layer2_md(layer2)}

---

### Layer 3: OSINT & Media Intelligence

{_layer3_md(layer3)}

---

## Multi-Dimensional Risk Assessment

### Risk Ratings

| Risk Category | Assessment |
|---|---|
| **Financial Crime Risk** | {financial_crime_risk} |
| **Regulatory Compliance Risk** | {regulatory_compliance_risk} |
| **Reputational Risk** | {reputational_risk} |
| **Sanctions Risk** | {sanctions_risk} |
| **Litigation Risk** | {litigation_risk} |

**Overall Risk Score:** {overall_risk_score}/10 ({formatted_risk})
{risk_factors_block}

---

## Entity Network & Relationships
{entity_block}

---

## Recommendations & Next Steps

{next_steps_md}

---

## Investigation Gaps & Limitations

{gaps_md}


---

## Disclaimer

> **⚠️ Important Notice:** This report was generated using automated OSINT, open-source regulatory databases, and publicly available information. All findings should be independently verified through official channels and licensed compliance providers before making any material business decisions. This report does not constitute legal, financial, or investment advice.

**For questions or clarifications, refer to original source documents and official regulatory authorities in relevant jurisdictions.**
"""
        return report

    def _format_research_items(self, items: List[Any], category: str) -> str:
        """Legacy helper kept for backward compat — only used by old code paths."""
        if not items or not isinstance(items, list):
            return f" No {category.lower()} records found\n"
        md = ""
        for item in items:
            if not isinstance(item, dict):
                md += f"- {str(item)}\n"
                continue
            summary = item.get("summary", item.get("snippet", "N/A"))
            md += f"- {summary}\n"
            md += "---\n\n"
        return md

    def insert_markdown_before_section(
        self,
        full_markdown: str,
        insert_markdown: str,
        section_header: str,
        section_label: str
    ) -> str:
        """
        Inserts markdown content before a specific section.
        Replicates: combine FULL MDN summary node logic
        
        Args:
            full_markdown: Complete markdown document
            insert_markdown: Markdown to insert
            section_header: Section header to find (e.g., "SECTION VII: FINANCIAL PERFORMANCE")
            section_label: Label for inserted section
        
        Returns:
            Modified markdown with inserted content
        """
        if not full_markdown or not isinstance(full_markdown, str):
            return full_markdown or ""
        
        if not insert_markdown or not isinstance(insert_markdown, str) or not insert_markdown.strip():
            return full_markdown
        
        # Try to find section header (case-insensitive, handles extra spaces)
        # We look for the section title within the line, ignoring extra spaces
        clean_header = re.escape(section_header).replace(r'\ ', r'\s+')
        pattern = rf'(^#{{1,4}}\s+.*{clean_header}.*$)'
        match = re.search(pattern, full_markdown, re.IGNORECASE | re.MULTILINE)
        
        if match:
            insertion_point = match.start()
            insertion_content = f"\n---\n\n## {section_label}\n\n{insert_markdown}\n\n---\n\n"
            return full_markdown[:insertion_point] + insertion_content + full_markdown[insertion_point:]
        else:
            # If section not found, append at end
            insertion_content = f"\n\n---\n\n## {section_label}\n\n{insert_markdown}\n"
            return full_markdown + insertion_content


# Singleton instance
markdown_converter = MarkdownConverter()
