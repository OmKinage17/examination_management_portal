"""
Backend routes package.
"""
from backend.routes.auth import auth_bp, role_required
from backend.routes.admin import admin_bp
from backend.routes.examiner import examiner_bp
from backend.routes.student import student_bp

__all__ = ["auth_bp", "role_required", "admin_bp", "examiner_bp", "student_bp"]
