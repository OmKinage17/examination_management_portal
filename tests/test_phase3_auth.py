"""
Phase 3 Authentication and Role-Based Access Control Tests.
Tests student registration, multi-role login, session persistence, role decorators, and CSRF protection.
"""
import os
import unittest

os.environ["ADMIN_EMAIL"] = "admin@emp.local"
os.environ["ADMIN_PASSWORD"] = "Admin@123"

from backend.app import create_app, init_database
from backend.extensions import db
from backend.models import User, ExaminerProfile
from backend.routes.auth import role_required, verify_csrf
from flask import jsonify, request


class Phase3AuthTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        cls.app.config["ADMIN_EMAIL"] = "admin@emp.local"
        cls.app.config["ADMIN_PASSWORD"] = "Admin@123"

        # Add temporary protected test endpoints to verify role_required decorator
        @cls.app.route("/api/test/admin-only", methods=["GET"])
        @role_required("admin")
        def admin_only():
            return jsonify({"message": "Welcome Admin"}), 200

        @cls.app.route("/api/test/examiner-only", methods=["GET"])
        @role_required("examiner")
        def examiner_only():
            return jsonify({"message": "Welcome Examiner"}), 200

        @cls.app.route("/api/test/student-only", methods=["GET"])
        @role_required("student")
        def student_only():
            return jsonify({"message": "Welcome Student"}), 200

        @cls.app.route("/api/test/protected-post", methods=["POST"])
        @role_required("student")
        def protected_post():
            if not verify_csrf():
                return jsonify({"error": "CSRF token missing or invalid."}), 403
            return jsonify({"message": "Post action successful"}), 200

        cls.ctx = cls.app.app_context()
        cls.ctx.push()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.pop()

    def setUp(self):
        db.create_all()
        # Initialize default admin
        init_database(self.app)

        # Seed a test examiner
        examiner = User(
            email="examiner1@emp.local",
            full_name="Dr. Alan Turing",
            role="examiner"
        )
        examiner.set_password("Examiner@123")
        db.session.add(examiner)
        db.session.commit()

        # Seed examiner profile
        profile = ExaminerProfile(
            user_id=examiner.id,
            department="Computer Science",
            contact_details="9876543210"
        )
        db.session.add(profile)
        db.session.commit()

        # Use test client with cookie support
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()

    def test_01_successful_student_registration(self):
        """Test successful registration of a student."""
        payload = {
            "email": "student1@emp.local",
            "password": "Password@123",
            "full_name": "Ada Lovelace"
        }
        res = self.client.post("/api/auth/register", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertIn("user", data)
        self.assertEqual(data["user"]["email"], "student1@emp.local")
        self.assertEqual(data["user"]["role"], "student")
        self.assertNotIn("password_hash", data["user"])

        # Check DB
        user = User.query.filter_by(email="student1@emp.local").first()
        self.assertIsNotNone(user)
        self.assertTrue(user.check_password("Password@123"))

    def test_02_duplicate_email_registration(self):
        """Test that registering with an existing email returns 409 Conflict."""
        payload = {
            "email": "duplicate@emp.local",
            "password": "Password@123",
            "full_name": "First User"
        }
        res1 = self.client.post("/api/auth/register", json=payload)
        self.assertEqual(res1.status_code, 201)

        # Attempt to register again with same email
        res2 = self.client.post("/api/auth/register", json=payload)
        self.assertEqual(res2.status_code, 409)
        self.assertIn("already exists", res2.get_json()["error"])

    def test_03_login_all_roles(self):
        """Test successful login for Admin, Examiner, and Student roles."""
        # 1. Admin Login
        admin_res = self.client.post("/api/auth/login", json={
            "email": "admin@emp.local",
            "password": "Admin@123"
        })
        self.assertEqual(admin_res.status_code, 200)
        admin_data = admin_res.get_json()
        self.assertEqual(admin_data["user"]["role"], "admin")
        self.assertIn("csrf_token", admin_data)

        # 2. Examiner Login
        examiner_res = self.client.post("/api/auth/login", json={
            "email": "examiner1@emp.local",
            "password": "Examiner@123"
        })
        self.assertEqual(examiner_res.status_code, 200)
        examiner_data = examiner_res.get_json()
        self.assertEqual(examiner_data["user"]["role"], "examiner")

        # 3. Student Registration + Login
        self.client.post("/api/auth/register", json={
            "email": "stu_login@emp.local",
            "password": "Student@123",
            "full_name": "Bob Student"
        })
        stu_res = self.client.post("/api/auth/login", json={
            "email": "stu_login@emp.local",
            "password": "Student@123"
        })
        self.assertEqual(stu_res.status_code, 200)
        stu_data = stu_res.get_json()
        self.assertEqual(stu_data["user"]["role"], "student")

    def test_04_incorrect_password(self):
        """Test that incorrect credentials return 401 Unauthorized."""
        res = self.client.post("/api/auth/login", json={
            "email": "admin@emp.local",
            "password": "WrongPassword999"
        })
        self.assertEqual(res.status_code, 401)
        self.assertIn("Invalid email or password", res.get_json()["error"])

    def test_05_logout(self):
        """Test logout clears the server-side session."""
        login_res = self.client.post("/api/auth/login", json={
            "email": "admin@emp.local",
            "password": "Admin@123"
        })
        csrf_token = login_res.get_json()["csrf_token"]

        logout_res = self.client.post(
            "/api/auth/logout",
            headers={"X-CSRF-Token": csrf_token}
        )
        self.assertEqual(logout_res.status_code, 200)
        self.assertIn("Logged out successfully", logout_res.get_json()["message"])

        # Check /api/auth/me after logout -> should be 401
        me_res = self.client.get("/api/auth/me")
        self.assertEqual(me_res.status_code, 401)

    def test_06_access_without_login(self):
        """Test accessing role-protected endpoints without logging in returns 401."""
        res = self.client.get("/api/test/admin-only")
        self.assertEqual(res.status_code, 401)
        self.assertIn("Authentication required", res.get_json()["error"])

    def test_07_unauthorized_role_access(self):
        """Test that a logged-in user cannot access an unauthorized role's endpoints (HTTP 403)."""
        # Register and login as Student
        self.client.post("/api/auth/register", json={
            "email": "student_role@emp.local",
            "password": "Student@123",
            "full_name": "Role Test Student"
        })
        self.client.post("/api/auth/login", json={
            "email": "student_role@emp.local",
            "password": "Student@123"
        })

        # Student trying to access Admin-only endpoint -> 403 Forbidden
        admin_res = self.client.get("/api/test/admin-only")
        self.assertEqual(admin_res.status_code, 403)
        self.assertIn("Forbidden", admin_res.get_json()["error"])

        # Student trying to access Examiner-only endpoint -> 403 Forbidden
        examiner_res = self.client.get("/api/test/examiner-only")
        self.assertEqual(examiner_res.status_code, 403)

        # Student accessing Student-only endpoint -> 200 OK
        student_res = self.client.get("/api/test/student-only")
        self.assertEqual(student_res.status_code, 200)

    def test_08_session_persistence(self):
        """Test that session persists across multiple HTTP requests."""
        login_res = self.client.post("/api/auth/login", json={
            "email": "examiner1@emp.local",
            "password": "Examiner@123"
        })
        self.assertEqual(login_res.status_code, 200)

        # Request 1: /api/auth/me
        me_res = self.client.get("/api/auth/me")
        self.assertEqual(me_res.status_code, 200)
        self.assertEqual(me_res.get_json()["user"]["email"], "examiner1@emp.local")

        # Request 2: /api/test/examiner-only
        test_res = self.client.get("/api/test/examiner-only")
        self.assertEqual(test_res.status_code, 200)

    def test_09_session_invalidation_after_logout(self):
        """Test that after logout, subsequent protected requests are rejected."""
        login_res = self.client.post("/api/auth/login", json={
            "email": "admin@emp.local",
            "password": "Admin@123"
        })
        csrf_token = login_res.get_json()["csrf_token"]

        # Access succeeds
        self.assertEqual(self.client.get("/api/test/admin-only").status_code, 200)

        # Logout
        self.client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token})

        # Access now fails with 401
        self.assertEqual(self.client.get("/api/test/admin-only").status_code, 401)

    def test_10_prevention_of_admin_examiner_self_registration(self):
        """Test that passing role='admin' or role='examiner' during registration is ignored/overridden to 'student'."""
        payload = {
            "email": "hacker@emp.local",
            "password": "Hacker@123",
            "full_name": "Fake Admin",
            "role": "admin"  # Attempt to elevate privileges
        }
        res = self.client.post("/api/auth/register", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertEqual(data["user"]["role"], "student", "Role must be forced to 'student' by backend")

        # Verify in database directly
        user = User.query.filter_by(email="hacker@emp.local").first()
        self.assertEqual(user.role, "student")

    def test_11_csrf_rejection_for_invalid_requests(self):
        """Test that state-changing requests with missing or invalid CSRF tokens are rejected (HTTP 403)."""
        # Register and login as Student
        self.client.post("/api/auth/register", json={
            "email": "csrf_student@emp.local",
            "password": "Student@123",
            "full_name": "CSRF Student"
        })
        login_res = self.client.post("/api/auth/login", json={
            "email": "csrf_student@emp.local",
            "password": "Student@123"
        })
        valid_csrf_token = login_res.get_json()["csrf_token"]

        # Case A: Missing CSRF token -> 403
        res_missing = self.client.post("/api/test/protected-post")
        self.assertEqual(res_missing.status_code, 403)
        self.assertIn("CSRF", res_missing.get_json()["error"])

        # Case B: Invalid / Forged CSRF token -> 403
        res_invalid = self.client.post(
            "/api/test/protected-post",
            headers={"X-CSRF-Token": "forged_malicious_token_12345"}
        )
        self.assertEqual(res_invalid.status_code, 403)

        # Case C: Valid CSRF token -> 200 OK
        res_valid = self.client.post(
            "/api/test/protected-post",
            headers={"X-CSRF-Token": valid_csrf_token}
        )
        self.assertEqual(res_valid.status_code, 200)


if __name__ == "__main__":
    unittest.main()
