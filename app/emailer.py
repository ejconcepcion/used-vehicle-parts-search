"""Send new-vehicle notification emails via SMTP."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import config

log = logging.getLogger(__name__)


def _make_pill(make: str) -> str:
    colors = {
        "BMW":           ("background:#dbeafe", "color:#1e40af"),
        "VOLKSWAGEN":    ("background:#dcfce7", "color:#166534"),
        "MERCEDES-BENZ": ("background:#fef3c7", "color:#92400e"),
        "AUDI":          ("background:#fee2e2", "color:#991b1b"),
    }
    bg, fg = colors.get((make or "").upper(), ("background:#f3f4f6", "color:#374151"))
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:9999px;'
        f'font-size:12px;font-weight:600;{bg};{fg}">{make}</span>'
    )


def _build_html(new_vehicles: list[dict]) -> str:
    rows = ""
    for v in new_vehicles:
        make   = v.get("make") or ""
        year   = v.get("year") or ""
        model  = v.get("model") or ""
        yard   = v.get("yard_name") or ""
        row_no = v.get("row_number") or ""
        added  = v.get("date_added_to_yard") or ""
        url    = v.get("detail_url") or ""
        value  = v.get("estimated_total_value") or 0.0

        value_str = f"~${value:,.0f}" if value > 0 else "—"
        row52_link = (
            f'<a href="{url}" style="color:#2563eb;text-decoration:none" target="_blank">View ↗</a>'
            if url else "—"
        )
        rows += f"""
        <tr style="border-bottom:1px solid #e2e8f0">
          <td style="padding:10px 12px;white-space:nowrap">{_make_pill(make)}</td>
          <td style="padding:10px 12px;font-weight:600">{year} {make} {model}</td>
          <td style="padding:10px 12px;color:#64748b">{yard}</td>
          <td style="padding:10px 12px;color:#64748b">{row_no}</td>
          <td style="padding:10px 12px;color:#64748b">{added}</td>
          <td style="padding:10px 12px;color:#0f766e;font-weight:600">{value_str}</td>
          <td style="padding:10px 12px">{row52_link}</td>
        </tr>"""

    count = len(new_vehicles)
    noun  = "vehicle" if count == 1 else "vehicles"

    return f"""<!DOCTYPE html>
<html>
<body style="font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
             background:#f8fafc;margin:0;padding:24px">
  <div style="max-width:760px;margin:0 auto;background:#fff;border-radius:12px;
              border:1px solid #e2e8f0;overflow:hidden">
    <div style="background:#1e40af;padding:20px 24px">
      <h1 style="margin:0;color:#fff;font-size:20px">
        🚗 {count} new {noun} at the yard
      </h1>
    </div>
    <div style="padding:0 0 8px">
      <table style="width:100%;border-collapse:collapse;font-size:14px">
        <thead>
          <tr style="background:#f1f5f9;text-align:left">
            <th style="padding:8px 12px;color:#64748b;font-weight:600"></th>
            <th style="padding:8px 12px;color:#64748b;font-weight:600">Vehicle</th>
            <th style="padding:8px 12px;color:#64748b;font-weight:600">Yard</th>
            <th style="padding:8px 12px;color:#64748b;font-weight:600">Row</th>
            <th style="padding:8px 12px;color:#64748b;font-weight:600">Added</th>
            <th style="padding:8px 12px;color:#64748b;font-weight:600">Est. value</th>
            <th style="padding:8px 12px;color:#64748b;font-weight:600">Row52</th>
          </tr>
        </thead>
        <tbody>{rows}
        </tbody>
      </table>
    </div>
    <div style="padding:16px 24px;border-top:1px solid #e2e8f0;color:#94a3b8;font-size:12px">
      RadioB Used Parts Search — nightly scrape notification
    </div>
  </div>
</body>
</html>"""


def send_new_vehicles_email(new_vehicles: list[dict]) -> str | None:
    """Send a new-vehicle notification. Returns None on success, error string on failure.
    Silently skips (returns None) if SMTP is not configured."""
    missing = [k for k, v in {
        "SMTP_HOST": config.SMTP_HOST,
        "SMTP_USER": config.SMTP_USER,
        "SMTP_PASSWORD": config.SMTP_PASSWORD,
        "EMAIL_TO": config.EMAIL_TO,
    }.items() if not v]
    if missing:
        log.debug("Email not configured — missing: %s", ", ".join(missing))
        return f"not configured — missing env vars: {', '.join(missing)}"

    count = len(new_vehicles)
    noun  = "vehicle" if count == 1 else "vehicles"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🚗 {count} new {noun} found at the yard"
    msg["From"]    = config.SMTP_USER
    msg["To"]      = config.EMAIL_TO

    plain = "\n".join(
        f"- {v.get('year')} {v.get('make')} {v.get('model')}  |  {v.get('yard_name')}  |  "
        f"Row {v.get('row_number')}  |  Added {v.get('date_added_to_yard')}"
        for v in new_vehicles
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(_build_html(new_vehicles), "html"))

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.sendmail(config.SMTP_USER, config.EMAIL_TO, msg.as_string())
        log.info("New-vehicle email sent to %s (%d vehicles)", config.EMAIL_TO, count)
        return None
    except Exception as exc:
        log.error("Failed to send new-vehicle email: %s", exc)
        return str(exc)
