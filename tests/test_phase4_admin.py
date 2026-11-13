"""
Phase 4 Admin Functionality Tests.
Tests Admin dashboard, Course CRUD, Examination CRUD, Rubrics, Examiner management,
Timelines, Window overrides, Search/Filters, Rescheduling, and Reassignment.
"""
import unittest
from datetime import datetime, date, time

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


class Phase4AdminTestCase(unittest.TestCase):
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

        self.client = self.app.test_client()

        # Log in as Admin and capture CSRF token
        login_res = self.client.post("/api/auth/login", json={
            "email": "admin@emp.local",
            "password": "Admin@123"
        })
        self.csrf_token = login_res.get_json()["csrf_token"]
        self.headers = {"X-CSRF-Token": self.csrf_token}

        # Create a student user for role checks and booking tests
        self.student = User(email="student@emp.local", full_name="Student One", role="student")
        self.student.set_password("Student@123")
        db.session.add(self.student)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()

    def test_01_admin_authorization(self):
        """Verify Admin endpoints reject unauthenticated users (401) and non-admin users (403)."""
        # Unauthenticated client
        anon_client = self.app.test_client()
        res_anon = anon_client.get("/api/admin/dashboard")
        self.assertEqual(res_anon.status_code, 401)

        # Student client
        stu_client = self.app.test_client()
        stu_client.post("/api/auth/login", json={"email": "student@emp.local", "password": "Student@123"})
        res_stu = stu_client.get("/api/admin/dashboard")
        self.assertEqual(res_stu.status_code, 403)

    def test_02_dashboard_statistics(self):
        """Verify dashboard statistics return correct counts."""
        res = self.client.get("/api/admin/dashboard")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("stats", data)
        self.assertEqual(data["stats"]["total_courses"], 0)
        self.assertEqual(data["stats"]["total_students"], 1)
        self.assertEqual(data["stats"]["total_examiners"], 0)

    def test_03_course_crud_and_integrity(self):
        """Test Course creation, retrieval, update, search, and delete protection."""
        # 1. Create Course
        c_res = self.client.post("/api/admin/courses", headers=self.headers, json={
            "course_code": "CS201",
            "course_name": "Modern App Dev 2",
            "description": "Vue, Flask, SQLite, Celery",
            "status": "active"
        })
        self.assertEqual(c_res.status_code, 201)
        c_id = c_res.get_json()["course"]["id"]

        # 2. Duplicate course code rejected
        dup_res = self.client.post("/api/admin/courses", headers=self.headers, json={
            "course_code": "CS201",
            "course_name": "Duplicate Course"
        })
        self.assertEqual(dup_res.status_code, 409)

        # 3. List & Search
        list_res = self.client.get("/api/admin/courses?search=Modern")
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(len(list_res.get_json()["courses"]), 1)

        # 4. Update Course
        up_res = self.client.put(f"/api/admin/courses/{c_id}", headers=self.headers, json={
            "course_code": "CS201",
            "course_name": "Modern Application Development II (Updated)",
            "description": "Updated syllabus"
        })
        self.assertEqual(up_res.status_code, 200)
        self.assertEqual(up_res.get_json()["course"]["course_name"], "Modern Application Development II (Updated)")

        # 5. Delete Course
        del_res = self.client.delete(f"/api/admin/courses/{c_id}", headers=self.headers)
        self.assertEqual(del_res.status_code, 200)

        # Confirm deleted
        get_res = self.client.get(f"/api/admin/courses/{c_id}")
        self.assertEqual(get_res.status_code, 404)

    def test_04_course_delete_prevented_when_exams_attached(self):
        """Verify deleting a course with attached examinations is safely rejected."""
        course = Course(course_code="MATH101", course_name="Calculus")
        db.session.add(course)
        db.session.commit()

        exam = Examination(
            course_id=course.id,
            exam_name="Calculus Midterm",
            duration_minutes=60,
            max_marks=50,
            slot_creation_start_date=datetime(2026, 11, 1),
            slot_creation_end_date=datetime(2026, 11, 5),
            slot_booking_start_date=datetime(2026, 11, 6),
            slot_booking_end_date=datetime(2026, 11, 10)
        )
        db.session.add(exam)
        db.session.commit()

        del_res = self.client.delete(f"/api/admin/courses/{course.id}", headers=self.headers)
        self.assertEqual(del_res.status_code, 400)
        self.assertIn("Cannot delete course", del_res.get_json()["error"])

    def test_05_examination_crud_and_timeline_validation(self):
        """Test Examination CRUD and timeline boundary validation."""
        course = Course(course_code="PHY101", course_name="Physics")
        db.session.add(course)
        db.session.commit()

        # 1. Invalid timeline: start >= end
        inv_res = self.client.post("/api/admin/examinations", headers=self.headers, json={
            "course_id": course.id,
            "exam_name": "Invalid Physics Lab",
            "duration_minutes": 60,
            "max_marks": 50,
            "slot_creation_start_date": "2026-11-10T10:00",
            "slot_creation_end_date": "2026-11-05T10:00",  # End before start
            "slot_booking_start_date": "2026-11-11T10:00",
            "slot_booking_end_date": "2026-11-15T10:00"
        })
        self.assertEqual(inv_res.status_code, 400)
        self.assertIn("Slot Creation Start Date must be earlier", inv_res.get_json()["error"])

        # 2. Valid Examination Creation
        val_res = self.client.post("/api/admin/examinations", headers=self.headers, json={
            "course_id": course.id,
            "exam_name": "Physics Lab Viva",
            "description": "Experiments and oral viva",
            "duration_minutes": 45,
            "max_marks": 40,
            "slot_creation_start_date": "2026-11-01T09:00",
            "slot_creation_end_date": "2026-11-05T18:00",
            "slot_booking_start_date": "2026-11-06T09:00",
            "slot_booking_end_date": "2026-11-10T18:00",
            "status": "Draft"
        })
        self.assertEqual(val_res.status_code, 201)
        exam_id = val_res.get_json()["examination"]["id"]

        # 3. Retrieve and verify
        get_res = self.client.get(f"/api/admin/examinations/{exam_id}")
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.get_json()["examination"]["exam_name"], "Physics Lab Viva")

        # 4. Update Examination
        up_res = self.client.put(f"/api/admin/examinations/{exam_id}", headers=self.headers, json={
            "exam_name": "Physics Lab Viva - Advanced",
            "duration_minutes": 60,
            "max_marks": 50
        })
        self.assertEqual(up_res.status_code, 200)
        self.assertEqual(up_res.get_json()["examination"]["duration_minutes"], 60)

    def test_06_manual_status_override(self):
        """Test manually updating examination lifecycle status (e.g. open/close slot creation/booking)."""
        course = Course(course_code="CHEM101", course_name="Chemistry")
        db.session.add(course)
        db.session.commit()

        exam = Examination(
            course_id=course.id,
            exam_name="Chemistry Practical",
            duration_minutes=60,
            max_marks=100,
            slot_creation_start_date=datetime(2026, 11, 1),
            slot_creation_end_date=datetime(2026, 11, 5),
            slot_booking_start_date=datetime(2026, 11, 6),
            slot_booking_end_date=datetime(2026, 11, 10),
            status="Draft"
        )
        db.session.add(exam)
        db.session.commit()

        # Override to Slot Creation
        res1 = self.client.put(f"/api/admin/examinations/{exam.id}/status", headers=self.headers, json={
            "status": "Slot Creation"
        })
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.get_json()["examination"]["status"], "Slot Creation")

        # Override to Booking Open
        res2 = self.client.put(f"/api/admin/examinations/{exam.id}/status", headers=self.headers, json={
            "status": "Booking Open"
        })
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.get_json()["examination"]["status"], "Booking Open")

        # Invalid status rejected
        res_bad = self.client.put(f"/api/admin/examinations/{exam.id}/status", headers=self.headers, json={
            "status": "InvalidStatus"
        })
        self.assertEqual(res_bad.status_code, 400)

    def test_07_rubric_crud_and_validation(self):
        """Test adding, editing, listing, and deleting rubrics."""
        course = Course(course_code="BIO101", course_name="Biology")
        db.session.add(course)
        db.session.commit()

        exam = Examination(
            course_id=course.id,
            exam_name="Biology Practical",
            duration_minutes=30,
            max_marks=50,
            slot_creation_start_date=datetime(2026, 11, 1),
            slot_creation_end_date=datetime(2026, 11, 5),
            slot_booking_start_date=datetime(2026, 11, 6),
            slot_booking_end_date=datetime(2026, 11, 10)
        )
        db.session.add(exam)
        db.session.commit()

        # 1. Add Rubric
        r_res = self.client.post(f"/api/admin/examinations/{exam.id}/rubrics", headers=self.headers, json={
            "criterion_name": "Specimen Identification",
            "max_marks": 25,
            "description": "Correct identification of slides"
        })
        self.assertEqual(r_res.status_code, 201)
        rubric_id = r_res.get_json()["rubric"]["id"]

        # 2. Add second Rubric
        self.client.post(f"/api/admin/examinations/{exam.id}/rubrics", headers=self.headers, json={
            "criterion_name": "Oral Q&A",
            "max_marks": 25,
            "description": "Viva answers"
        })

        # 3. List Rubrics
        list_res = self.client.get(f"/api/admin/examinations/{exam.id}/rubrics")
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(len(list_res.get_json()["rubrics"]), 2)

        # 4. Edit Rubric
        edit_res = self.client.put(f"/api/admin/rubrics/{rubric_id}", headers=self.headers, json={
            "criterion_name": "Microscope Operation & Identification",
            "max_marks": 30
        })
        self.assertEqual(edit_res.status_code, 200)
        self.assertEqual(edit_res.get_json()["rubric"]["max_marks"], 30.0)

        # 5. Delete Rubric
        del_res = self.client.delete(f"/api/admin/rubrics/{rubric_id}", headers=self.headers)
        self.assertEqual(del_res.status_code, 200)

    def test_08_examiner_creation_and_assignment(self):
        """Test Admin creating an examiner account and assigning/unassigning from examinations."""
        # 1. Create Examiner
        ex_res = self.client.post("/api/admin/examiners", headers=self.headers, json={
            "email": "dr_watson@emp.local",
            "password": "Watson@123",
            "full_name": "Dr. John Watson",
            "department": "Medicine",
            "contact_details": "9988776655"
        })
        self.assertEqual(ex_res.status_code, 201)
        examiner_id = ex_res.get_json()["examiner"]["id"]

        # 2. Check Examiner created with profile
        examiner = db.session.get(User, examiner_id)
        self.assertEqual(examiner.role, "examiner")
        self.assertIsNotNone(examiner.examiner_profile)
        self.assertEqual(examiner.examiner_profile.department, "Medicine")

        # 3. Create Exam and Assign Examiner
        course = Course(course_code="MED101", course_name="General Medicine")
        db.session.add(course)
        db.session.commit()
        exam = Examination(
            course_id=course.id,
            exam_name="Clinical Skills Assessment",
            duration_minutes=45,
            max_marks=100,
            slot_creation_start_date=datetime(2026, 11, 1),
            slot_creation_end_date=datetime(2026, 11, 5),
            slot_booking_start_date=datetime(2026, 11, 6),
            slot_booking_end_date=datetime(2026, 11, 10)
        )
        db.session.add(exam)
        db.session.commit()

        assign_res = self.client.post(f"/api/admin/examinations/{exam.id}/assign", headers=self.headers, json={
            "examiner_id": examiner_id
        })
        self.assertEqual(assign_res.status_code, 201)

        # Duplicate assignment rejected
        dup_assign = self.client.post(f"/api/admin/examinations/{exam.id}/assign", headers=self.headers, json={
            "examiner_id": examiner_id
        })
        self.assertEqual(dup_assign.status_code, 409)

        # 4. Unassign Examiner
        un_res = self.client.delete(f"/api/admin/examinations/{exam.id}/unassign/{examiner_id}", headers=self.headers)
        self.assertEqual(un_res.status_code, 200)

    def test_09_reschedule_student_booking_transactional(self):
        """Test Admin rescheduling a student's booking to another slot atomically."""
        course = Course(course_code="ENG101", course_name="Software Engineering")
        examiner = User(email="prof_eng@emp.local", full_name="Prof. Eng", role="examiner")
        examiner.set_password("Prof@123")
        db.session.add_all([course, examiner])
        db.session.commit()

        exam = Examination(
            course_id=course.id,
            exam_name="SE Viva",
            duration_minutes=30,
            max_marks=50,
            slot_creation_start_date=datetime(2026, 11, 1),
            slot_creation_end_date=datetime(2026, 11, 5),
            slot_booking_start_date=datetime(2026, 11, 6),
            slot_booking_end_date=datetime(2026, 11, 10)
        )
        db.session.add(exam)
        db.session.commit()

        # Slot 1 (initial slot): max 2, 1 available (1 booked)
        slot1 = ExaminationSlot(
            exam_id=exam.id,
            examiner_id=examiner.id,
            exam_date=date(2026, 11, 20),
            start_time=time(10, 0),
            end_time=time(10, 30),
            max_capacity=2,
            available_seats=1,
            status="Available"
        )
        # Slot 2 (target slot): max 2, 2 available
        slot2 = ExaminationSlot(
            exam_id=exam.id,
            examiner_id=examiner.id,
            exam_date=date(2026, 11, 20),
            start_time=time(11, 0),
            end_time=time(11, 30),
            max_capacity=2,
            available_seats=2,
            status="Available"
        )
        db.session.add_all([slot1, slot2])
        db.session.commit()

        booking = Booking(student_id=self.student.id, slot_id=slot1.id, exam_id=exam.id, status="Booked")
        db.session.add(booking)
        db.session.commit()

        # Reschedule from slot1 to slot2
        resched_res = self.client.post(
            f"/api/admin/bookings/{booking.id}/reschedule",
            headers=self.headers,
            json={"new_slot_id": slot2.id}
        )
        self.assertEqual(resched_res.status_code, 200)

        # Refresh objects
        db.session.refresh(slot1)
        db.session.refresh(slot2)
        db.session.refresh(booking)

        # Old slot available seats should have incremented from 1 to 2
        self.assertEqual(slot1.available_seats, 2)
        # New slot available seats should have decremented from 2 to 1
        self.assertEqual(slot2.available_seats, 1)
        # Booking slot_id should point to slot2
        self.assertEqual(booking.slot_id, slot2.id)

    def test_10_reassign_slot_examiner(self):
        """Test Admin changing the assigned examiner on an existing slot."""
        course = Course(course_code="HIST101", course_name="World History")
        ex1 = User(email="ex1@emp.local", full_name="Historian Alpha", role="examiner")
        ex1.set_password("Pass@123")
        ex2 = User(email="ex2@emp.local", full_name="Historian Beta", role="examiner")
        ex2.set_password("Pass@123")
        db.session.add_all([course, ex1, ex2])
        db.session.commit()

        exam = Examination(
            course_id=course.id,
            exam_name="History Oral Exam",
            duration_minutes=30,
            max_marks=50,
            slot_creation_start_date=datetime(2026, 11, 1),
            slot_creation_end_date=datetime(2026, 11, 5),
            slot_booking_start_date=datetime(2026, 11, 6),
            slot_booking_end_date=datetime(2026, 11, 10)
        )
        db.session.add(exam)
        db.session.commit()

        slot = ExaminationSlot(
            exam_id=exam.id,
            examiner_id=ex1.id,
            exam_date=date(2026, 11, 21),
            start_time=time(14, 0),
            end_time=time(14, 30),
            max_capacity=5,
            available_seats=5,
            status="Available"
        )
        db.session.add(slot)
        db.session.commit()

        # Reassign to ex2
        re_res = self.client.post(
            f"/api/admin/slots/{slot.id}/reassign",
            headers=self.headers,
            json={"new_examiner_id": ex2.id}
        )
        self.assertEqual(re_res.status_code, 200)

        db.session.refresh(slot)
        self.assertEqual(slot.examiner_id, ex2.id)

    def test_11_search_and_historical_stats(self):
        """Test search endpoints and historical reporting stats."""
        # Historical stats endpoint
        stat_res = self.client.get("/api/admin/reports/stats")
        self.assertEqual(stat_res.status_code, 200)
        data = stat_res.get_json()
        self.assertIn("historical_stats", data)
        self.assertIn("examiner_activity", data)

        # Search students
        stu_search = self.client.get("/api/admin/students?search=One")
        self.assertEqual(stu_search.status_code, 200)
        self.assertEqual(len(stu_search.get_json()["students"]), 1)


if __name__ == "__main__":
    unittest.main()

