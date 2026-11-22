from app.ingestion.metadata import content_hash, extract_metadata


def test_full_path():
    m = extract_metadata("CSE/Semester 5/Operating Systems/PYQ/2023-24", "CS-301 End Sem.pdf")
    assert m["branch"] == "CSE" and m["semester"] == 5
    assert m["subject"] == "Operating Systems" and m["academic_year"] == "2023-24"
    assert m["subject_code"] == "CS-301" and m["doc_type"] == "pyq"


def test_missing_stays_none_not_invented():
    m = extract_metadata("", "random.pdf")
    assert m == {"branch": None, "semester": None, "subject": None, "subject_code": None,
                 "academic_year": None, "study_year": None, "unit": None,
                 "chapter": None, "doc_type": None}


def test_semester_variants():
    assert extract_metadata("Sem 3", "x.pdf")["semester"] == 3
    assert extract_metadata("", "3rd sem notes.pdf")["semester"] == 3
    assert extract_metadata("", "Semester-8.pdf")["semester"] == 8
    assert extract_metadata("", "Semester 12.pdf")["semester"] is None  # out of range


def test_year_normalisation():
    assert extract_metadata("", "paper 2022-2023.pdf")["academic_year"] == "2022-23"
    assert extract_metadata("", "paper 2022_23.pdf")["academic_year"] == "2022-23"
    assert extract_metadata("", "DBMS 2024 PYQ.pdf")["academic_year"] == "2024"


def test_study_year_and_branch_are_separate_and_subject_skips_structure():
    metadata = extract_metadata(
        "2ND YEAR/3RD SEMESTER/COMPUTER SCIENCE & ENGINEERING/"
        "Subject-Wise Distribution/Operating Systems/PYQ/2023-24",
        "CS-301 End Sem.pdf",
    )
    assert metadata["study_year"] == 2
    assert metadata["branch"] == "CSE"
    assert metadata["semester"] == 3
    assert metadata["subject"] == "Operating Systems"
    assert metadata["academic_year"] == "2023-24"


def test_generic_folder_not_used_as_subject():
    assert extract_metadata("CSE/Sem 5/PYQ", "a.pdf")["subject"] is None


def test_doc_types():
    assert extract_metadata("Placements", "stats.pdf")["doc_type"] == "placement"
    assert extract_metadata("", "Attendance policy.pdf")["doc_type"] == "attendance"
    assert extract_metadata("Notices", "x.pdf")["doc_type"] == "notice"
    assert extract_metadata("", "collaboration notes.pdf")["doc_type"] == "notes"


def test_root_level_names_and_text_headings_infer_only_recognized_metadata():
    os_notes = extract_metadata("", "OS Unit 1.pdf")
    assert os_notes["subject"] == "Operating Systems"
    assert os_notes["unit"] == "1"

    dbms_pyq = extract_metadata("", "DBMS 2024 PYQ.pdf")
    assert dbms_pyq["subject"] == "Database Management Systems"
    assert dbms_pyq["doc_type"] == "pyq"
    assert dbms_pyq["academic_year"] == "2024"

    from_text = extract_metadata(
        "Random Download Folder",
        "material.pdf",
        "Subject: Computer Networks\nUNIT 2 - Routing basics\n",
    )
    assert from_text["subject"] == "Computer Networks"
    assert from_text["unit"] == "2"
    assert extract_metadata("", "material.pdf")["subject"] is None


def test_hash_stable():
    assert content_hash("a") == content_hash("a") != content_hash("b")
