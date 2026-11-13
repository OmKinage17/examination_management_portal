"""
Phase 2 Database Initialization and Integrity Tests.
Tests programmatic table creation, Admin seeding, foreign keys, and constraints.
"""
import unittest
from datetime import datetime, date, time
from sqlalchemy.exc import IntegrityError

from backend.app import create_app, init_database
from backend.extensions import db
from backend.models import (
    User,
    ExaminerProfile,
    Course,
    Examination,
    ExamAssignment,
    Rubric,
    ExaminationSlot,
    Booking,
    Evaluation,
    EvaluationScore,
)


class Phase2DatabaseTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        cls.app.config["ADMIN_EMAIL"] = "testadmin@emp.local"
        cls.app.config["ADMIN_PASSWORD"] = "TestAdmin@123"
        cls.ctx = cls.app.app_context()
        cls.ctx.push()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.pop()

    def setUp(self):
        # Programmatically create all tables before each test
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()

    def test_01_all_tables_created(self):
        """Verify that all required entity tables exist in SQLite metadata."""
        inspector = db.inspect(db.engine)
        table_names = set(inspector.get_table_names())
        required_tables = {
            "users",
            "examiner_profiles",
            "courses",
            "examinations",
            "exam_assignments",
            "rubrics",
            "examination_slots",
            "bookings",
            "evaluations",
            "evaluation_scores",
        }
        self.assertTrue(required_tables.issubset(table_names), f"Missing tables: {required_tables - table_names}")

    def test_02_admin_programmatic_creation(self):
        """Verify default Admin account is created with securely hashed password."""
        init_database(self.app)

        admins = User.query.filter_by(role="admin").all()
        self.assertEqual(len(admins), 1, "There should be exactly one Admin user")

        admin = admins[0]
        self.assertEqual(admin.email, "testadmin@emp.local")
        self.assertTrue(admin.check_password("TestAdmin@123"))
        self.assertFalse(admin.check_password("WrongPassword"))
        self.assertNotEqual(admin.password_hash, "TestAdmin@123", "Password must be hashed, never plain text")

        # Test idempotency: running init_database again must not create duplicates
        init_database(self.app)
        self.assertEqual(User.query.filter_by(role="admin").count(), 1)

    def test_03_foreign_key_enforcement(self):
        """Verify that foreign keys are strictly enforced by SQLite."""
        # Inserting ExaminerProfile with non-existent user_id must fail
        orphan_profile = ExaminerProfile(user_id=9999, department="Computer Science")
        db.session.add(orphan_profile)
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_04_slot_capacity_check_constraint(self):
        """Verify that available_seats cannot be negative (preventing overbooking at DB level)."""
        # Create course, exam, and examiner
        course = Course(course_code="CS101", course_name="Introduction to Programming")
        examiner = User(email="prof@emp.local", full_name="Professor Smith", role="examiner")
        examiner.set_password("Prof@123")
        db.session.add_all([course, examiner])
        db.session.commit()

        exam = Examination(
            course_id=course.id,
            exam_name="Midterm Lab Exam",
            duration_minutes=60,
            max_marks=50.0,
            slot_creation_start_date=datetime(2026, 11, 1),
            slot_creation_end_date=datetime(2026, 11, 10),
            slot_booking_start_date=datetime(2026, 11, 11),
            slot_booking_end_date=datetime(2026, 11, 20),
            status="Booking Open",
        )
        db.session.add(exam)
        db.session.commit()

        # Slot with negative available seats must raise IntegrityError
        invalid_slot = ExaminationSlot(
            exam_id=exam.id,
            examiner_id=examiner.id,
            exam_date=date(2026, 11, 25),
            start_time=time(10, 0),
            end_time=time(11, 0),
            max_capacity=5,
            available_seats=-1,  # Violates CHECK(available_seats >= 0)
        )
        db.session.add(invalid_slot)
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_05_single_active_booking_per_student_per_exam(self):
        """Verify that a student cannot have more than one active ('Booked') slot for the same exam."""
        course = Course(course_code="CS102", course_name="Data Structures")
        examiner = User(email="prof2@emp.local", full_name="Professor Jones", role="examiner")
        examiner.set_password("Prof@123")
        student = User(email="student1@emp.local", full_name="Alice Student", role="student")
        student.set_password("Alice@123")
        db.session.add_all([course, examiner, student])
        db.session.commit()

        exam = Examination(
            course_id=course.id,
            exam_name="DSA Final Exam",
            duration_minutes=90,
            max_marks=100.0,
            slot_creation_start_date=datetime(2026, 11, 1),
            slot_creation_end_date=datetime(2026, 11, 10),
            slot_booking_start_date=datetime(2026, 11, 11),
            slot_booking_end_date=datetime(2026, 11, 20),
            status="Booking Open",
        )
        db.session.add(exam)
        db.session.commit()

        slot1 = ExaminationSlot(
            exam_id=exam.id,
            examiner_id=examiner.id,
            exam_date=date(2026, 11, 22),
            start_time=time(9, 0),
            end_time=time(10, 30),
            max_capacity=10,
            available_seats=10,
        )
        slot2 = ExaminationSlot(
            exam_id=exam.id,
            examiner_id=examiner.id,
            exam_date=date(2026, 11, 22),
            start_time=time(11, 0),
            end_time=time(12, 30),
            max_capacity=10,
            available_seats=10,
        )
        db.session.add_all([slot1, slot2])
        db.session.commit()

        # First booking succeeds
        booking1 = Booking(
            student_id=student.id,
            slot_id=slot1.id,
            exam_id=exam.id,
            status="Booked",
        )
        db.session.add(booking1)
        db.session.commit()

        # Attempting second active booking for same student & exam must fail via partial unique index
        booking2 = Booking(
            student_id=student.id,
            slot_id=slot2.id,
            exam_id=exam.id,
            status="Booked",
        )
        db.session.add(booking2)
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        # But if the first booking is Cancelled, a new Booked booking should be permitted
        booking1 = Booking.query.filter_by(student_id=student.id, exam_id=exam.id).first()
        booking1.status = "Cancelled"
        db.session.commit()

        # Now rebooking succeeds
        booking3 = Booking(
            student_id=student.id,
            slot_id=slot2.id,
            exam_id=exam.id,
            status="Booked",
        )
        db.session.add(booking3)
        db.session.commit()
        self.assertEqual(booking3.status, "Booked")

    def test_06_rubric_evaluation_score_normalization(self):
        """Verify rubric creation and evaluation score relationship."""
        course = Course(course_code="CS103", course_name="Web Systems")
        examiner = User(email="prof3@emp.local", full_name="Professor Lee", role="examiner")
        examiner.set_password("Prof@123")
        student = User(email="student2@emp.local", full_name="Bob Student", role="student")
        student.set_password("Bob@123")
        db.session.add_all([course, examiner, student])
        db.session.commit()

        exam = Examination(
            course_id=course.id,
            exam_name="Web Dev Viva",
            duration_minutes=30,
            max_marks=40.0,
            slot_creation_start_date=datetime(2026, 11, 1),
            slot_creation_end_date=datetime(2026, 11, 10),
            slot_booking_start_date=datetime(2026, 11, 11),
            slot_booking_end_date=datetime(2026, 11, 20),
            status="Completed",
        )
        db.session.add(exam)
        db.session.commit()

        # Rubrics
        rubric1 = Rubric(exam_id=exam.id, criterion_name="Code Explanation", max_marks=20.0)
        rubric2 = Rubric(exam_id=exam.id, criterion_name="Live Demo", max_marks=20.0)
        db.session.add_all([rubric1, rubric2])
        db.session.commit()

        slot = ExaminationSlot(
            exam_id=exam.id,
            examiner_id=examiner.id,
            exam_date=date(2026, 11, 23),
            start_time=time(14, 0),
            end_time=time(14, 30),
            max_capacity=1,
            available_seats=0,
            status="Full",
        )
        db.session.add(slot)
        db.session.commit()

        booking = Booking(student_id=student.id, slot_id=slot.id, exam_id=exam.id, status="Completed")
        db.session.add(booking)
        db.session.commit()

        # Evaluation
        eval_record = Evaluation(
            booking_id=booking.id,
            student_id=student.id,
            examiner_id=examiner.id,
            total_marks=35.0,
            remarks="Excellent understanding of frontend and backend integration.",
        )
        db.session.add(eval_record)
        db.session.commit()

        # Individual scores
        score1 = EvaluationScore(evaluation_id=eval_record.id, rubric_id=rubric1.id, marks_obtained=18.0)
        score2 = EvaluationScore(evaluation_id=eval_record.id, rubric_id=rubric2.id, marks_obtained=17.0)
        db.session.add_all([score1, score2])
        db.session.commit()

        self.assertEqual(eval_record.total_marks, score1.marks_obtained + score2.marks_obtained)
        self.assertEqual(len(eval_record.rubric_scores), 2)


if __name__ == "__main__":
    unittest.main()
