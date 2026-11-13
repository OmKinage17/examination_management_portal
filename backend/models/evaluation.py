from datetime import datetime, timezone
from backend.extensions import db


def get_utc_now():
    return datetime.now(timezone.utc)


class Evaluation(db.Model):
    """
    Evaluation record created by an examiner for a booked student.
    Contains overall marks, remarks, and links to detailed rubric scores.
    """
    __tablename__ = "evaluations"

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id", ondelete="CASCADE"), unique=True, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    examiner_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    total_marks = db.Column(db.Float, nullable=False)
    remarks = db.Column(db.Text, nullable=True)
    evaluation_date = db.Column(db.DateTime, default=get_utc_now, nullable=False)
    status = db.Column(db.String(20), default="Evaluated", nullable=False)

    # Relationships
    booking = db.relationship("Booking", back_populates="evaluation")
    student = db.relationship("User", back_populates="student_evaluations", foreign_keys=[student_id])
    examiner = db.relationship("User", back_populates="conducted_evaluations", foreign_keys=[examiner_id])

    # 1-to-many relationship with EvaluationScore
    rubric_scores = db.relationship(
        "EvaluationScore",
        back_populates="evaluation",
        cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "booking_id": self.booking_id,
            "student_id": self.student_id,
            "student_name": self.student.full_name if self.student else None,
            "examiner_id": self.examiner_id,
            "examiner_name": self.examiner.full_name if self.examiner else None,
            "total_marks": self.total_marks,
            "remarks": self.remarks,
            "evaluation_date": self.evaluation_date.isoformat() if self.evaluation_date else None,
            "status": self.status,
            "rubric_scores": [score.to_dict() for score in self.rubric_scores]
        }


class EvaluationScore(db.Model):
    """
    Individual rubric criterion score for a student's evaluation.
    Keeps rubric marks normalized and distinct.
    """
    __tablename__ = "evaluation_scores"

    id = db.Column(db.Integer, primary_key=True)
    evaluation_id = db.Column(db.Integer, db.ForeignKey("evaluations.id", ondelete="CASCADE"), nullable=False)
    rubric_id = db.Column(db.Integer, db.ForeignKey("rubrics.id", ondelete="CASCADE"), nullable=False)
    marks_obtained = db.Column(db.Float, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("evaluation_id", "rubric_id", name="uq_evaluation_rubric"),
    )

    # Relationships
    evaluation = db.relationship("Evaluation", back_populates="rubric_scores")
    rubric = db.relationship("Rubric", back_populates="scores")

    def to_dict(self):
        return {
            "id": self.id,
            "evaluation_id": self.evaluation_id,
            "rubric_id": self.rubric_id,
            "criterion_name": self.rubric.criterion_name if self.rubric else None,
            "max_marks": self.rubric.max_marks if self.rubric else None,
            "marks_obtained": self.marks_obtained
        }
