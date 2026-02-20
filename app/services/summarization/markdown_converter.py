"""
Markdown Converter for Summary Pipeline
Converts Agent JSON outputs to markdown format
Matches n8n workflow conversion nodes
"""
import re
from typing import Dict, Any, List, Optional


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
        investor_json: Dict[str, Any]
    ) -> str:
        """
        Converts Agent 1 JSON output to markdown tables.
        Replicates: investors data MDN converter node
        """
        if not investor_json or not isinstance(investor_json, dict):
            return ""
        
        company_name = investor_json.get("company_name", "Company Name Not Found")
        total_share_issue = investor_json.get("total_share_issue", 0)
        investors = self._safe_get_list(investor_json, "section_a_extracted_investors")
        
        # Process and add Others row
        processed_investors = []
        for inv in investors:
            if isinstance(inv, dict):
                processed_investors.append(inv)
        
        if total_share_issue > 0:
            total_extracted = sum(inv.get("number_of_equity_shares", 0) for inv in processed_investors)
            if total_extracted < total_share_issue:
                others_shares = total_share_issue - total_extracted
                processed_investors.append({
                    "investor_name": "Others",
                    "number_of_equity_shares": others_shares,
                    "investor_category": "Public"
                })
            
            # Recalculate percentages
            for inv in processed_investors:
                shares = inv.get("number_of_equity_shares", 0)
                pct = (shares / total_share_issue) * 100
                inv["percentage_of_pre_issue_capital"] = f"{pct:.2f}%"

        # Calculate totals
        total_extracted_shares = sum(inv.get("number_of_equity_shares", 0) for inv in processed_investors)
        total_percentage_numeric = 0
        for inv in processed_investors:
            try:
                pct_str = str(inv.get("percentage_of_pre_issue_capital", "0%")).replace('%', '').strip()
                total_percentage_numeric += float(pct_str)
            except (ValueError, TypeError):
                continue
        total_percentage_str = f"{total_percentage_numeric:.2f}%"
        
        # Build Summary Markdown
        markdown = f"""## Complete Investors & Share Capital History Tables

**Company Name:** {company_name}

**Total Share Issue:** {total_share_issue:,}

**Total Investors Extracted:** {len(processed_investors)}

**Total Extracted Shares:** {total_extracted_shares:,}

**Total Extracted %:** {total_percentage_str}

---

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
                pct_display = inv.get("percentage_of_pre_issue_capital", "0%")
                category = inv.get("investor_category", "N/A")
                markdown += f"| {name} | {shares:,} | {pct_display} | {category} |\n"
            
            markdown += f"| **TOTAL** | **{total_extracted_shares:,}** | **{total_percentage_str}** | - |\n"
        
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
        
        markdown = ""
        
        # Add Part 1: Share Capital History Table
        if markdown_table:
            markdown += "### PART 1: CAPTURED SHARE CAPITAL HISTORY\n\n"
            markdown += markdown_table + "\n\n---\n\n"
        
        # Add Part 2: Premium Rounds (Valuation Analysis)
        if include_valuation_analysis and premium_rounds:
            markdown += "### PART 2: PREMIUM ROUNDS & VALUATION ANALYSIS\n\n"
            for idx, round_data in enumerate(premium_rounds, 1):
                if not isinstance(round_data, dict):
                    continue
                    
                shares = round_data.get("shares_allotted", 0)
                price = round_data.get("issue_price", 0)
                face = round_data.get("face_value", 0)
                cumulative = round_data.get("cumulative_equity_shares", 0)
                
                # Recalculations as per n8n "calculatoer valuation" node
                round_raised = shares * price
                dilution = shares / cumulative if cumulative > 0 else 0
                post_money = round_raised / dilution if dilution > 0 else 0
                
                markdown += f"""
#### Premium Round {idx}

| Field | Value |
|---|---|
| Row Number | {round_data.get('row_number', 'N/A')} |
| Date of Allotment | {round_data.get('date_of_allotment', 'N/A')} |
| Nature of Allotment | {round_data.get('nature_of_allotment', 'N/A')} |
| Shares Allotted | {shares:,} |
| Face Value (\u20b9) | {face:,.2f} |
| Issue Price (\u20b9) | {price:,.2f} |
| Cumulative Equity Shares | {cumulative:,} |
| Round Raised (\u20b9) | {round_raised:,.2f} |
| Dilution (Decimal) | {dilution:.4f} |
| Dilution (%) | {dilution * 100:.2f}% |
| Post Money Valuation (\u20b9) | {post_money:,.2f} |
"""
        
        if not markdown:
            return "\n### No share capital history or premium rounds found.\n"
            
        return markdown
    
    def convert_research_json_to_markdown(
        self,
        research_json: Dict[str, Any]
    ) -> str:
        """
        Converts research JSON to markdown.
        Replicates: convert in mdn node (for Perplexity)
        """
        if not research_json or not isinstance(research_json, dict):
            return ""
            
        # Safely extract dictionaries
        meta = self._safe_get_dict(research_json, "metadata")
        exec_sum = self._safe_get_dict(research_json, "executive_summary")
        risk_ass = self._safe_get_dict(research_json, "risk_assessment")
        detailed = self._safe_get_dict(research_json, "detailed_findings")
        network = self._safe_get_dict(research_json, "entity_network")
        red_flags_count = self._safe_get_dict(exec_sum, "red_flags_count")
        
        company = meta.get("company", "Unknown Company")
        adverse_flag = exec_sum.get("adverse_flag", False)
        risk_level = exec_sum.get("risk_level", "Not Rated")
        
        # Action Badge Logic (Matches n8n getActionBadge)
        action = exec_sum.get("recommended_action", "N/A")
        badges = {
            'proceed': 'Proceed',
            'proceed_with_caution': 'Proceed with Caution',
            'enhanced_due_diligence': 'Enhanced Due Diligence Required',
            'do_not_proceed': 'Do Not Proceed'
        }
        action_display = f"**{badges.get(action, action)}**"

        markdown = f"""
| Field | Value |
|---|---|
| Company | {company} |
| Adverse Flag | {'YES' if adverse_flag else 'NO'} |
| Risk Level | {risk_level} |

**Promoters/Key Persons:** {meta.get('promoters', 'N/A')}

**Key Findings:** {exec_sum.get('key_findings', 'No findings available.')}

**Recommended Action:** {action_display}

### Red Flags Summary

| Category | Count |
|---|---|
| Sanctions | {red_flags_count.get('sanctions', 0)} |
| Enforcement Actions | {red_flags_count.get('enforcement_actions', 0)} |
| Criminal Cases | {red_flags_count.get('criminal_cases', 0)} |
| High-Risk Media | {red_flags_count.get('high_risk_media', 0)} |

---

## Investigation Scope

**Jurisdictions:** {', '.join(self._safe_get_list(meta, 'jurisdictions_searched'))}

**Total Sources Checked:** {meta.get('total_sources_checked', 0)}

---

## Detailed Findings

### Layer 1: Sanctions & Debarment
{self._format_research_items(self._safe_get_list(detailed, 'layer1_sanctions'), "Sanctions")}

### Layer 2: Legal & Regulatory Actions
{self._format_research_items(self._safe_get_list(detailed, 'layer2_legal_regulatory'), "Legal")}

### Layer 3: OSINT & Media Intelligence
{self._format_research_items(self._safe_get_list(detailed, 'layer3_osint_media'), "Media")}

---

## 📈 Multi-Dimensional Risk Assessment

| Risk Category | Level |
|---|---|
| Financial Crime | {risk_ass.get('financial_crime_risk', 'N/A')} |
| Regulatory Compliance | {risk_ass.get('regulatory_compliance_risk', 'N/A')} |
| Reputational | {risk_ass.get('reputational_risk', 'N/A')} |
| Sanctions | {risk_ass.get('sanctions_risk', 'N/A')} |
| Litigation | {risk_ass.get('litigation_risk', 'N/A')} |

**Overall Risk Score:** {risk_ass.get('overall_risk_score', 0)}/10
"""
        
        # Risk Factors
        factors = self._safe_get_list(risk_ass, "risk_factors")
        if factors:
            markdown += "\n### Key Risk Factors\n\n"
            markdown += "\n".join([f"- {f}" for f in factors]) + "\n"

        markdown += "\n---\n"

        # Entity Network (Matches n8n Entity Network section)
        assoc_cos = self._safe_get_list(network, "associated_companies")
        assoc_pers = self._safe_get_list(network, "associated_persons")
        beneficial = self._safe_get_list(network, "beneficial_owners_identified")
        related = self._safe_get_list(network, "related_entities_in_adverse_actions")

        if any([assoc_cos, assoc_pers, beneficial, related]):
            markdown += "\n## 🕸️ Entity Network\n\n"
            if assoc_cos:
                markdown += "**Associated Companies:**\n" + "\n".join([f"- {c}" for c in assoc_cos]) + "\n\n"
            if assoc_pers:
                markdown += "**Associated Persons:**\n" + "\n".join([f"- {p}" for p in assoc_pers]) + "\n\n"
            if beneficial:
                markdown += "**Beneficial Owners:**\n" + "\n".join([f"- {o}" for o in beneficial]) + "\n\n"
            if related:
                markdown += "**Related Entities in Adverse Actions:**\n" + "\n".join([f"- {r}" for r in related]) + "\n\n"
            markdown += "---\n"

        # Next Steps
        next_steps = self._safe_get_list(research_json, "next_steps")
        if next_steps:
            markdown += "\n## Recommended Next Steps\n\n"
            markdown += "\n".join([f"{i+1}. {step}" for i, step in enumerate(next_steps)]) + "\n\n---\n"

        # Gaps
        gaps = self._safe_get_list(research_json, "gaps_and_limitations")
        if gaps:
            markdown += "\n## Investigation Gaps & Limitations\n\n"
            markdown += "\n".join([f"- {gap}" for gap in gaps]) + "\n\n---\n"

        markdown += "\n## Footer\n\n> This report was generated using automated OSINT and regulatory database searches. All findings should be independently verified.\n"
        return markdown

    def _format_research_items(self, items: List[Any], category: str) -> str:
        if not items or not isinstance(items, list):
            return f" No {category.lower()} records found\n"
        
        md = ""
        for item in items:
            if not isinstance(item, dict):
                md += f"- {str(item)}\n"
                continue
            
            # Format depends on category (Matches n8n helpers)
            if category == "Sanctions":
                md += f"**List:** {item.get('list_name', 'N/A')}\n\n"
                md += f"**Matched Entity:** {item.get('matched_entity', 'N/A')}\n\n"
                md += f"**Role:** {item.get('role', 'N/A')}\n\n"
                md += f"**Reason:** {item.get('reason', 'N/A')}\n\n"
                if item.get('action_date'): md += f"**Date:** {item.get('action_date')}\n\n"
                if item.get('document_id'): md += f"**Document ID:** {item.get('document_id')}\n\n"
                
            elif category == "Legal":
                md += f"**Type:** {item.get('action_type', 'N/A')}\n\n"
                if item.get('case_number'): md += f"**Case Number:** {item.get('case_number')}\n\n"
                if item.get('filing_date'): md += f"**Filing Date:** {item.get('filing_date')}\n\n"
                md += f"**Parties:** {item.get('parties', 'N/A')}\n\n"
                if item.get('jurisdiction'): md += f"**Jurisdiction:** {item.get('jurisdiction')}\n\n"
                md += f"**Allegations:** {item.get('allegations', item.get('summary', 'N/A'))}\n\n"
                if item.get('key_findings'): md += f"**Key Findings:** {item.get('key_findings')}\n\n"
                if item.get('penalties'): md += f"**Penalties:** {item.get('penalties')}\n\n"
                md += f"**Status:** {item.get('final_disposition', item.get('disposition', 'N/A'))}\n\n"
                if item.get('appeal_status'): md += f"**Appeal Status:** {item.get('appeal_status')}\n\n"
                
            elif category == "Media":
                md += f"**Headline:** {item.get('headline', 'N/A')}\n\n"
                if item.get('publication'): md += f"**Publication:** {item.get('publication')}\n\n"
                if item.get('date'): md += f"**Date:** {item.get('date')}\n\n"
                md += f"**Summary:** {item.get('snippet', item.get('allegations', 'N/A'))}\n\n"
                md += f"**Source Type:** {item.get('source_type', 'N/A')} | **Risk Level:** {item.get('risk_label', 'N/A')}\n\n"
            
            # Related Entities
            related = self._safe_get_list(item, "related_entities")
            if related:
                md += f"**Related Entities:** {', '.join([str(r) for r in related])}\n\n"

            # Source URL
            if item.get('source_url') or item.get('url'):
                url = item.get('source_url') or item.get('url')
                md += f"**Source:** [{url}]({url})\n\n"
            
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
