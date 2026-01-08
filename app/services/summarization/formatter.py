"""
Premium HTML Formatter for DRHP summaries.
Ported from n8n mdn to html and mdn to html27 nodes.
"""
import re

class HTMLFormatter:
    def __init__(self):
        self.css = """
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #ffffff; color: #1a1a1a; line-height: 1.6; }
            .container { max-width: 1000px; margin: 0 auto; padding: 20px; }
            h1 { color: #4B2A06; border-bottom: 2px solid #4B2A06; padding-bottom: 10px; }
            h2 { color: #4B2A06; margin-top: 30px; border-bottom: 1px solid #e5e7eb; padding-bottom: 5px; }
            h3 { color: #4B2A06; font-size: 18px; margin-top: 20px; }
            table { border-collapse: collapse; width: 100%; margin: 20px 0; font-size: 13px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
            th { background: #f1f5f9; color: #334155; font-weight: 700; text-align: left; padding: 12px; border: 1px solid #e2e8f0; }
            td { padding: 10px; border: 1px solid #e2e8f0; }
            tr:nth-child(even) { background: #f8fafc; }
            .percentage { color: #059669; font-weight: 600; }
            .currency { color: #b45309; font-weight: 600; }
            .amount { font-weight: 600; color: #1e293b; }
            hr { border: none; border-top: 1px solid #e5e7eb; margin: 30px 0; }
            ul, ol { margin-bottom: 20px; }
            li { margin-bottom: 8px; }
            .report-section { margin-bottom: 40px; }
        </style>
        """

    def insert_html_before_section(self, full_html: str, insert_html: str, section_header: str, section_label: str) -> str:
        """
        Inserts HTML content before a specific section identified by an H2 header.
        Replicates n8n code node logic.
        """
        if not full_html or not isinstance(full_html, str):
            return full_html or ""
        
        if not insert_html or not isinstance(insert_html, str) or not insert_html.strip():
            return full_html
            
        # Try to match H2 or H3 headers that contain the section_header text
        pattern = rf'(<(h2|h3)[^>]*>[^<]*{re.escape(section_header)}[^<]*</\2>)'
        match = re.search(pattern, full_html, re.IGNORECASE)
        
        if match:
            insertion_point = match.start()
            insertion_html = f'<div class="{section_label.lower().replace(" ", "-")}-insertion" style="margin-top:10px; margin-bottom:20px;"><h3 style="color:#4B2A06; font-size:16px; font-weight:700; margin:12px 0;">{section_label}</h3>{insert_html}</div><hr>'
            return full_html[:insertion_point] + insertion_html + full_html[insertion_point:]
        else:
            insertion_html = f'<hr><div class="{section_label.lower().replace(" ", "-")}-insertion" style="margin-top:10px; margin-bottom:20px;"><h3 style="color:#4B2A06; font-size:16px; font-weight:700; margin:12px 0;">{section_label}</h3>{insert_html}</div>'
            return full_html + insertion_html

    def wrap_enhanced_html(self, content_html: str, company_name: str) -> str:
        """
        Wraps content in a full HTML page with styling.
        Replicates n8n generateEnhancedHtml function.
        """
        # Remove excessive whitespace/newlines that cause gaps
        content_html = re.sub(r'(<br>\s*){2,}', '<br>', content_html)
        content_html = content_html.replace('\\n', ' ').replace('\n', ' ')
        
        return f"""
<!DOCTYPE html>
<html>
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>Investment Analysis Report - {company_name}</title>
        {self.css}
        <style>
            .research-card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 20px; margin-bottom: 20px; }}
            .risk-badge {{ display: inline-block; padding: 4px 12px; border-radius: 9999px; font-weight: 600; font-size: 12px; }}
            .risk-low {{ background: #dcfce7; color: #166534; }}
            .risk-moderate {{ background: #fef9c3; color: #854d0e; }}
            .risk-high {{ background: #fee2e2; color: #991b1b; }}
            .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 15px; }}
            .grid-item {{ border-bottom: 1px solid #f3f4f6; padding-bottom: 5px; }}
            .label {{ font-size: 11px; color: #6b7280; text-transform: uppercase; font-weight: 700; }}
            .value {{ font-size: 14px; font-weight: 600; color: #111827; }}
            .finding-item {{ border-left: 3px solid #4B2A06; padding-left: 15px; margin-bottom: 15px; background: #f9fafb; padding: 10px; }}
            .data-table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
            .data-table th {{ background: #f8fafc; padding: 10px; text-align: left; border: 1px solid #e2e8f0; font-size: 12px; }}
            .data-table td {{ padding: 8px; border: 1px solid #e2e8f0; font-size: 12px; }}
            p {{ margin: 0 0 10px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            {content_html}
        </div>
    </body>
</html>"""

    def format_research_report(self, data: dict) -> str:
        """
        Converts Perplexity JSON output to a comprehensive HTML report.
        """
        if not data or "error" in data:
            return f"<div class='research-card'><p>❌ Research Error: {data.get('error', 'No data available')}</p></div>"

        exec_sum = data.get("executive_summary", {})
        risk_ass = data.get("risk_assessment", {})
        detailed = data.get("detailed_findings", {})
        entity = data.get("entity_network", {})
        metadata = data.get("metadata", {})
        gaps = data.get("gaps_and_limitations", [])
        next_steps = data.get("next_steps", [])
        
        risk_level = exec_sum.get("risk_level", "Low")
        risk_class = f"risk-{risk_level.lower()}"
        
        html = f"""
        <div class="research-card" style="border: 2px solid #e5e7eb; border-radius: 12px; overflow: hidden;">
            <div style="background: #f9fafb; padding: 15px; border-bottom: 2px solid #e5e7eb; display: flex; justify-content: space-between; align-items: center;">
                <h2 style="margin: 0; font-size: 18px; color: #111827;">🛡️ ADVERSE FINDINGS & BACKGROUND CHECK</h2>
                <span class="risk-badge {risk_class}" style="padding: 6px 16px; font-size: 14px;">RISK: {risk_level.upper()}</span>
            </div>
            
            <div style="padding: 20px;">
                <div class="grid-2">
                    <div class="grid-item"><div class="label">Adverse Flag</div><div class="value">{'⚠️ YES' if exec_sum.get('adverse_flag') else '✅ NO'}</div></div>
                    <div class="grid-item"><div class="label">Investigation Date</div><div class="value">{metadata.get('investigation_date', '-')}</div></div>
                </div>
                
                <div style="margin-top: 20px;">
                    <div class="label">Executive Summary</div>
                    <div class="value" style="font-weight: 400; line-height: 1.6; margin-top: 8px;">{exec_sum.get('key_findings', 'No adverse findings identified.')}</div>
                </div>

                <h4 style="margin-top: 30px; border-bottom: 1px solid #eee; padding-bottom: 8px;">🔍 Risk Assessment Breakdown</h4>
                <div class="grid-2" style="margin-top: 15px;">
                    <div class="grid-item"><div class="label">Financial Crime</div><div class="value">{risk_ass.get('financial_crime_risk', 'Low')}</div></div>
                    <div class="grid-item"><div class="label">Regulatory</div><div class="value">{risk_ass.get('regulatory_compliance_risk', 'Low')}</div></div>
                    <div class="grid-item"><div class="label">Reputational</div><div class="value">{risk_ass.get('reputational_risk', 'Low')}</div></div>
                    <div class="grid-item"><div class="label">Sanctions</div><div class="value">{risk_ass.get('sanctions_risk', 'Low')}</div></div>
                </div>

                {self._generate_entity_html("👥 Associated Persons", entity.get("associated_persons", []))}
                {self._generate_entity_html("🏢 Associated Companies", entity.get("associated_companies", []))}

                {self._generate_findings_html("🚫 Layer 1: Sanctions & Debarment", detailed.get("layer1_sanctions", []))}
                {self._generate_findings_html("⚖️ Layer 2: Legal & Regulatory Actions", detailed.get("layer2_legal_regulatory", []))}
                {self._generate_findings_html("📰 Layer 3: OSINT & Media Intelligence", detailed.get("layer3_osint_media", []))}
                
                {self._generate_list_html("⚠️ Gaps & Limitations", gaps)}
                {self._generate_list_html("🚀 Recommended Next Steps", next_steps)}

                <div style="font-size: 11px; color: #9ca3af; margin-top: 25px; text-align: right; border-top: 1px solid #f3f4f6; padding-top: 10px;">
                    Sources checked: {metadata.get('total_sources_checked', 0)} | Jurisdictions: {', '.join(metadata.get('jurisdictions_searched', []))}
                </div>
            </div>
        </div>
        """
        return html

    def _generate_entity_html(self, title, entities):
        if not entities:
            return ""
        
        html = f"<h4 style='margin-top: 25px; border-bottom: 1px solid #eee; padding-bottom: 8px;'>{title}</h4>"
        html += "<div style='margin-top: 10px;'>"
        for e in entities:
            html += f"""
            <div style="margin-bottom: 12px; padding: 12px; background: #f8fafc; border-radius: 8px; border-left: 4px solid #3b82f6;">
                <div style="font-weight: 700; color: #1e40af;">{e.get('name', 'Unnamed')}</div>
                <div style="font-size: 13px; color: #475569; margin-top: 4px;"><strong>Role:</strong> {e.get('role', '-')}</div>
                <div style="font-size: 12px; color: #64748b; margin-top: 2px;">{e.get('relationship_basis') or e.get('adverse_links_summary') or ''}</div>
            </div>
            """
        html += "</div>"
        return html

    def _generate_list_html(self, title, items):
        if not items:
            return ""
        
        html = f"<h4 style='margin-top: 25px; border-bottom: 1px solid #eee; padding-bottom: 8px;'>{title}</h4>"
        html += "<ul style='margin-top: 10px; padding-left: 20px;'>"
        for item in items:
            html += f"<li style='margin-bottom: 8px; font-size: 13px; color: #374151; line-height: 1.5;'>{item}</li>"
        html += "</ul>"
        return html

    def _generate_findings_html(self, title, items):
        if not items:
            return f"<p style='font-size: 12px; color: #059669; margin-top: 20px;'>✅ No records found for {title.split(':')[-1].strip()}</p>"
        
        html = f"<h4 style='margin-top: 25px; border-bottom: 1px solid #eee; padding-bottom: 8px;'>{title}</h4>"
        for item in items:
            html += f"""
            <div class="finding-item" style="margin-top: 15px;">
                <div class="value">{item.get('matched_entity') or item.get('headline') or item.get('parties') or 'Finding'}</div>
                <div style="font-size: 12px; color: #4b5563; margin-top: 4px;">{item.get('reason') or item.get('snippet') or item.get('allegations') or ''}</div>
                {f'<div style="font-size: 11px; margin-top:6px;">Source: <a href="{item.get("source_url") or item.get("url")}" target="_blank" style="color: #2563eb;">View Reference</a></div>' if item.get('source_url') or item.get('url') else ''}
            </div>
            """
        return html

    def markdown_to_html(self, md: str) -> str:
        """
        Converts markdown with tables and special formatting to HTML snippet.
        """
        html = md
        
        # 1. Convert Tables
        def table_replacer(match):
            table_block = match.group(0).strip()
            lines = [l.strip() for l in table_block.split('\n') if '|' in l]
            if len(lines) < 2: return match.group(0)
            
            # Skip separator line (e.g. |---|)
            content_lines = [l for l in lines if not re.match(r'^\|[\s\-|:]+\|$', l)]
            
            res = '<table class="data-table"><thead><tr>'
            # Header
            headers = [c.strip() for c in content_lines[0].split('|') if c.strip()]
            for h in headers:
                res += f'<th>{h}</th>'
            res += '</tr></thead><tbody>'
            
            # Body
            for line in content_lines[1:]:
                raw_cells = [c.strip() for c in line.split('|')]
                if line.startswith('|') and line.endswith('|'):
                    cells = raw_cells[1:-1]
                else:
                    cells = [c for c in raw_cells if c]
                
                res += '<tr>'
                for i, c in enumerate(cells):
                    if i >= len(headers): break
                    c = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', c)
                    res += f'<td>{c}</td>'
                if len(cells) < len(headers):
                    for _ in range(len(headers) - len(cells)):
                        res += '<td></td>'
                res += '</tr>'
            res += '</tbody></table>'
            return res

        table_pattern = re.compile(r'(?:\n|^)(?:\|.+\|\s*\n)+', re.MULTILINE)
        html = table_pattern.sub(table_replacer, html)

        # 2. Headers
        html = re.sub(r'^# (.*)$', r'<h1>\1</h1>', html, flags=re.M)
        html = re.sub(r'^## (.*)$', r'<h2>\1</h2>', html, flags=re.M)
        html = re.sub(r'^### (.*)$', r'<h3>\1</h3>', html, flags=re.M)

        # 3. Bold/Italic
        html = re.sub(r'\*\*\*(.*?)\*\*\*', r'<strong><em>\1</em></strong>', html)
        html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html)
        html = re.sub(r'\*(.*?)\*', r'<em>\1</em>', html)

        # 4. Lists (with UL wrapping)
        html = re.sub(r'^[*-] (.*)$', r'<li>\1</li>', html, flags=re.M)
        html = re.sub(r'(<li>.*?</li>(?:\s*<li>.*?</li>)*)', r'<ul>\1</ul>', html, flags=re.S)

        # 5. Links
        html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" target="_blank" style="color: #2563eb;">\1</a>', html)

        # 6. Ratios/Currency styling
        html = re.sub(r'(\d+\.?\d*)\s*%', r'<span class="percentage">\1%</span>', html)
        html = re.sub(r'₹\s*(\d+(?:,\d+)*(?:\.\d+)?)', r'<span class="currency">₹\1</span>', html)

        # 7. Paragraphs and line breaks (Improved)
        # Avoid double wrapping if already processed by table/header logic
        html = html.replace('\n\n', '</p><p>')
        
        # Only add <br> if it's not following a block tag
        lines = html.split('\n')
        processed_lines = []
        for line in lines:
            line = line.strip()
            if not line: continue
            if re.match(r'<(h1|h2|h3|table|thead|tbody|tr|th|td|div|hr|li|p|ul|ol|a)', line):
                processed_lines.append(line)
            else:
                processed_lines.append(line + '<br>')
        
        html = ''.join(processed_lines)
        
        # Wrap the whole thing in a p if it doesn't start with a block tag
        if not re.match(r'^\s*<(h1|h2|h3|table|div|p|ul|ol)', html):
            html = f'<p>{html}</p>'

        return html

formatter = HTMLFormatter()
