# Project Submission & Integrity Checklist
**Examination Management Portal V2 (EMP-V2) — IITM MAD-2**

Follow this exact checklist prior to submitting on the portal.

---

## 1. Required Source Files & Structure

Per submission instructions, the root directory must unpack cleanly:

```
project_name_2xFXXXXXX/
├── backend/
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── course.py
│   │   ├── examination.py
│   │   ├── slot.py
│   │   ├── booking.py
│   │   └── evaluation.py
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── admin.py
│   │   ├── examiner.py
│   │   └── student.py
│   ├── tasks/
│   │   ├── __init__.py
│   │   ├── celery_app.py
│   │   ├── jobs.py
│   │   └── email_service.py
│   ├── app.py
│   ├── config.py
│   ├── extensions.py
│   └── cache.py
├── frontend/
│   ├── src/
│   └── index.html
├── tests/
│   ├── test_phase2_db.py
│   ├── test_phase3_auth.py
│   ├── test_phase4_admin.py
│   ├── test_phase5_examiner.py
│   ├── test_phase6_student.py
│   └── test_phase7_tasks.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── PROJECT_REPORT.md
├── VIVA_PREPARATION.md
└── SUBMISSION_CHECKLIST.md
```

---

## 2. Mandatory Files to EXCLUDE from Submission ZIP

Before zipping, ensure the following runtime, cache, and sensitive files are deleted or excluded:
- [x] **`.venv/` or `env/`:** Virtual environment directory (must be installed by evaluator via `pip install -r requirements.txt`).
- [x] **`.env`:** Environment file containing local passwords or secret keys (`.env.example` is included instead).
- [x] **`backend/*.db` or `*.db`:** SQLite database files (the database is created programmatically by running `app.py`).
- [x] **`backend/flask_session/`:** Server-side session cache files.
- [x] **`backend/exports/` & `*.csv`:** Runtime generated export files.
- [x] **`__pycache__/` & `*.pyc`:** Python bytecode cache files.
- [x] **`.git/`:** Git repository directory.
- [x] **`.vscode/` or `.idea/`:** IDE configuration folders.

---

## 3. Project Report Requirements

- **File Format:** PDF or DOCX format.
- **Length Constraint:** Strictly **maximum 5 pages**.
- **Mandatory Content Included:**
  1. Student Name and Roll Number.
  2. Question statement and approach taken.
  3. AI / LLM Tool Declaration (tools used and extent of usage).
  4. Frameworks and libraries used (Flask, Vue 3, Bootstrap 5, SQLite, Celery, Redis).
  5. ER Diagram of the database showing entities and relationships.
  6. API resource endpoints table.
  7. Google Drive link to the video presentation.

---

## 4. Video Presentation Guidelines

- **Duration:** Strictly between **5 and 10 minutes** (aim for 6–7 minutes).
- **Camera Requirement:** Your face/video feed must remain visible throughout the recording (e.g., webcam PIP in Loom/OBS/Zoom).
- **Video Outline:**
  1. Short introduction (< 30 sec): Name, roll number, course, topic.
  2. Problem statement approach (30 sec): Architecture and flow overview.
  3. Key feature walkthrough (90 sec): Role-based dashboards.
  4. Core technical demo (3–4 min):
     - Admin creates course, exam, rubric, and examiner.
     - Examiner logs in, views assigned exam, creates a slot.
     - Student registers, searches, and books slot atomically.
     - Examiner grades student using rubrics.
     - Student views results and triggers asynchronous CSV export.
  5. Closing (30 sec): Summary of testing and background jobs.
- **Drive Permission:** Set to **"Anyone with the link can view"** (public view access). Test in an incognito window!

---

## 5. ZIP Packaging Commands (PowerShell)

To create the clean ZIP archive conforming to the required naming pattern:

```powershell
# 1. Set your IITM roll number (e.g., 21f1000000)
$RollNo = "2xFXXXXXX"
$FolderName = "exam_mgt_portal_$RollNo"

# 2. Create clean temporary staging directory
$StagingDir = Join-Path $env:TEMP $FolderName
Remove-Item -Recurse -Force $StagingDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $StagingDir | Out-Null

# 3. Copy clean project files (excluding runtime/cache files)
$ExcludeList = @('.venv', '.git', '.vscode', '.idea', '__pycache__', 'flask_session', 'exports', 'examination.db', '.env')

Get-ChildItem -Path . | Where-Object { $ExcludeList -notcontains $_.Name } | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination $StagingDir -Recurse -Force
}

# 4. Create the final submission ZIP
$ZipPath = "E:\examination_management_portal\$FolderName.zip"
Remove-Item -Force $ZipPath -ErrorAction SilentlyContinue
Compress-Archive -Path $StagingDir -DestinationPath $ZipPath

Write-Host "ZIP created successfully at: $ZipPath"
```

---

## 6. Pre-Submission Self-Audit

- [ ] All 74 automated unit tests pass: `.\.venv\Scripts\python.exe -m unittest discover tests`.
- [ ] No hardcoded passwords or `.env` files are inside the ZIP.
- [ ] Database tables generate programmatically on first launch.
- [ ] Project report contains your actual name, roll number, and video link.
- [ ] Presentation video has open view access (verified in private/incognito tab).
- [ ] ZIP tested by extracting to a new folder and launching with `python backend/app.py`.
- [ ] Submission passed the preliminary Google Form validator before deadline.

