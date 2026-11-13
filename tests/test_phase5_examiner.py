"""
Phase 5 Examiner Functionality Tests.
Tests Examiner dashboard, assigned examinations, slot management, timeline enforcement,
booked students, rubric scoring, evaluation submission, and slot completion.
"""
import unittest
from datetime import datetime, date, time, timedelta

from backend.app import create_app, init_database
from backend.extensions import db
from backend.models import (
    User,
    Course,
    Examination,
    ExamAssignment,
    Rubric,
    ExaminationSlot,
    Booking,
    Evaluation,
    EvaluationScore,
)


class Phase5ExaminerTestCase(unittest.TestCase):
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

        # 1. Create Course
        self.course = Course(course_code="CS301", course_name="Distributed Systems")
        db.session.add(self.course)

        # 2. Create Examiner 1
        self.examiner1 = User(email="examiner1@emp.local", full_name="Dr. Leslie Lamport", role="examiner")
        self.examiner1.set_password("Lamport@123")
        db.session.add(self.examiner1)

        # 3. Create Examiner 2
        self.examiner2 = User(email="examiner2@emp.local", full_name="Dr. Barbara Liskov", role="examiner")
        self.examiner2.set_password("Liskov@123")
        db.session.add(self.examiner2)

        # 4. Create Student
        self.student = User(email="student@emp.local", full_name="Alice Student", role="student")
        self.student.set_password("Alice@123")
        db.session.add(self.student)
        db.session.commit()

        # 5. Create Examination with open slot creation
        now = datetime.now()
        self.exam = Examination(
            course_id=self.course.id,
            exam_name="Distributed Systems Viva",
            duration_minutes=30,
            max_marks=50,
            slot_creation_start_date=now - timedelta(days=2),
            slot_creation_end_date=now + timedelta(days=5),
            slot_booking_start_date=now + timedelta(days=6),
            slot_booking_end_date=now + timedelta(days=10),
            status="Slot Creation"
        )
        db.session.add(self.exam)
        db.session.commit()

        # Assign Examiner 1 to Exam
        assign = ExamAssignment(exam_id=self.exam.id, examiner_id=self.examiner1.id)
        db.session.add(assign)

        # Add Rubrics
        self.rubric1 = Rubric(exam_id=self.exam.id, criterion_name="Consensus Protocols", max_marks=25)
        self.rubric2 = Rubric(exam_id=self.exam.id, criterion_name="Fault Tolerance", max_marks=25)
        db.session.add_all([self.rubric1, self.rubric2])
        db.session.commit()

        # Login as Examiner 1
        self.client1 = self.app.test_client()
        login1 = self.client1.post("/api/auth/login", json={"email": "examiner1@emp.local", "password": "Lamport@123"})
        self.csrf1 = login1.get_json()["csrf_token"]
        self.headers1 = {"X-CSRF-Token": self.csrf1}

        # Login as Examiner 2
        self.client2 = self.app.test_client()
        login2 = self.client2.post("/api/auth/login", json={"email": "examiner2@emp.local", "password": "Liskov@123"})
        self.csrf2 = login2.get_json()["csrf_token"]
        self.headers2 = {"X-CSRF-Token": self.csrf2}

    def tearDown(self):
        db.session.remove()
        db.drop_all()

    def test_01_examiner_authorization(self):
        """Verify unauthenticated (401) and student (403) access to examiner routes is rejected."""
        anon = self.app.test_client()
        res_anon = anon.get("/api/examiner/dashboard")
        self.assertEqual(res_anon.status_code, 401)

        stu = self.app.test_client()
        stu.post("/api/auth/login", json={"email": "student@emp.local", "password": "Alice@123"})
        res_stu = stu.get("/api/examiner/dashboard")
        self.assertEqual(res_stu.status_code, 403)

    def test_02_assigned_examination_visibility(self):
        """Examiner 1 sees assigned exam; Examiner 2 sees empty list."""
        res1 = self.client1.get("/api/examiner/examinations")
        self.assertEqual(res1.status_code, 200)
        exams1 = res1.get_json()["examinations"]
        self.assertEqual(len(exams1), 1)
        self.assertEqual(exams1[0]["exam_name"], "Distributed Systems Viva")

        res2 = self.client2.get("/api/examiner/examinations")
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(len(res2.get_json()["examinations"]), 0)

    def test_03_slot_creation_within_allowed_dates(self):
        """Examiner successfully creates an examination slot within allowed window."""
        slot_date = (date.today() + timedelta(days=7)).isoformat()
        res = self.client1.post("/api/examiner/slots", headers=self.headers1, json={
            "exam_id": self.exam.id,
            "exam_date": slot_date,
            "start_time": "10:00",
            "end_time": "11:00",
            "max_capacity": 5
        })
        self.assertEqual(res.status_code, 201)
        slot = res.get_json()["slot"]
        self.assertEqual(slot["max_capacity"], 5)
        self.assertEqual(slot["available_seats"], 5)
        self.assertEqual(slot["status"], "Available")

    def test_04_rejection_outside_allowed_dates_or_unassigned(self):
        """Rejects slot creation when window is closed or examiner is unassigned."""
        # Case A: Examiner 2 is not assigned
        slot_date = (date.today() + timedelta(days=7)).isoformat()
        res_unassigned = self.client2.post("/api/examiner/slots", headers=self.headers2, json={
            "exam_id": self.exam.id,
            "exam_date": slot_date,
            "start_time": "10:00",
            "end_time": "11:00",
            "max_capacity": 5
        })
        self.assertEqual(res_unassigned.status_code, 403)

        # Case B: Examination status is Closed
        self.exam.status = "Closed"
        db.session.commit()

        res_closed = self.client1.post("/api/examiner/slots", headers=self.headers1, json={
            "exam_id": self.exam.id,
            "exam_date": slot_date,
            "start_time": "10:00",
            "end_time": "11:00",
            "max_capacity": 5
        })
        self.assertEqual(res_closed.status_code, 400)
        self.assertIn("closed", res_closed.get_json()["error"].lower())

    def test_05_invalid_slot_capacity_and_times(self):
        """Rejects invalid times (start >= end) and capacity <= 0."""
        slot_date = (date.today() + timedelta(days=7)).isoformat()

        # Start >= End
        res_time = self.client1.post("/api/examiner/slots", headers=self.headers1, json={
            "exam_id": self.exam.id,
            "exam_date": slot_date,
            "start_time": "12:00",
            "end_time": "11:00",
            "max_capacity": 5
        })
        self.assertEqual(res_time.status_code, 400)

        # Capacity <= 0
        res_cap = self.client1.post("/api/examiner/slots", headers=self.headers1, json={
            "exam_id": self.exam.id,
            "exam_date": slot_date,
            "start_time": "10:00",
            "end_time": "11:00",
            "max_capacity": 0
        })
        self.assertEqual(res_cap.status_code, 400)

    def test_06_slot_editing_and_deletion_restrictions(self):
        """Slots can be edited/deleted before booking starts, but blocked once bookings exist."""
        slot_date = (date.today() + timedelta(days=7)).isoformat()
        res_create = self.client1.post("/api/examiner/slots", headers=self.headers1, json={
            "exam_id": self.exam.id,
            "exam_date": slot_date,
            "start_time": "14:00",
            "end_time": "15:00",
            "max_capacity": 3
        })
        slot_id = res_create.get_json()["slot"]["id"]

        # Edit slot (succeeds before booking)
        res_edit = self.client1.put(f"/api/examiner/slots/{slot_id}", headers=self.headers1, json={
            "max_capacity": 4
        })
        self.assertEqual(res_edit.status_code, 200)
        self.assertEqual(res_edit.get_json()["slot"]["max_capacity"], 4)

        # Simulate a student booking
        booking = Booking(student_id=self.student.id, slot_id=slot_id, exam_id=self.exam.id, status="Booked")
        db.session.add(booking)
        db.session.commit()

        # Edit blocked once booking exists
        res_edit_blocked = self.client1.put(f"/api/examiner/slots/{slot_id}", headers=self.headers1, json={
            "max_capacity": 5
        })
        self.assertEqual(res_edit_blocked.status_code, 400)

        # Delete blocked once booking exists
        res_del_blocked = self.client1.delete(f"/api/examiner/slots/{slot_id}", headers=self.headers1)
        self.assertEqual(res_del_blocked.status_code, 400)

    def test_07_unauthorized_slot_access(self):
        """Examiner 2 cannot modify or delete Examiner 1's slot."""
        slot = ExaminationSlot(
            exam_id=self.exam.id,
            examiner_id=self.examiner1.id,
            exam_date=date.today() + timedelta(days=7),
            start_time=time(9, 0),
            end_time=time(10, 0),
            max_capacity=5,
            available_seats=5,
            status="Available"
        )
        db.session.add(slot)
        db.session.commit()

        # Examiner 2 tries to edit
        res_edit = self.client2.put(f"/api/examiner/slots/{slot.id}", headers=self.headers2, json={"max_capacity": 10})
        self.assertEqual(res_edit.status_code, 403)

        # Examiner 2 tries to delete
        res_del = self.client2.delete(f"/api/examiner/slots/{slot.id}", headers=self.headers2)
        self.assertEqual(res_del.status_code, 403)

    def test_08_booked_student_visibility(self):
        """Examiner sees students booked into their slots only."""
        slot = ExaminationSlot(
            exam_id=self.exam.id,
            examiner_id=self.examiner1.id,
            exam_date=date.today() + timedelta(days=7),
            start_time=time(9, 0),
            end_time=time(10, 0),
            max_capacity=2,
            available_seats=1,
            status="Available"
        )
        db.session.add(slot)
        db.session.commit()

        booking = Booking(student_id=self.student.id, slot_id=slot.id, exam_id=self.exam.id, status="Booked")
        db.session.add(booking)
        db.session.commit()

        # Examiner 1 sees student
        res1 = self.client1.get("/api/examiner/booked-students")
        self.assertEqual(res1.status_code, 200)
        students1 = res1.get_json()["booked_students"]
        self.assertEqual(len(students1), 1)
        self.assertEqual(students1[0]["student_name"], "Alice Student")

        # Examiner 2 sees no students
        res2 = self.client2.get("/api/examiner/booked-students")
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(len(res2.get_json()["booked_students"]), 0)

    def test_09_rubric_marks_validation_and_evaluation(self):
        """Tests rubric criteria marks validation, calculation, and submission."""
        slot = ExaminationSlot(
            exam_id=self.exam.id,
            examiner_id=self.examiner1.id,
            exam_date=date.today() + timedelta(days=7),
            start_time=time(9, 0),
            end_time=time(10, 0),
            max_capacity=2,
            available_seats=1,
            status="Available"
        )
        db.session.add(slot)
        db.session.commit()

        booking = Booking(student_id=self.student.id, slot_id=slot.id, exam_id=self.exam.id, status="Booked")
        db.session.add(booking)
        db.session.commit()

        # Case A: Marks exceed criterion max (rubric1 max is 25)
        bad_score_res = self.client1.post(f"/api/examiner/bookings/{booking.id}/evaluate", headers=self.headers1, json={
            "rubric_scores": [
                {"rubric_id": self.rubric1.id, "marks_obtained": 30.0},
                {"rubric_id": self.rubric2.id, "marks_obtained": 20.0}
            ],
            "remarks": "Exceeds max"
        })
        self.assertEqual(bad_score_res.status_code, 400)
        self.assertIn("exceed maximum", bad_score_res.get_json()["error"])

        # Case B: Negative marks rejected
        neg_res = self.client1.post(f"/api/examiner/bookings/{booking.id}/evaluate", headers=self.headers1, json={
            "rubric_scores": [
                {"rubric_id": self.rubric1.id, "marks_obtained": -5.0},
                {"rubric_id": self.rubric2.id, "marks_obtained": 20.0}
            ]
        })
        self.assertEqual(neg_res.status_code, 400)

        # Case C: Valid submission (20 + 22 = 42 out of 50)
        good_res = self.client1.post(f"/api/examiner/bookings/{booking.id}/evaluate", headers=self.headers1, json={
            "rubric_scores": [
                {"rubric_id": self.rubric1.id, "marks_obtained": 20.0},
                {"rubric_id": self.rubric2.id, "marks_obtained": 22.0}
            ],
            "remarks": "Solid understanding of Paxos and Raft."
        })
        self.assertEqual(good_res.status_code, 200)
        eval_data = good_res.get_json()["evaluation"]
        self.assertEqual(eval_data["total_marks"], 42.0)
        self.assertEqual(len(eval_data["rubric_scores"]), 2)

        # Verify booking status updated to Completed
        db.session.refresh(booking)
        self.assertEqual(booking.status, "Completed")

    def test_10_unauthorized_evaluation_rejected(self):
        """Examiner 2 cannot evaluate a student booked into Examiner 1's slot."""
        slot = ExaminationSlot(
            exam_id=self.exam.id,
            examiner_id=self.examiner1.id,
            exam_date=date.today() + timedelta(days=7),
            start_time=time(9, 0),
            end_time=time(10, 0),
            max_capacity=2,
            available_seats=1,
            status="Available"
        )
        db.session.add(slot)
        db.session.commit()

        booking = Booking(student_id=self.student.id, slot_id=slot.id, exam_id=self.exam.id, status="Booked")
        db.session.add(booking)
        db.session.commit()

        # Examiner 2 tries to evaluate
        res = self.client2.post(f"/api/examiner/bookings/{booking.id}/evaluate", headers=self.headers2, json={
            "rubric_scores": [
                {"rubric_id": self.rubric1.id, "marks_obtained": 15.0},
                {"rubric_id": self.rubric2.id, "marks_obtained": 15.0}
            ]
        })
        self.assertEqual(res.status_code, 403)

    def test_11_slot_completion_validation(self):
        """Slot cannot be completed if evaluations are pending; succeeds when all evaluated."""
        slot = ExaminationSlot(
            exam_id=self.exam.id,
            examiner_id=self.examiner1.id,
            exam_date=date.today() + timedelta(days=7),
            start_time=time(9, 0),
            end_time=time(10, 0),
            max_capacity=2,
            available_seats=1,
            status="Available"
        )
        db.session.add(slot)
        db.session.commit()

        booking = Booking(student_id=self.student.id, slot_id=slot.id, exam_id=self.exam.id, status="Booked")
        db.session.add(booking)
        db.session.commit()

        # Step 1: Attempt to complete slot while student is not evaluated -> 400
        comp_res_blocked = self.client1.post(f"/api/examiner/slots/{slot.id}/complete", headers=self.headers1)
        self.assertEqual(comp_res_blocked.status_code, 400)
        self.assertIn("pending evaluations", comp_res_blocked.get_json()["error"])

        # Step 2: Submit evaluation for student
        self.client1.post(f"/api/examiner/bookings/{booking.id}/evaluate", headers=self.headers1, json={
            "rubric_scores": [
                {"rubric_id": self.rubric1.id, "marks_obtained": 20.0},
                {"rubric_id": self.rubric2.id, "marks_obtained": 20.0}
            ],
            "remarks": "Completed successfully."
        })

        # Step 3: Now complete slot -> 200 OK
        comp_res_ok = self.client1.post(f"/api/examiner/slots/{slot.id}/complete", headers=self.headers1)
        self.assertEqual(comp_res_ok.status_code, 200)
        self.assertEqual(comp_res_ok.get_json()["slot"]["status"], "Completed")

        # Step 4: After completion, modifying evaluation is locked
        res_locked = self.client1.post(f"/api/examiner/bookings/{booking.id}/evaluate", headers=self.headers1, json={
            "rubric_scores": [
                {"rubric_id": self.rubric1.id, "marks_obtained": 25.0},
                {"rubric_id": self.rubric2.id, "marks_obtained": 25.0}
            ]
        })
        self.assertEqual(res_locked.status_code, 400)
        self.assertIn("slot has already been completed", res_locked.get_json()["error"])


if __name__ == "__main__":
    unittest.main()

