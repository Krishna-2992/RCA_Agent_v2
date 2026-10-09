from rca.entities import canonical_file, extract, parse_ajs_alert


def test_ajs_abend_alert():
    alert = parse_ajs_alert("Incident AJS002E ABEND=S000 U4005  F8RH0430(JOB42942)  JCL=A.F8RH0410  Z1SCH190001 - 9 Jul 2026")
    assert alert.abend == "U4005"
    assert alert.step == "F8RH0430"
    assert alert.jcl == "A.F8RH0410"
    assert alert.reason is None


def test_ajs_system_abend_and_job_failed():
    s0c7 = parse_ajs_alert("Incident AJS002E ABEND=S0C7 U0000  F8RH0415(JOB07536)  JCL=A.F8RH0410  Z1SCH205001")
    assert s0c7.abend == "S0C7"
    failed = parse_ajs_alert("Incident AJS002I JOB FAILED RSN=2  F8RH3910(JOB26881)  JCL=A.F8RH3910  Z1SCH180001")
    assert failed.abend is None and failed.reason == "RSN=2"


def test_file_typos_map_to_the_right_file():
    assert canonical_file("IN001.MST") == "IN0011.MST"     # not IN0001.MST
    assert canonical_file("IN0011MST") == "IN0011.MST"
    assert canonical_file("in0011.mst") == "IN0011.MST"
    assert canonical_file("IN0018.MOL") == "IN0018.MOV"
    assert canonical_file("IN0015.MOR") == "IN0015.MOV"
    assert canonical_file("IN0018") == "IN0018.MOV"        # stem with one known file


def test_extract_lms_report():
    found = extract("File lock error 984 IN0011MST Section 5700 writes")
    assert found.files == ["IN0011.MST"]
    assert found.error_codes == ["984"]
    assert found.error_families == ["file_corrupt_or_locked"]


def test_error_code_glued_to_text_and_negative():
    assert extract("We have a file lock error code 9302file name IN0014.CTL").error_codes == ["9302"]
    assert extract("Open error location file -9802").error_codes == ["9802"]
    found = extract("Open error location file -9802")
    assert "IN0011.MST" in found.files                     # "location file" phrase


def test_transaction_codes_and_control_numbers_are_not_errors():
    found = extract("LMS will not allow the operator to issue out ticket. Control Number - LH031278316 Trans Code - 325")
    assert found.error_codes == []


def test_soc7_typo_and_dataset():
    found = extract("Job failed with Soc7. Abacus mismatch for VSAMPROD.F8RH.F810D006 .")
    assert "S0C7" in found.abends
    assert found.datasets == ["VSAMPROD.F8RH.F810D006"]


def test_applications_and_jobs():
    found = extract("F8 COBOL - STKORD job did not complete; LMS users affected")
    assert "STKORD" in found.jobs
    assert "LMS" in found.applications


def test_legacy_program_names_map_to_f8rh_ids():
    assert extract("see 03RMNLMS documentation").programs == ["F8RH0093"]


def test_report_names_resolve_to_the_documentation_id():
    assert extract("COBOL reports MISCRPT068 & MISCRPT069 have garbage data").reports == ["RPT068", "RPT069"]
    assert extract("Output: MISCRPT074.CSV").reports == ["RPT074"]
