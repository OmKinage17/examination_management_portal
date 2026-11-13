# Examination Management Portal V2 (EMP-V2)
**IIT Madras — Modern Application Development II (MAD-2) Project**

---

## 1. Project Overview

The **Examination Management Portal V2 (EMP-V2)** is a role-based web application designed to streamline academic examination scheduling, slot booking, rubric-based evaluation, and result publishing. It automates workflows for three core roles:
- **Administrator:** Manages courses, examinations, timelines, rubrics, examiners, and booking rescheduling.
- **Examiner:** Views assigned examinations, creates time slots, grades students using normalized rubrics, and completes slots.
- **Student:** Searches examinations, books available slots (with atomic capacity and overbooking protection), cancels bookings within permitted windows, views evaluated marks/feedback, and exports examination history to CSV asynchronously.

Background scheduling and asynchronous tasks are powered by **Celery** and **Redis** for daily examination reminders, monthly administrative reporting, and asynchronous CSV exports.

---

## 2. Technology Stack

In strict compliance with MAD-2 project guidelines:
- **Backend:** Python 3.12, Flask 3.1.3
- **Database:** SQLite with Flask-SQLAlchemy 3.1.1 (programmatic table creation and foreign key constraints)
- **Authentication & Sessions:** Flask-Session with CacheLib FileSystemCache (server-side session management with session fixation protection)
- **Frontend:** Vue.js 3 (CDN, composition API), Bootstrap 5.3 (no unauthorized CSS or complex frontend build steps)
- **Background Jobs & Scheduling:** Celery 5.7.0 with Celery Beat
- **Message Broker & Cache:** Redis 8.1.0 (with graceful offline fallback)
- **Testing:** Python standard `unittest` (74 automated tests passing)

---

## 3. Core Role Capabilities

### A. Administrator
- **Dashboard Overview:** Real-time metrics for courses, exams, examiners, students, slots, and bookings.
- **Course Management:** Full CRUD operations with foreign key integrity protection.
- **Examination Management:** CRUD with strict 4-point timeline validation (`slot_creation_start/end`, `slot_booking_start/end`).
- **Lifecycle Overrides:** Manually open or close slot creation and student booking windows.
- **Rubric Management:** Define evaluation criteria and maximum marks per exam.
- **Examiner Management:** Create examiner accounts and assign/unassign examiners to exams.
- **Booking Rescheduling:** Atomically transfer a student's booking to another slot for the same exam with automatic seat adjustments.
- **Slot Reassignment:** Reassign examiners on created slots.
- **Global Search:** Search students, examiners, exams, slots, and bookings.

### B. Examiner
- **Examiner Dashboard:** Summary of assigned exams, created slots, booked students, and pending evaluations.
- **Slot Management:** Create, edit, and delete slots within allowed creation dates; capacity validation.
- **Booked Student Tracking:** Real-time visibility into students reserved for their slots.
- **Rubric Grading:** Evaluate student submissions criterion-by-criterion with score validation against rubric maximums and optional remarks.
- **Slot Completion:** Mark slots as completed once all booked students are evaluated.

### C. Student
- **Self-Registration & Login:** Secure student account creation.
- **Examination Discovery:** Filter and search examinations by name, course code, and lifecycle status.
- **Slot Reservation:** Atomic slot booking with conditional updates to prevent overbooking and concurrency races.
- **Booking Rule Enforcement:** Maximum 1 active booking per exam; no re-booking after completed evaluation.
- **Cancellation:** Cancel bookings prior to deadline with automatic seat restoration.
- **Results & Rubric Breakdown:** View published scores and examiner feedback.
- **Profile Management:** Update personal profile details.
- **Asynchronous CSV Export:** Trigger background CSV export with real-time progress tracking and verified ownership downloads.

---

## 4. Setup and Installation (Windows PowerShell)

### Step 1: Clone or Open Project
```powershell
Set-Location E:\examination_management_portal
```

### Step 2: Create and Activate Virtual Environment
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Step 3: Install Required Dependencies
```powershell
pip install -r requirements.txt
```

### Step 4: Configure Environment Variables
Copy `.env.example` to `.env` (or use default development values):
```powershell
Copy-Item .env.example .env
```
Default Administrator credentials:
- **Email:** `admin@emp.local`
- **Password:** `Admin@123`

---

## 5. Running the Application on Windows

For full live operation, open 4 separate PowerShell terminals:

### Terminal 1: Redis Server
```powershell
# Option A: If Redis / Memurai native service is installed
redis-server

# Option B: Docker container
docker run --name emp-redis -p 6379:6379 -d redis:alpine
```

### Terminal 2: Celery Worker (Windows requires `--pool=solo`)
```powershell
Set-Location E:\examination_management_portal
.\.venv\Scripts\Activate.ps1
celery -A backend.tasks.celery_app.celery_app worker --pool=solo -l info
```

### Terminal 3: Celery Beat Scheduler
```powershell
Set-Location E:\examination_management_portal
.\.venv\Scripts\Activate.ps1
celery -A backend.tasks.celery_app.celery_app beat -l info
```

### Terminal 4: Flask Web Application
```powershell
Set-Location E:\examination_management_portal
.\.venv\Scripts\Activate.ps1
python backend\app.py
```
Open your browser at: **`http://127.0.0.1:5000`**

---

## 6. Running the Automated Test Suite

The test suite covers all database models, authentication, role workflows, booking concurrency, Celery tasks, caching, and export security:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v tests
```
**Current Status:** `Ran 74 tests — OK (100% passing)`

---

## 7. Recommended 5–7 Minute Video Demonstration Sequence

Follow this structured workflow for the viva recording (keep video duration between 5 and 10 minutes):

1. **Introduction (< 30 seconds):**
   - State your name, roll number, course (IITM MAD-2), and project title.
   - Mention technology stack: Flask, Vue 3, Bootstrap 5, SQLite, Celery, Redis.
2. **Admin Portal Demonstration (1.5 minutes):**
   - Log in as `admin@emp.local` / `Admin@123`.
   - View Dashboard metrics.
   - Add a Course (e.g., `CS901 - Cloud Security`).
   - Create an Examination with active date windows and add a Rubric criterion (e.g., `Architecture - 50 marks`, `Implementation - 50 marks`).
   - Register an Examiner account and assign them to the examination.
3. **Examiner Portal Demonstration (1.5 minutes):**
   - Log in with the newly created examiner account.
   - View assigned examination.
   - Create an Examination Slot (e.g., tomorrow 10:00 - 10:45, capacity: 2).
4. **Student Portal Demonstration (1.5 minutes):**
   - Register a new student account and log in.
   - Search for the examination and view available slots.
   - Book the slot (note available capacity decrements atomically).
   - Attempt duplicate booking (demonstrate safe rejection).
5. **Examiner Evaluation (1 minute):**
   - Switch back to Examiner login.
   - View booked student in the slot.
   - Open evaluation form, enter rubric marks and examiner remarks, submit.
   - Mark the slot as completed.
6. **Student Result & Asynchronous CSV Export (1 minute):**
   - Return to Student portal.
   - View published results with rubric breakdown and remarks.
   - In "My Bookings", click **Export History**.
   - Show status transition (`Export started` → `Export processing` → `Export completed`).
   - Click **Download CSV** and display the exported file with the 6 mandatory columns.
7. **Conclusion & Wrap-up (< 30 seconds):**
   - Brief closing summary of architecture, background tasks, and security validations.

