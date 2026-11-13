"""
Phase 7 Celery Background Tasks and Redis Caching Tests.
Covers:
1. Celery initialization
2. Redis configuration
3. Daily reminder eligibility
4. Duplicate reminder prevention
5. Monthly report generation
6. SMTP email functionality using mocks
7. Student CSV export
8. CSV content correctness
9. Student export authorization
10. Export status and download
11. Redis cache TTL
12. Cache invalidation
13. Task failure handling
"""
import csv
import json
import os
import time
from datetime import date, datetime, time as dtime, timedelta
import unittest
from unittest.mock import MagicMock, patch

# Ensure in-memory backend for isolated Celery execution during testing
os.environ["CELERY_BROKER_URL"] = "memory://"
os.environ["CELERY_RESULT_BACKEND"] = "cache+memory://"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"

from backend.app import create_app, init_database
from backend.cache import (
    cache_get,
    cache_set,
    cache_delete,
    cache_delete_pattern,
    invalidate_exam_cache,
)
from backend.config import Config
from backend.extensions import db
from backend.models import (
    Booking,
    Course,
    Evaluation,
    EvaluationScore,
    Examination,
    ExaminationSlot,
    Rubric,
    User,
)
from backend.tasks.celery_app import celery_app
from backend.tasks.email_service import send_email
from backend.tasks.jobs import (
    _fallback_sent_reminders,
    export_student_csv,
    send_daily_reminders,
    send_monthly_admin_report,
)


class Phase7TasksTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        cls.app.config["ADMIN_EMAIL"] = "admin@emp.local"
        cls.app.config["ADMIN_PASSWORD"] = "Admin@123"

        # Configure Celery for isolated, fast, and deterministic in-memory test execution
        cls.app.config["CELERY_TASK_ALWAYS_EAGER"] = True
        cls.app.config["CELERY_TASK_STORE_EAGER_RESULT"] = True
        cls.app.config["CELERY_TASK_EAGER_PROPAGATES"] = True
        celery_app.conf.task_always_eager = True
        celery_app.conf.task_store_eager_result = True
        celery_app.conf.result_backend = "cache+memory://"

        cls.ctx = cls.app.app_context()
        cls.ctx.push()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.pop()

    def setUp(self):
        db.create_all()
        init_database(self.app)
        _fallback_sent_reminders.clear()
        invalidate_exam_cache()

        # Seed standard entities
        self.course = Course(course_code="CS701", course_name="Advanced Cloud Computing")
        self.student = self.make_user("student1@emp.local", "Student Alice", "student")
        self.other_student = self.make_user("student2@emp.local", "Student Bob", "student")
        self.examiner = self.make_user("examiner1@emp.local", "Dr. Charlie", "examiner")
        db.session.add_all([self.course, self.student, self.other_student, self.examiner])
        db.session.commit()

        # Create an examination
        now = datetime.now()
        self.exam = Examination(
            course_id=self.course.id,
            exam_name="Cloud Midterm",
            description="Viva on Cloud Architecture",
            duration_minutes=30,
            max_marks=100.0,
            slot_creation_start_date=now - timedelta(days=2),
            slot_creation_end_date=now + timedelta(days=5),
            slot_booking_start_date=now - timedelta(days=1),
            slot_booking_end_date=now + timedelta(days=5),
            status="Booking Open",
        )
        db.session.add(self.exam)
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()

    def make_user(self, email, full_name, role):
        u = User(email=email, full_name=full_name, role=role)
        u.set_password("Pass@123")
        return u

    def login_user(self, user):
        """Helper to log in and return session client and CSRF token."""
        res = self.client.post(
            "/api/auth/login",
            json={"email": user.email, "password": "Pass@123"},
        )
        data = res.get_json() or {}
        return data.get("csrf_token")

    # ---------------------------------------------------------------------
    # Test 1: Celery Initialization
    # ---------------------------------------------------------------------
    def test_01_celery_initialization(self):
        """Verifies Celery application is configured with Beat schedule and tasks."""
        self.assertIsNotNone(celery_app)
        self.assertEqual(celery_app.conf.timezone, "Asia/Kolkata")
        self.assertIn("daily-examination-reminder", celery_app.conf.beat_schedule)
        self.assertIn("monthly-admin-report", celery_app.conf.beat_schedule)

        registered_tasks = celery_app.tasks
        self.assertIn("backend.tasks.jobs.send_daily_reminders", registered_tasks)
        self.assertIn("backend.tasks.jobs.send_monthly_admin_report", registered_tasks)
        self.assertIn("backend.tasks.jobs.export_student_csv", registered_tasks)

    # ---------------------------------------------------------------------
    # Test 2: Redis Configuration
    # ---------------------------------------------------------------------
    def test_02_redis_configuration(self):
        """Verifies Redis URLs and cache timeout settings are populated in Config."""
        self.assertTrue(hasattr(Config, "REDIS_URL"))
        self.assertTrue(hasattr(Config, "CELERY_BROKER_URL"))
        self.assertTrue(hasattr(Config, "CELERY_RESULT_BACKEND"))
        self.assertTrue(hasattr(Config, "CACHE_REDIS_URL"))
        self.assertGreaterEqual(Config.CACHE_DEFAULT_TIMEOUT, 60)
        self.assertTrue(os.path.isdir(Config.EXPORTS_DIR))

    # ---------------------------------------------------------------------
    # Test 3: Daily Reminder Eligibility
    # ---------------------------------------------------------------------
    @patch("backend.tasks.jobs.send_email")
    def test_03_daily_reminder_eligibility(self, mock_send_email):
        """Verifies only active Booked slots occurring tomorrow receive reminders."""
        tomorrow = date.today() + timedelta(days=1)
        next_week = date.today() + timedelta(days=7)

        # Slot 1: Tomorrow (Eligible)
        slot_tomorrow = ExaminationSlot(
            exam_id=self.exam.id,
            examiner_id=self.examiner.id,
            exam_date=tomorrow,
            start_time=dtime(10, 0),
            end_time=dtime(10, 30),
            max_capacity=5,
            available_seats=4,
            status="Available",
        )
        # Slot 2: Next week (Ineligible date)
        slot_future = ExaminationSlot(
            exam_id=self.exam.id,
            examiner_id=self.examiner.id,
            exam_date=next_week,
            start_time=dtime(11, 0),
            end_time=dtime(11, 30),
            max_capacity=5,
            available_seats=4,
            status="Available",
        )
        student3 = self.make_user("student3@emp.local", "Student Charlie", "student")
        db.session.add_all([slot_tomorrow, slot_future, student3])
        db.session.commit()

        # Booking 1: Tomorrow, Booked (Eligible)
        b1 = Booking(student_id=self.student.id, slot_id=slot_tomorrow.id, exam_id=self.exam.id, status="Booked")
        # Booking 2: Tomorrow, Cancelled (Excluded)
        b2 = Booking(student_id=self.other_student.id, slot_id=slot_tomorrow.id, exam_id=self.exam.id, status="Cancelled")
        # Booking 3: Future, Booked (Excluded due to date)
        b3 = Booking(student_id=student3.id, slot_id=slot_future.id, exam_id=self.exam.id, status="Booked")
        db.session.add_all([b1, b2, b3])
        db.session.commit()

        result = send_daily_reminders(target_date_str=tomorrow.strftime("%Y-%m-%d"))

        self.assertEqual(result["reminders_sent"], 1)
        mock_send_email.assert_called_once()
        args, kwargs = mock_send_email.call_args
        self.assertEqual(kwargs["to_email"], self.student.email)
        self.assertIn("Cloud Midterm", kwargs["subject"])

    # ---------------------------------------------------------------------
    # Test 4: Duplicate Reminder Prevention
    # ---------------------------------------------------------------------
    @patch("backend.tasks.jobs.send_email")
    def test_04_duplicate_reminder_prevention(self, mock_send_email):
        """Verifies duplicate reminders are skipped for the same scheduled booking."""
        tomorrow = date.today() + timedelta(days=1)
        slot = ExaminationSlot(
            exam_id=self.exam.id,
            examiner_id=self.examiner.id,
            exam_date=tomorrow,
            start_time=dtime(14, 0),
            end_time=dtime(14, 30),
            max_capacity=5,
            available_seats=4,
            status="Available",
        )
        db.session.add(slot)
        db.session.commit()

        booking = Booking(student_id=self.student.id, slot_id=slot.id, exam_id=self.exam.id, status="Booked")
        db.session.add(booking)
        db.session.commit()

        # First run: should send reminder
        res1 = send_daily_reminders(target_date_str=tomorrow.strftime("%Y-%m-%d"))
        self.assertEqual(res1["reminders_sent"], 1)
        self.assertEqual(mock_send_email.call_count, 1)

        # Second run: should be skipped as duplicate
        res2 = send_daily_reminders(target_date_str=tomorrow.strftime("%Y-%m-%d"))
        self.assertEqual(res2["reminders_sent"], 0)
        self.assertEqual(res2["reminders_skipped"], 1)
        # Email call count remains 1
        self.assertEqual(mock_send_email.call_count, 1)

    # ---------------------------------------------------------------------
    # Test 5: Monthly Report Generation
    # ---------------------------------------------------------------------
    @patch("backend.tasks.jobs.send_email")
    def test_05_monthly_report_generation(self, mock_send_email):
        """Verifies monthly report compiles correct stats and dispatches to Admin."""
        # Create a completed evaluation
        slot = ExaminationSlot(
            exam_id=self.exam.id,
            examiner_id=self.examiner.id,
            exam_date=date.today(),
            start_time=dtime(9, 0),
            end_time=dtime(9, 30),
            max_capacity=5,
            available_seats=4,
            status="Completed",
        )
        db.session.add(slot)
        db.session.commit()

        booking = Booking(student_id=self.student.id, slot_id=slot.id, exam_id=self.exam.id, status="Completed")
        db.session.add(booking)
        db.session.commit()

        eval_rec = Evaluation(
            booking_id=booking.id,
            student_id=self.student.id,
            examiner_id=self.examiner.id,
            total_marks=85.0,
            remarks="Excellent architecture diagrams.",
        )
        db.session.add(eval_rec)
        db.session.commit()

        report = send_monthly_admin_report()

        self.assertEqual(report["status"], "success")
        self.assertEqual(report["admin_email"], "admin@emp.local")
        self.assertGreaterEqual(report["total_exams"], 1)
        self.assertGreaterEqual(report["total_bookings"], 1)
        self.assertGreaterEqual(report["completed_evaluations"], 1)
        self.assertGreaterEqual(report["examiner_count"], 1)

        mock_send_email.assert_called_once()
        args, kwargs = mock_send_email.call_args
        self.assertEqual(kwargs["to_email"], "admin@emp.local")
        self.assertIn("Monthly Examination", kwargs["subject"])

    # ---------------------------------------------------------------------
    # Test 6: SMTP Email Functionality Using Mocks
    # ---------------------------------------------------------------------
    @patch("smtplib.SMTP")
    def test_06_smtp_email_functionality_with_mocks(self, mock_smtp_class):
        """Verifies SMTP client connects and dispatches proper MIME message structure."""
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__.return_value = mock_server

        success = send_email(
            to_email="testuser@emp.local",
            subject="Test Subject",
            body_text="Hello Plain Text",
            body_html="<p>Hello HTML</p>",
        )

        self.assertTrue(success)
        mock_smtp_class.assert_called_once_with("localhost", 1025, timeout=10)
        mock_server.send_message.assert_called_once()
        msg = mock_server.send_message.call_args[0][0]
        self.assertEqual(msg["Subject"], "Test Subject")
        self.assertEqual(msg["To"], "testuser@emp.local")

    # ---------------------------------------------------------------------
    # Test 7: Student CSV Export
    # ---------------------------------------------------------------------
    def test_07_student_csv_export(self):
        """Verifies asynchronous CSV export generates output file with success state."""
        slot = ExaminationSlot(
            exam_id=self.exam.id,
            examiner_id=self.examiner.id,
            exam_date=date(2026, 11, 20),
            start_time=dtime(10, 0),
            end_time=dtime(10, 30),
            max_capacity=5,
            available_seats=4,
            status="Available",
        )
        db.session.add(slot)
        db.session.commit()

        booking = Booking(student_id=self.student.id, slot_id=slot.id, exam_id=self.exam.id, status="Booked")
        db.session.add(booking)
        db.session.commit()

        # Run export task eagerly
        res = export_student_csv.apply(args=[self.student.id])
        data = res.result

        self.assertEqual(data["status"], "SUCCESS")
        self.assertEqual(data["student_id"], self.student.id)
        self.assertTrue(os.path.isfile(data["filepath"]))
        self.assertGreaterEqual(data["total_rows"], 1)

    # ---------------------------------------------------------------------
    # Test 8: CSV Content Correctness
    # ---------------------------------------------------------------------
    def test_08_csv_content_correctness(self):
        """Verifies generated CSV contains exact 6 mandatory columns and valid row data."""
        slot = ExaminationSlot(
            exam_id=self.exam.id,
            examiner_id=self.examiner.id,
            exam_date=date(2026, 11, 22),
            start_time=dtime(15, 0),
            end_time=dtime(15, 30),
            max_capacity=5,
            available_seats=4,
            status="Completed",
        )
        db.session.add(slot)
        db.session.commit()

        booking = Booking(student_id=self.student.id, slot_id=slot.id, exam_id=self.exam.id, status="Completed")
        db.session.add(booking)
        db.session.commit()

        eval_rec = Evaluation(
            booking_id=booking.id,
            student_id=self.student.id,
            examiner_id=self.examiner.id,
            total_marks=92.5,
        )
        db.session.add(eval_rec)
        db.session.commit()

        task_res = export_student_csv.apply(args=[self.student.id]).result
        filepath = task_res["filepath"]

        with open(filepath, mode="r", encoding="utf-8") as f:
            reader = list(csv.reader(f))

        # Mandatory columns
        expected_headers = [
            "Examination Name",
            "Course",
            "Examination Date",
            "Examiner",
            "Marks Obtained",
            "Booking Status",
        ]
        self.assertEqual(reader[0], expected_headers)

        row = reader[1]
        self.assertEqual(row[0], "Cloud Midterm")
        self.assertIn("CS701", row[1])
        self.assertEqual(row[2], "2026-11-22")
        self.assertEqual(row[3], "Dr. Charlie")
        self.assertEqual(row[4], "92.5")
        self.assertEqual(row[5], "Completed")

    # ---------------------------------------------------------------------
    # Test 9: Student Export Authorization
    # ---------------------------------------------------------------------
    def test_09_student_export_authorization(self):
        """Verifies student cannot download or access export file belonging to another student."""
        # Student 1 triggers an export through the API
        csrf1 = self.login_user(self.student)
        res_export = self.client.post("/api/student/export", headers={"X-CSRF-Token": csrf1})
        self.assertEqual(res_export.status_code, 202)
        task_id = res_export.get_json()["task_id"]

        # Student 2 logs in
        csrf2 = self.login_user(self.other_student)

        # Student 2 attempts to query status of Student 1's export
        res_status = self.client.get(f"/api/student/export/status/{task_id}")
        self.assertEqual(res_status.status_code, 403)
        self.assertIn("Unauthorized", res_status.get_json()["error"])

        # Student 2 attempts to download Student 1's export
        res_download = self.client.get(f"/api/student/export/download/{task_id}")
        self.assertEqual(res_download.status_code, 403)
        self.assertIn("Unauthorized", res_download.get_json()["error"])

    # ---------------------------------------------------------------------
    # Test 10: Export Status and Download Endpoints
    # ---------------------------------------------------------------------
    def test_10_export_status_and_download(self):
        """Tests complete student workflow: trigger export -> check status -> download CSV."""
        csrf = self.login_user(self.student)

        # 1. Trigger export
        res_trigger = self.client.post(
            "/api/student/export",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(res_trigger.status_code, 202)
        data_trigger = res_trigger.get_json()
        task_id = data_trigger["task_id"]
        self.assertIsNotNone(task_id)

        # 2. Check status
        res_status = self.client.get(f"/api/student/export/status/{task_id}")
        self.assertEqual(res_status.status_code, 200)
        data_status = res_status.get_json()
        self.assertEqual(data_status["status"], "SUCCESS")
        self.assertTrue(data_status["ready"])

        # 3. Download CSV
        res_download = self.client.get(f"/api/student/export/download/{task_id}")
        self.assertEqual(res_download.status_code, 200)
        self.assertEqual(res_download.mimetype, "text/csv")
        self.assertIn("attachment", res_download.headers.get("Content-Disposition", ""))
        self.assertIn("Examination Name", res_download.get_data(as_text=True))
        res_download.close()

    # ---------------------------------------------------------------------
    # Test 11: Redis Cache TTL
    # ---------------------------------------------------------------------
    def test_11_redis_cache_ttl(self):
        """Verifies cache entries respect explicit TTL and expire after timeout."""
        cache_key = "test_ttl_key"
        cache_set(cache_key, {"exam_name": "Cached Exam"}, timeout=1)

        # Immediately available
        val = cache_get(cache_key)
        self.assertIsNotNone(val)
        self.assertEqual(val["exam_name"], "Cached Exam")

        # After timeout expires
        time.sleep(1.1)
        expired_val = cache_get(cache_key)
        self.assertIsNone(expired_val)

    # ---------------------------------------------------------------------
    # Test 12: Cache Invalidation
    # ---------------------------------------------------------------------
    def test_12_cache_invalidation(self):
        """Verifies cache entries matching pattern are cleared on invalidate_exam_cache."""
        cache_set("public_exams:Cloud:Booking Open", [{"id": 1, "name": "Exam 1"}], timeout=60)
        cache_set("courses_list", [{"id": 10, "code": "CS701"}], timeout=60)
        cache_set("unrelated_key", "keep_me", timeout=60)

        # Verify items exist
        self.assertIsNotNone(cache_get("public_exams:Cloud:Booking Open"))
        self.assertIsNotNone(cache_get("courses_list"))

        # Invalidate
        invalidate_exam_cache()

        # Public exam and courses caches are cleared
        self.assertIsNone(cache_get("public_exams:Cloud:Booking Open"))
        self.assertIsNone(cache_get("courses_list"))
        # Unrelated key remains
        self.assertEqual(cache_get("unrelated_key"), "keep_me")

    # ---------------------------------------------------------------------
    # Test 13: Task Failure Handling
    # ---------------------------------------------------------------------
    def test_13_task_failure_handling(self):
        """Verifies invalid task inputs raise descriptive errors handled gracefully."""
        # Non-existent student ID
        with self.assertRaises(ValueError) as ctx:
            export_student_csv(99999)
        self.assertIn("not found", str(ctx.exception).lower())

        # Calling download with non-existent task returns 400
        csrf = self.login_user(self.student)
        res = self.client.get("/api/student/export/download/invalid-task-id-12345")
        self.assertEqual(res.status_code, 400)
