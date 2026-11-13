import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from threading import Barrier
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from backend.app import create_app, init_database
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


class Phase6StudentTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        cls.app.config["ADMIN_EMAIL"] = "admin@emp.local"
        cls.app.config["ADMIN_PASSWORD"] = "Admin@123"
        cls.ctx = cls.app.app_context()
        cls.ctx.push()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.pop()

    def setUp(self):
        db.create_all()
        init_database(self.app)

        self.course = Course(course_code="CS601", course_name="Distributed Systems")
        self.student = self.make_user("student@emp.local", "Student One", "student")
        self.other_student = self.make_user("other@emp.local", "Student Two", "student")
        self.examiner = self.make_user("examiner@emp.local", "Dr. Examiner", "examiner")
        db.session.add_all([self.course, self.student, self.other_student, self.examiner])
        db.session.commit()

        now = datetime.now()
        self.exam = Examination(
            course_id=self.course.id,
            exam_name="Distributed Systems Viva",
            duration_minutes=30,
            max_marks=50,
            slot_creation_start_date=now - timedelta(days=5),
            slot_creation_end_date=now - timedelta(days=1),
            slot_booking_start_date=now - timedelta(days=1),
            slot_booking_end_date=now + timedelta(days=5),
            status="Booking Open",
        )
        db.session.add(self.exam)
        db.session.commit()

        self.rubric = Rubric(
            exam_id=self.exam.id,
            criterion_name="Technical Knowledge",
            max_marks=50,
        )
        self.slot = ExaminationSlot(
            exam_id=self.exam.id,
            examiner_id=self.examiner.id,
            exam_date=date.today() + timedelta(days=2),
            start_time=time(10, 0),
            end_time=time(10, 30),
            max_capacity=2,
            available_seats=2,
            status="Available",
        )
        db.session.add_all([self.rubric, self.slot])
        db.session.commit()

        self.client = self.app.test_client()
        login = self.client.post("/api/auth/login", json={
            "email": self.student.email,
            "password": "Password@123",
        })
        self.csrf_token = login.get_json()["csrf_token"]
        self.headers = {"X-CSRF-Token": self.csrf_token}

    def tearDown(self):
        db.session.remove()
        db.drop_all()

    @staticmethod
    def make_user(email, full_name, role):
        user = User(email=email, full_name=full_name, role=role)
        user.set_password("Password@123")
        return user

    def add_booking(self, student=None, slot=None, status="Booked", evaluated=False):
        student = student or self.student
        slot = slot or self.slot
        booking = Booking(
            student_id=student.id,
            slot_id=slot.id,
            exam_id=self.exam.id,
            status=status,
        )
        db.session.add(booking)
        db.session.flush()
        if evaluated:
            evaluation = Evaluation(
                booking_id=booking.id,
                student_id=student.id,
                examiner_id=self.examiner.id,
                total_marks=40,
                remarks="Good work.",
                status="Evaluated",
            )
            db.session.add(evaluation)
        db.session.commit()
        return booking

    def book(self, client=None, slot_id=None):
        client = client or self.client
        return client.post(
            "/api/student/book",
            headers=self.headers,
            json={"slot_id": slot_id or self.slot.id},
        )

    def test_01_student_only_authorization(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get("/api/student/dashboard").status_code, 401)

        admin = self.app.test_client()
        admin.post("/api/auth/login", json={
            "email": "admin@emp.local",
            "password": "Admin@123",
        })
        self.assertEqual(admin.get("/api/student/dashboard").status_code, 403)

    def test_02_examination_search_by_name_and_course(self):
        by_name = self.client.get("/api/student/examinations?search=Distributed")
        self.assertEqual(by_name.status_code, 200)
        self.assertEqual(len(by_name.get_json()["examinations"]), 1)

        by_course = self.client.get("/api/student/examinations?search=CS601")
        self.assertEqual(len(by_course.get_json()["examinations"]), 1)

        draft = Examination(
            course_id=self.course.id,
            exam_name="Hidden Draft",
            duration_minutes=30,
            max_marks=50,
            slot_creation_start_date=datetime.now(),
            slot_creation_end_date=datetime.now() + timedelta(days=1),
            slot_booking_start_date=datetime.now(),
            slot_booking_end_date=datetime.now() + timedelta(days=2),
            status="Draft",
        )
        db.session.add(draft)
        db.session.commit()
        self.assertEqual(
            self.client.get("/api/student/examinations?status=Draft").get_json()["examinations"],
            [],
        )

    def test_03_examination_details_are_returned_with_slots(self):
        response = self.client.get(f"/api/student/examinations/{self.exam.id}/slots")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["examination"]["exam_name"], self.exam.exam_name)
        self.assertEqual(data["slots"][0]["examiner_name"], self.examiner.full_name)

    def test_04_available_slots_show_schedule_and_capacity(self):
        response = self.client.get(f"/api/student/examinations/{self.exam.id}/slots")
        slot = response.get_json()["slots"][0]
        self.assertEqual(slot["exam_date"], self.slot.exam_date.isoformat())
        self.assertEqual(slot["start_time"], "10:00")
        self.assertEqual(slot["end_time"], "10:30")
        self.assertEqual(slot["available_seats"], 2)

    def test_05_successful_booking_decrements_capacity(self):
        response = self.book()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Booking.query.filter_by(student_id=self.student.id).count(), 1)
        db.session.refresh(self.slot)
        self.assertEqual(self.slot.available_seats, 1)

    def test_06_booking_outside_dates_and_status_override_is_rejected(self):
        now = datetime.now()
        self.exam.slot_booking_start_date = now + timedelta(days=1)
        self.exam.slot_booking_end_date = now + timedelta(days=2)
        db.session.commit()

        dashboard = self.client.get("/api/student/dashboard").get_json()
        search = self.client.get("/api/student/examinations").get_json()["examinations"][0]
        slots = self.client.get(f"/api/student/examinations/{self.exam.id}/slots").get_json()
        self.assertEqual(dashboard["stats"]["available_examinations"], 0)
        self.assertFalse(search["is_booking_open"])
        self.assertFalse(slots["is_booking_open"])
        self.assertEqual(self.book().status_code, 400)

        # Admin's Booking Open status does not bypass the configured date range.
        self.exam.slot_booking_start_date = now - timedelta(days=2)
        self.exam.slot_booking_end_date = now - timedelta(days=1)
        db.session.commit()
        self.assertEqual(self.book().status_code, 400)

        self.exam.slot_booking_start_date = now - timedelta(days=1)
        self.exam.slot_booking_end_date = now + timedelta(days=1)
        self.exam.status = "Closed"
        db.session.commit()
        self.assertEqual(self.book().status_code, 400)

    def test_07_duplicate_active_booking_is_rejected(self):
        self.assertEqual(self.book().status_code, 201)
        duplicate = self.book()
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(Booking.query.filter_by(student_id=self.student.id).count(), 1)

    def test_08_rebooking_after_cancellation_is_allowed(self):
        booking_id = self.book().get_json()["booking"]["id"]
        cancelled = self.client.post(
            f"/api/student/bookings/{booking_id}/cancel",
            headers=self.headers,
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(self.book().status_code, 201)
        self.assertEqual(
            Booking.query.filter_by(student_id=self.student.id, status="Booked").count(),
            1,
        )

    def test_09_booking_after_evaluation_or_completion_is_rejected(self):
        self.add_booking(status="Completed", evaluated=True)
        response = self.book()
        self.assertEqual(response.status_code, 400)
        self.assertIn("already completed", response.get_json()["error"])

    def test_10_full_slot_is_rejected(self):
        self.slot.available_seats = 0
        self.slot.status = "Full"
        db.session.commit()
        self.assertEqual(self.book().status_code, 400)

    def test_11_cancellation_before_deadline_succeeds(self):
        self.slot.available_seats = 1
        booking = self.add_booking()
        response = self.client.post(
            f"/api/student/bookings/{booking.id}/cancel",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        db.session.refresh(booking)
        self.assertEqual(booking.status, "Cancelled")

    def test_12_cancellation_after_deadline_is_rejected(self):
        booking = self.add_booking()
        self.exam.slot_booking_end_date = datetime.now() - timedelta(seconds=1)
        db.session.commit()

        response = self.client.post(
            f"/api/student/bookings/{booking.id}/cancel",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)
        db.session.refresh(booking)
        self.assertEqual(booking.status, "Booked")

    def test_13_cancellation_restores_seat_without_exceeding_capacity(self):
        self.slot.available_seats = 1
        booking = self.add_booking()
        self.assertEqual(
            self.client.post(
                f"/api/student/bookings/{booking.id}/cancel",
                headers=self.headers,
            ).status_code,
            200,
        )
        db.session.refresh(self.slot)
        self.assertEqual(self.slot.available_seats, self.slot.max_capacity)
        self.assertEqual(self.slot.status, "Available")

    def test_14_repeated_cancellation_does_not_restore_seat_twice(self):
        self.slot.available_seats = 1
        booking = self.add_booking()
        endpoint = f"/api/student/bookings/{booking.id}/cancel"
        self.assertEqual(self.client.post(endpoint, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.post(endpoint, headers=self.headers).status_code, 400)
        db.session.refresh(self.slot)
        self.assertEqual(self.slot.available_seats, self.slot.max_capacity)

    def test_15_only_booking_owner_can_cancel(self):
        booking = self.add_booking()
        other_client = self.app.test_client()
        login = other_client.post("/api/auth/login", json={
            "email": self.other_student.email,
            "password": "Password@123",
        })
        response = other_client.post(
            f"/api/student/bookings/{booking.id}/cancel",
            headers={"X-CSRF-Token": login.get_json()["csrf_token"]},
        )
        self.assertEqual(response.status_code, 403)
        db.session.refresh(self.slot)
        self.assertEqual(self.slot.available_seats, 2)

    def test_16_only_evaluated_results_are_visible_to_their_student(self):
        pending_booking = self.add_booking()
        pending = Evaluation(
            booking_id=pending_booking.id,
            student_id=self.student.id,
            examiner_id=self.examiner.id,
            total_marks=30,
            remarks="Pending result",
            status="Pending",
        )
        db.session.add(pending)
        db.session.commit()
        self.assertEqual(self.client.get("/api/student/results").get_json()["results"], [])

        pending.status = "Evaluated"
        pending_booking.status = "Completed"
        score = EvaluationScore(
            evaluation_id=pending.id,
            rubric_id=self.rubric.id,
            marks_obtained=30,
        )
        db.session.add(score)
        other_booking = self.add_booking(
            student=self.other_student,
            status="Completed",
            evaluated=True,
        )
        db.session.commit()
        results = self.client.get("/api/student/results").get_json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["total_marks"], 30)
        self.assertEqual(results[0]["remarks"], "Pending result")
        self.assertEqual(results[0]["rubric_scores"][0]["marks_obtained"], 30)
        self.assertNotEqual(results[0]["booking_id"], other_booking.id)

    def test_17_student_can_view_and_update_profile(self):
        profile = self.client.get("/api/student/profile")
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.get_json()["profile"]["email"], self.student.email)

        updated = self.client.put(
            "/api/student/profile",
            headers=self.headers,
            json={"full_name": "Updated Student"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["profile"]["full_name"], "Updated Student")
        self.assertEqual(updated.get_json()["profile"]["email"], self.student.email)

    def test_18_failed_booking_commit_rolls_back_capacity(self):
        with patch.object(
            db.session,
            "commit",
            side_effect=OperationalError("COMMIT", {}, RuntimeError("simulated failure")),
        ):
            response = self.book()
        self.assertEqual(response.status_code, 500)
        db.session.expire_all()
        self.assertEqual(Booking.query.filter_by(student_id=self.student.id).count(), 0)
        self.assertEqual(db.session.get(ExaminationSlot, self.slot.id).available_seats, 2)

    def test_19_concurrent_bookings_do_not_overbook_a_sqlite_slot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = os.path.join(temp_dir, "concurrent.db").replace("\\", "/")

            class FileDatabaseConfig(Config):
                SQLALCHEMY_DATABASE_URI = "sqlite:///" + database_path

            app = create_app(FileDatabaseConfig)
            app.config["TESTING"] = True
            app.config["ADMIN_EMAIL"] = "admin@emp.local"
            app.config["ADMIN_PASSWORD"] = "Admin@123"
            barrier = Barrier(2)

            with app.app_context():
                db.create_all()
                init_database(app)
                course = Course(course_code="CS699", course_name="Concurrency")
                examiner = self.make_user("racing-examiner@emp.local", "Examiner", "examiner")
                students = [
                    self.make_user("racer1@emp.local", "Racer One", "student"),
                    self.make_user("racer2@emp.local", "Racer Two", "student"),
                ]
                db.session.add_all([course, examiner] + students)
                db.session.commit()
                now = datetime.now()
                exam = Examination(
                    course_id=course.id,
                    exam_name="Concurrent Booking",
                    duration_minutes=30,
                    max_marks=50,
                    slot_creation_start_date=now - timedelta(days=2),
                    slot_creation_end_date=now - timedelta(days=1),
                    slot_booking_start_date=now - timedelta(days=1),
                    slot_booking_end_date=now + timedelta(days=1),
                    status="Booking Open",
                )
                db.session.add(exam)
                db.session.commit()
                slot = ExaminationSlot(
                    exam_id=exam.id,
                    examiner_id=examiner.id,
                    exam_date=date.today() + timedelta(days=1),
                    start_time=time(9, 0),
                    end_time=time(9, 30),
                    max_capacity=1,
                    available_seats=1,
                    status="Available",
                )
                db.session.add(slot)
                db.session.commit()
                slot_id = slot.id
                credentials = [
                    (student.email, "Password@123") for student in students
                ]

            def attempt_booking(email, password):
                client = app.test_client()
                login = client.post("/api/auth/login", json={
                    "email": email,
                    "password": password,
                })
                barrier.wait(timeout=10)
                return client.post(
                    "/api/student/book",
                    headers={"X-CSRF-Token": login.get_json()["csrf_token"]},
                    json={"slot_id": slot_id},
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(
                    lambda credential: attempt_booking(*credential),
                    credentials,
                ))

            self.assertEqual(sum(response.status_code == 201 for response in responses), 1)
            with app.app_context():
                db.session.remove()
                self.assertEqual(Booking.query.filter_by(slot_id=slot_id, status="Booked").count(), 1)
                saved_slot = db.session.get(ExaminationSlot, slot_id)
                self.assertEqual(saved_slot.available_seats, 0)
                self.assertLessEqual(saved_slot.available_seats, saved_slot.max_capacity)
                db.session.remove()
                db.engine.dispose()

    def test_20_evaluated_booking_cannot_be_cancelled(self):
        self.slot.available_seats = 1
        booking = self.add_booking(status="Completed", evaluated=True)
        response = self.client.post(
            f"/api/student/bookings/{booking.id}/cancel",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)
        db.session.refresh(self.slot)
        self.assertEqual(self.slot.available_seats, 1)

    def test_21_failed_cancellation_rolls_back_status_and_capacity(self):
        self.slot.available_seats = 1
        booking = self.add_booking()
        with patch.object(
            db.session,
            "commit",
            side_effect=OperationalError("COMMIT", {}, RuntimeError("simulated failure")),
        ):
            response = self.client.post(
                f"/api/student/bookings/{booking.id}/cancel",
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 500)
        db.session.expire_all()
        self.assertEqual(db.session.get(Booking, booking.id).status, "Booked")
        self.assertEqual(db.session.get(ExaminationSlot, self.slot.id).available_seats, 1)

    def test_22_cancellation_never_exceeds_maximum_capacity(self):
        booking = self.add_booking()
        response = self.client.post(
            f"/api/student/bookings/{booking.id}/cancel",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 409)
        db.session.refresh(booking)
        self.assertEqual(booking.status, "Booked")
        db.session.refresh(self.slot)
        self.assertEqual(self.slot.available_seats, self.slot.max_capacity)


if __name__ == "__main__":
    unittest.main()
