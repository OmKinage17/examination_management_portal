import os
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from sqlalchemy.engine import Engine
from sqlalchemy import event

from backend.config import Config
from backend.extensions import db, server_session
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
from backend.routes.auth import auth_bp
from backend.routes.admin import admin_bp
from backend.routes.examiner import examiner_bp
from backend.routes.student import student_bp


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """
    Enforce foreign key constraints in SQLite.
    By default, SQLite does not enforce foreign keys unless PRAGMA foreign_keys=ON is executed.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_app(config_class=Config):
    """
    Application factory pattern.
    Configures Flask, database, sessions, blueprints, and static frontend serving.
    """
    frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
    app = Flask(__name__, static_folder=frontend_dir, static_url_path="")
    app.config.from_object(config_class)

    # Initialize CORS so frontend (or dev proxies) can communicate with backend API
    CORS(app, supports_credentials=True)

    # Initialize extensions
    db.init_app(app)
    server_session.init_app(app)

    # Register Blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(examiner_bp)
    app.register_blueprint(student_bp)

    # Health check route
    @app.route("/api/health", methods=["GET"])
    def health_check():
        return jsonify({
            "status": "healthy",
            "message": "Examination Management Portal V2 API is running"
        }), 200

    # Serve the Vue.js Single Page Application entry point
    @app.route("/")
    def serve_frontend():
        return send_from_directory(frontend_dir, "index.html")

    return app


def init_database(app):
    """
    Programmatically creates all database tables and seeds the default Admin.
    Admin credentials are read strictly from environment variables via app.config.
    """
    with app.app_context():
        # Programmatically create all database tables
        db.create_all()

        # Check if an admin already exists
        admin_user = User.query.filter_by(role="admin").first()
        if not admin_user:
            admin_email = app.config.get("ADMIN_EMAIL", "admin@emp.local")
            admin_password = app.config.get("ADMIN_PASSWORD", "Admin@123")

            admin = User(
                email=admin_email,
                full_name="Portal Administrator",
                role="admin"
            )
            # Securely hash password using Werkzeug
            admin.set_password(admin_password)

            db.session.add(admin)
            db.session.commit()
            print(f"[Database Init] Created default Admin: {admin_email}")
        else:
            print("[Database Init] Admin user already exists.")


# Create the standard app instance
app = create_app()

if __name__ == "__main__":
    init_database(app)
    app.run(debug=True, host="127.0.0.1", port=5000)
