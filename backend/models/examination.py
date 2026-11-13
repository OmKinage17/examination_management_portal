from datetime import datetime, timezone
from backend.extensions import db


def get_utc_now():
    return datetime.now(timezone.utc)


class Examination(db.Model):
    """
    Examination model created by Admin for a particular course.
    """
    __tablename__ = "examinations"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    exam_name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    duration_minutes = db.Column(db.Integer, nullable=False)
    max_marks = db.Column(db.Float, nullable=False)

    # Examination Timelines
    slot_creation_start_date = db.Column(db.DateTime, nullable=False)
    slot_creation_end_date = db.Column(db.DateTime, nullable=False)
    slot_booking_start_date = db.Column(db.DateTime, nullable=False)
    slot_booking_end_date = db.Column(db.DateTime, nullable=False)

    # Examination Status: 'Draft', 'Slot Creation', 'Booking Open', 'Closed', 'Completed'
    status = db.Column(db.String(30), default="Draft", nullable=False)
    created_at = db.Column(db.DateTime, default=get_utc_now)

    # Relationships
    course = db.relationship("Course", back_populates="examinations")

    assigned_examiners = db.relationship(
        "ExamAssignment",
        back_populates="examination",
        cascade="all, delete-orphan"
    )

    rubrics = db.relationship(
        "Rubric",
        back_populates="examination",
        cascade="all, delete-orphan"
    )

    slots = db.relationship(
        "ExaminationSlot",
        back_populates="examination",
        cascade="all, delete-orphan"
    )

    bookings = db.relationship(
        "Booking",
        back_populates="examination",
        cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "course_id": self.course_id,
            "course_code": self.course.course_code if self.course else None,
            "course_name": self.course.course_name if self.course else None,
            "exam_name": self.exam_name,
            "description": self.description,
            "duration_minutes": self.duration_minutes,
            "max_marks": self.max_marks,
            "slot_creation_start_date": self.slot_creation_start_date.isoformat() if self.slot_creation_start_date else None,
            "slot_creation_end_date": self.slot_creation_end_date.isoformat() if self.slot_creation_end_date else None,
            "slot_booking_start_date": self.slot_booking_start_date.isoformat() if self.slot_booking_start_date else None,
            "slot_booking_end_date": self.slot_booking_end_date.isoformat() if self.slot_booking_end_date else None,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class ExamAssignment(db.Model):
    """
    Explicit assignment relationship linking examinations to examiners.
    Admin assigns one or more examiners to an examination.
    """
    __tablename__ = "exam_assignments"

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("examinations.id", ondelete="CASCADE"), nullable=False)
    examiner_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assigned_at = db.Column(db.DateTime, default=get_utc_now)

    __table_args__ = (
        db.UniqueConstraint("exam_id", "examiner_id", name="uq_exam_examiner"),
    )

    # Relationships
    examination = db.relationship("Examination", back_populates="assigned_examiners")
    examiner = db.relationship("User", back_populates="assigned_exams", foreign_keys=[examiner_id])

    def to_dict(self):
        return {
            "id": self.id,
            "exam_id": self.exam_id,
            "examiner_id": self.examiner_id,
            "examiner_name": self.examiner.full_name if self.examiner else None,
            "examiner_email": self.examiner.email if self.examiner else None,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None
        }


class Rubric(db.Model):
    """
    Evaluation criteria defined for an examination.
    """
    __tablename__ = "rubrics"

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("examinations.id", ondelete="CASCADE"), nullable=False)
    criterion_name = db.Column(db.String(100), nullable=False)
    max_marks = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text, nullable=True)

    # Relationships
    examination = db.relationship("Examination", back_populates="rubrics")
    scores = db.relationship("EvaluationScore", back_populates="rubric", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "exam_id": self.exam_id,
            "criterion_name": self.criterion_name,
            "max_marks": self.max_marks,
            "description": self.description
        }
