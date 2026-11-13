# Viva Voce Preparation Guide
**Examination Management Portal V2 (EMP-V2) — IITM MAD-2**

This guide provides concise, confident answers to 25 critical viva questions, a breakdown of core project files, and explanations of the underlying technologies.

---

## 1. Project Architecture & Codebase Overview

| File / Folder | Role & Purpose |
|---|---|
| `backend/app.py` | Flask application factory (`create_app`), Blueprint registrations, CORS setup, and programmatic database seeding (`init_database`). |
| `backend/config.py` | Central configuration class loading `.env` variables (database URI, secrets, Redis URLs, SMTP settings, export directory). |
| `backend/extensions.py` | Shared extension instances (`db = SQLAlchemy()`, `server_session = Session()`) avoiding circular imports. |
| `backend/models/` | Declarative SQLAlchemy models: `user.py`, `course.py`, `examination.py`, `slot.py`, `booking.py`, `evaluation.py`. |
| `backend/routes/` | Role-based REST API Blueprints: `auth.py`, `admin.py`, `examiner.py`, `student.py`. |
| `backend/cache.py` | Redis caching service (`cache_get`, `cache_set`, `invalidate_exam_cache`) with graceful fallback. |
| `backend/tasks/celery_app.py` | Celery application factory, Celery Beat crontab schedule, and `FlaskTask` context wrapper. |
| `backend/tasks/jobs.py` | Background tasks: `send_daily_reminders`, `send_monthly_admin_report`, `export_student_csv`. |
| `backend/tasks/email_service.py` | Real SMTP email sender using Python's built-in `smtplib`. |
| `frontend/index.html` | Vue.js 3 single-page application using Bootstrap 5 for UI styling and reactive state management. |
| `tests/` | 74 automated unit and integration tests covering all requirements. |

---

## 2. Core Technologies Explained

- **Flask:** Lightweight Python WSGI web application framework used to expose JSON RESTful API endpoints.
- **Vue.js 3:** Progressive JavaScript framework imported via CDN (`createApp`, `ref`, `computed`, `onMounted`) driving reactive client-side rendering without a complex Node/Webpack build pipeline.
- **Bootstrap 5:** CSS framework used for responsive grid layout, tables, alerts, and navigation without unauthorized CSS preprocessors.
- **SQLite:** Serverless, zero-configuration relational database engine embedded directly in Python via SQLAlchemy.
- **Flask-Session:** Server-side session management storing encrypted session files on the server (FileSystemCache) rather than in client-side cookies, preventing client tampering.
- **Redis:** In-memory data store functioning simultaneously as Celery's message broker, result backend, and API response cache.
- **Celery & Celery Beat:** Distributed task queue handling asynchronous processing (CSV export) and periodic cron-style jobs (daily reminders, monthly reports).

---

## 3. Top 25 Viva Questions and Answers

### Q1: What is the main purpose of this project?
**Answer:** It is a centralized academic examination portal managing three roles (Admin, Examiner, Student) to automate examination scheduling, slot booking, rubric grading, batch notifications, and result publishing.

### Q2: How is authentication implemented, and why Flask-Session?
**Answer:** Authentication uses server-side sessions via `Flask-Session`. On login, `session["user_id"]` and `session["role"]` are stored on the server. The client only receives an encrypted cookie ID. This is more secure than client-side JWTs because sessions can be instantly invalidated on logout and protected against session fixation.

### Q3: How do you protect passwords in the database?
**Answer:** Passwords are never stored in plain text. We use Werkzeug’s `generate_password_hash()` which applies modern, salted cryptographic hashing (`scrypt` or `pbkdf2:sha256`) and verify with `check_password_hash()`.

### Q4: How is Role-Based Access Control (RBAC) enforced?
**Answer:** Through a custom Python decorator `@role_required(*allowed_roles)`. It inspects `session.get("role")`, returns `HTTP 401` if unauthenticated, and `HTTP 403 Forbidden` if the user's role is not permitted.

### Q5: How do you prevent overbooking and concurrency race conditions?
**Answer:** We execute an atomic SQL update: `UPDATE examination_slots SET available_seats = available_seats - 1 WHERE id = :slot_id AND available_seats > 0`. If zero rows are affected, the booking is rejected immediately. In addition, an SQLite `CHECK(available_seats >= 0)` constraint prevents negative seats at the database level.

### Q6: How do you restrict a student to only one booking per examination?
**Answer:** The `Booking` model enforces a partial unique index: `db.Index('uq_student_active_booking', 'student_id', 'exam_id', unique=True, sqlite_where=(status == 'Booked'))`. A student can only have one active reservation at a time.

### Q7: Can a student re-book after cancelling?
**Answer:** Yes, provided the examination's booking date window is still open and status is `'Booking Open'`. Cancellation sets the booking status to `'Cancelled'`, freeing the unique constraint. However, once an evaluation is submitted or status is `'Completed'`, re-booking is permanently blocked.

### Q8: What happens when a student cancels a booking?
**Answer:** The booking status changes to `'Cancelled'` and the slot's `available_seats` is atomically incremented by 1 (up to `max_capacity`). If the slot was `'Full'`, its status reverts to `'Available'`.

### Q9: How does the Admin reschedule a student's booking?
**Answer:** In a single atomic database transaction, the Admin frees one seat in the student's current slot, decrements one seat in the target slot, and updates `booking.slot_id`. Both slots must belong to the same examination.

### Q10: How are examination timelines enforced?
**Answer:** Examinations have two distinct windows: `slot_creation_start_date` to `slot_creation_end_date` (for examiners) and `slot_booking_start_date` to `slot_booking_end_date` (for students). Requests outside these timestamps or when statuses are overridden are rejected with `HTTP 400`.

### Q11: How does rubric evaluation work?
**Answer:** Each examination has multiple `Rubric` criteria, each with `criterion_name` and `max_marks`. Examiners enter marks for each criterion. The backend validates that marks obtained do not exceed criterion maximums, stores individual normalized scores in `EvaluationScore`, and records total score and feedback in `Evaluation`.

### Q12: Why did you separate `Evaluation` and `EvaluationScore`?
**Answer:** For database normalization (3NF). An evaluation has one overall remark and total score, but multiple criteria scores. Normalization ensures criteria can vary per exam without schema alterations.

### Q13: How does Celery communicate with Flask?
**Answer:** Celery uses a custom `FlaskTask(Task)` base class whose `__call__` method wraps task execution in `with app.app_context():`. This allows Celery tasks to access SQLAlchemy models, query databases, and read application configurations directly.

### Q14: How are daily examination reminders scheduled and executed?
**Answer:** Celery Beat schedules `send_daily_reminders` to run every morning at 08:00 AM IST. It queries slots scheduled for tomorrow where `Booking.status == 'Booked'`, formats exam, course, date/time, and examiner details, and dispatches real emails via SMTP.

### Q15: How do you avoid duplicate reminder emails?
**Answer:** Before sending, the task checks a Redis deduplication key `reminder_sent:<booking_id>:<exam_date>`. If found, the task skips sending. Once sent, the key is set with a 7-day TTL.

### Q16: What is included in the Monthly Admin Report?
**Answer:** The `send_monthly_admin_report` task runs on the 1st of every month via Celery Beat, compiling: total exams, total bookings, completed exams, completed evaluations, and a breakdown table of examiner activity (slots created and evaluations completed). It dispatches an HTML and plain-text report to `ADMIN_EMAIL`.

### Q17: How does asynchronous CSV export work?
**Answer:** When a student clicks "Export History", a `POST /api/student/export` request triggers `export_student_csv.delay(student_id)`. Celery queries only that student's records, writes the 6 mandatory columns to `backend/exports/`, and returns the task ID. The frontend polls the status endpoint and displays a download link upon completion.

### Q18: What are the 6 mandatory columns in the student CSV export?
**Answer:**
1. `Examination Name`
2. `Course`
3. `Examination Date`
4. `Examiner`
5. `Marks Obtained`
6. `Booking Status`

### Q19: How do you secure the CSV export download endpoint?
**Answer:** `GET /api/student/export/download/<task_id>` inspects the completed task result and verifies that `res.result["student_id"] == session["user_id"]`. If a student attempts to download another student's export, `HTTP 403 Forbidden` is returned.

### Q20: What is cached in Redis and what is the TTL?
**Answer:** Public examination listings (`GET /api/student/examinations`) are cached in Redis under keys like `public_exams:<search>:<status>` with an explicit TTL of 60 seconds (`CACHE_DEFAULT_TIMEOUT`).

### Q21: How do you ensure private student data is not leaked in the Redis cache?
**Answer:** Only public examination metadata (course code, name, dates, duration, open seats) is stored in the shared Redis cache key. Student-specific attributes (`has_active_booking`, `student_booking_status`) are dynamically queried per student and appended to the response, keeping private data out of the shared cache.

### Q22: When is the cache invalidated?
**Answer:** Whenever an Administrator creates, edits, deletes, or manually changes the status of a course or examination, `invalidate_exam_cache()` is called, purging all matching `public_exams:*` keys from Redis immediately.

### Q23: Why is Celery worker run with `--pool=solo` on Windows?
**Answer:** Windows does not support POSIX `fork()`. Standard Celery multiprocessing (prefork) causes socket and process deadlocks on Windows. The `--pool=solo` flag tells Celery to execute tasks sequentially in a single process without forking, which is fully stable on Windows.

### Q24: What is CSRF and how is it prevented?
**Answer:** Cross-Site Request Forgery (CSRF) tricks a logged-in browser into executing unwanted actions. We generate a cryptographic CSRF token stored in the server session and require it in the `X-CSRF-Token` HTTP header for all state-changing requests (`POST`, `PUT`, `DELETE`).

### Q25: How did you verify the application?
**Answer:** We wrote 74 comprehensive unit and integration tests using Python's `unittest` module covering database constraints, session authentication, Admin workflows, Examiner grading, Student booking concurrency, Celery background jobs, SMTP delivery, Redis TTL, and export authorization. All 74 tests pass.

