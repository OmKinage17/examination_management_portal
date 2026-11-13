from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from backend.extensions import db


def get_utc_now():
    """Helper returning current UTC datetime."""
    return datetime.now(timezone.utc)


class User(db.Model):
    """
    Unified User model representing Admin, Examiner, and Student roles.
    """
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'admin', 'examiner', 'student'
    created_at = db.Column(db.DateTime, default=get_utc_now)

    # 1-to-1 relationship with ExaminerProfile (for examiners)
    examiner_profile = db.relationship(
        "ExaminerProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )

    # Relationships for examinations assigned (if examiner)
    assigned_exams = db.relationship(
        "ExamAssignment",
        back_populates="examiner",
        cascade="all, delete-orphan",
        foreign_keys="ExamAssignment.examiner_id"
    )

    # Relationships for slots created (if examiner)
    created_slots = db.relationship(
        "ExaminationSlot",
        back_populates="examiner",
        cascade="all, delete-orphan",
        foreign_keys="ExaminationSlot.examiner_id"
    )

    # Relationships for bookings made (if student)
    bookings = db.relationship(
        "Booking",
        back_populates="student",
        cascade="all, delete-orphan",
        foreign_keys="Booking.student_id"
    )

    # Relationships for evaluations received (if student)
    student_evaluations = db.relationship(
        "Evaluation",
        back_populates="student",
        cascade="all, delete-orphan",
        foreign_keys="Evaluation.student_id"
    )

    # Relationships for evaluations conducted (if examiner)
    conducted_evaluations = db.relationship(
        "Evaluation",
        back_populates="examiner",
        foreign_keys="Evaluation.examiner_id"
    )

    def set_password(self, password):
        """Hash and set user password using Werkzeug."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verify candidate password against stored hash."""
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        """Convert user instance to dictionary."""
        data = {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
        if self.role == "examiner" and self.examiner_profile:
            data["examiner_profile"] = self.examiner_profile.to_dict()
        return data


class ExaminerProfile(db.Model):
    """
    Examiner Profile model storing academic and contact details for examiners.
    """
    __tablename__ = "examiner_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    department = db.Column(db.String(100), nullable=True)
    contact_details = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), default="active")  # 'active', 'inactive'

    # Back relationship to User
    user = db.relationship("User", back_populates="examiner_profile")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "department": self.department,
            "contact_details": self.contact_details,
            "status": self.status
        }
