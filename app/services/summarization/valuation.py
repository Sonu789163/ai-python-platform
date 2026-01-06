"""
Valuation service for calculating investment rounds and dilutions.
Ported from n8n nodes Code17 and Code18.
"""
from typing import List, Dict, Any

class ValuationService:
    @staticmethod
    def calculate_premium_rounds(premium_rounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Calculates valuation metrics for each identified premium round.
        """
        results = []
        for round_data in premium_rounds:
            shares = float(round_data.get("shares_allotted", 0))
            price = float(round_data.get("issue_price", 0))
            total_shares = float(round_data.get("cumulative_equity_shares", 0))
            
            if shares > 0 and price > 0 and total_shares > 0:
                round_raised = shares * price
                dilution = shares / total_shares
                post_money = round_raised / dilution if dilution > 0 else 0
                
                results.append({
                    **round_data,
                    "round_raised": round_raised,
                    "dilution": dilution,
                    "dilution_percent": dilution * 100,
                    "post_money_valuation": post_money
                })
        return results

    @staticmethod
    def generate_valuation_html(calculated_rounds: List[Dict[str, Any]]) -> str:
        """
        Generates premium HTML tables for the valuation analysis.
        """
        if not calculated_rounds:
            return "<div style='padding:15px; background:#fef9c3; border-radius:8px;'><h4 style='margin:0; color:#854d0e;'>ℹ️ No premium rounds found. Valuation of the company has not increased.</h4></div>"

        html = ""
        for i, r in enumerate(calculated_rounds):
            html += f"""
            <div style="margin-bottom: 25px; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden;">
                <div style="background: #f8fafc; padding: 10px 15px; border-bottom: 1px solid #e5e7eb;">
                    <h4 style="margin: 0; color: #4B2A06;">💎 Premium Round {i + 1}</h4>
                </div>
                <table style="width: 100%; border-collapse: collapse; margin: 0; font-size: 13px;">
                    <tbody>
                        <tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 10px 15px; color: #6b7280; width: 40%;">Date of Allotment</td><td style="padding: 10px 15px; font-weight: 600;">{r.get('date_of_allotment', 'N/A')}</td></tr>
                        <tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 10px 15px; color: #6b7280;">Nature of Allotment</td><td style="padding: 10px 15px;">{r.get('nature_of_allotment', 'N/A')}</td></tr>
                        <tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 10px 15px; color: #6b7280;">Shares Allotted</td><td style="padding: 10px 15px; font-weight: 600;">{int(r.get('shares_allotted', 0)):,}</td></tr>
                        <tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 10px 15px; color: #6b7280;">Issue Price</td><td style="padding: 10px 15px; color: #b45309; font-weight: 700;">₹{float(r.get('issue_price', 0)):,.2f}</td></tr>
                        <tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 10px 15px; color: #6b7280;">Round Raised</td><td style="padding: 10px 15px; font-weight: 600;">₹{float(r.get('round_raised', 0)):,.2f}</td></tr>
                        <tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 10px 15px; color: #6b7280;">Dilution (%)</td><td style="padding: 10px 15px; color: #059669; font-weight: 700;">{float(r.get('dilution_percent', 0)):.4f}%</td></tr>
                        <tr><td style="padding: 10px 15px; color: #6b7280; background: #fffbeb;">Post-Money Valuation</td><td style="padding: 10px 15px; font-weight: 700; color: #1e293b; background: #fffbeb;">₹{float(r.get('post_money_valuation', 0)):,.2f}</td></tr>
                    </tbody>
                </table>
            </div>
            """
        return html

valuation_service = ValuationService()
