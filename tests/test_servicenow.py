from datetime import datetime

from rca.servicenow import (
    clean_sr_description,
    clean_work_notes,
    parse_resolution,
    scrub,
    strip_prefix,
    to_datetime,
)


def test_excel_serial_dates():
    assert to_datetime("46230.289398148147").date().isoformat() == "2026-07-27"
    assert to_datetime(None) is None
    assert to_datetime(datetime(2026, 1, 2)) == datetime(2026, 1, 2)


def test_resolution_template_with_single_numbers():
    notes = (
        "For Resolution:-\n1. T3 Help – N\n2. Incident Resolution Hours Break up\n"
        " a. Solution Analysis Effort - 0.083\n3.Root Cause - Job failed Because of  abascus Mismatch\n"
        "4. Action Taken - Bypass abascus and restarted the job \n5. SLA miss - N"
    )
    root, action = parse_resolution(notes)
    assert root == "Job failed Because of abascus Mismatch"
    assert action == "Bypass abascus and restarted the job"


def test_resolution_template_with_dotted_numbers():
    notes = (
        "3.1. SME Help – <Y/N> \n3.3. Root Cause - Job failed with Soc7, non numeric data in numeric filed..\n"
        "3.4. Action Taken -removed bad data and restarted job, it complete sccessfully..\n"
        "3.5. SLA [Missed -Reason / Outlier: Reason] : N/A"
    )
    root, action = parse_resolution(notes)
    assert root == "Job failed with Soc7, non numeric data in numeric filed"
    assert action == "removed bad data and restarted job, it complete sccessfully"


def test_free_text_resolution_is_the_action():
    root, action = parse_resolution("File recovered and users are able to access the LMS now.")
    assert root is None
    assert action == "File recovered and users are able to access the LMS now"


def test_strip_prefix_keeps_ajs_alert_intact():
    alert = "Incident AJS002E ABEND=S000 U4005  F8RH0920(JOB64200)  JCL=A.F8RH0910  Z1SCH208001 - 27 Jul 2026 06:56:19"
    assert strip_prefix(alert, "AJS - AUTOMATED JOB SCHEDULER") == alert
    assert strip_prefix("Material Systems (Corinth) - LMS error codes", "Material Systems (Corinth)") == "LMS error codes"
    assert strip_prefix(" - can not load lms", "Material Systems (Corinth)") == "can not load lms"


def test_scrub_keeps_names_but_removes_contact_details():
    text = (
        "Paul manually deleted the tickets. Ask Ed. Business Phone = +16627202508 "
        "Email:someone@cat.com LAN/CWS: hodumcl control number F6062238403"
    )
    out = scrub(text)
    assert "Paul manually deleted" in out and "Ask Ed" in out
    assert "16627202508" not in out and "someone@cat.com" not in out and "hodumcl" not in out
    assert "F6062238403" in out


def test_sr_form_boilerplate_is_dropped_but_free_text_kept():
    text = (
        'Request for catalog item "I Need Something (Service Request)"\n\n'
        "Requested For = Steven McClellan\n"
        "What is the nature of this request = Other\n"
        "Description – Share details to help us fulfill your request (what you need, timing, related requests, etc.) = "
        'LMS error saying "Invalid file structure"\n'
        "Assignment Group = Reman - MS COBOL Application Support\n"
        "Location Management system shows the wrong count"
    )
    out = clean_sr_description(text)
    assert out.splitlines() == ['LMS error saying "Invalid file structure"', "Location Management system shows the wrong count"]


def test_work_notes_drop_acks_and_email_headers():
    notes = "2026-07-07 12:59:40 - FNU Preeti (Work notes)\nACK\nFrom: Someone <a@b.com>\nFile recovered"
    assert clean_work_notes(notes) == "2026-07-07 12:59:40 - FNU Preeti (Work notes)\nFile recovered"
