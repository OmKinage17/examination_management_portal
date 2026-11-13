import re
import secrets
from functools import wraps
from flask import Blueprint, request, session, jsonify, current_app
from backend.extensions import db
from backend.models import User

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def role_required(*allowed_roles):
    """
    Decorator to restrict access based on authentication status and user role.
    Returns 401 if not logged in.
    Returns 403 if user role is not in allowed_roles.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user_id = session.get("user_id")
            user_role = session.get("role")

            # 1. Check if user is logged in
            if not user_id:
                return jsonify({"error": "Authentication required. Please log in."}), 401

            # 2. Check if user role is authorized
            if allowed_roles and user_role not in allowed_roles:
                return jsonify({"error": "Forbidden: You do not have permission to access this resource."}), 403

            return fn(*args, **kwargs)
        return wrapper
    return decorator


def verify_csrf():
    """
    Validates CSRF token from request header 'X-CSRF-Token' against session token.
    State-changing requests (POST, PUT, DELETE) on session-authenticated routes must pass this check.
    """
    expected_token = session.get("csrf_token")
    provided_token = request.headers.get("X-CSRF-Token")

    if not expected_token or not provided_token or not secrets.compare_digest(expected_token, provided_token):
        return False
    return True


@auth_bp.route("/csrf-token", methods=["GET"])
def get_csrf_token():
    """
    Returns or initializes the CSRF token for the current session.
    """
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return jsonify({"csrf_token": session["csrf_token"]}), 200


@auth_bp.route("/register", methods=["POST"])
def register():
    """
    Student registration endpoint.
    Only students can register via public endpoint.
    Admin and Examiner registration is strictly prohibited here.
    """
    data = request.get_json() or {}

    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    full_name = (data.get("full_name") or "").strip()

    # Validation: Required fields
    if not email or not password or not full_name:
        return jsonify({"error": "All fields (email, password, full_name) are required."}), 400

    # Validation: Basic email format
    email_regex = r"^[^@]+@[^@]+\.[^@]+$"
    if not re.match(email_regex, email):
        return jsonify({"error": "Invalid email address format."}), 400

    # Validation: Password minimum length
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters long."}), 400

    # Check for duplicate email
    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        return jsonify({"error": "An account with this email already exists."}), 409

    # Enforce student role on the backend (ignore any role sent from frontend)
    student_user = User(
        email=email,
        full_name=full_name,
        role="student"
    )
    student_user.set_password(password)

    db.session.add(student_user)
    db.session.commit()

    return jsonify({
        "message": "Student registered successfully.",
        "user": student_user.to_dict()
    }), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    """
    Common login endpoint for Admin, Examiner, and Student roles.
    Verifies credentials and creates a secure server-side session.
    """
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    user = User.query.filter_by(email=email).first()

    # Verify user and password hash
    if not user or not user.check_password(password):
        return jsonify({"error": "Invalid email or password."}), 401

    # Prevent session fixation: regenerate session identifier on login
    if hasattr(current_app, "session_interface") and hasattr(current_app.session_interface, "regenerate"):
        current_app.session_interface.regenerate(session)
    session.clear()

    # Store only essential identifiers in server-side session
    session["user_id"] = user.id
    session["role"] = user.role

    # Generate a fresh CSRF token for this session
    csrf_token = secrets.token_hex(16)
    session["csrf_token"] = csrf_token

    return jsonify({
        "message": "Login successful.",
        "user": user.to_dict(),
        "csrf_token": csrf_token
    }), 200


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """
    Clears the server-side session and logs out the user.
    Protected with CSRF token check.
    """
    if "user_id" not in session:
        return jsonify({"error": "Authentication required. Please log in."}), 401

    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    # Clear server-side session
    session.clear()
    return jsonify({"message": "Logged out successfully."}), 200


@auth_bp.route("/me", methods=["GET"])
def get_current_user():
    """
    Returns details of the currently authenticated user and CSRF token.
    """
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"authenticated": False, "user": None}), 401

    user = db.session.get(User, user_id)
    if not user:
        session.clear()
        return jsonify({"authenticated": False, "user": None}), 401

    # Ensure CSRF token exists
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)

    return jsonify({
        "authenticated": True,
        "user": user.to_dict(),
        "csrf_token": session["csrf_token"]
    }), 200
