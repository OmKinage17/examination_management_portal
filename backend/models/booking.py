from datetime import datetime, timezone
from backend.extensions import db


def get_utc_now():
    return datetime.now(timezone.utc)


class Booking(db.Model):
    """
    Booking record representing a student's reservation for an examination slot.
    Enforces that a student can have at most one active ('Booked') slot per examination.
    """
    __tablename__ = "bookings"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey("examination_slots.id", ondelete="CASCADE"), nullable=False)
    exam_id = db.Column(db.Integer, db.ForeignKey("examinations.id", ondelete="CASCADE"), nullable=False)

    booking_date = db.Column(db.DateTime, default=get_utc_now, nullable=False)
    status = db.Column(db.String(20), default="Booked", nullable=False)  # 'Booked', 'Cancelled', 'Completed'

    __table_args__ = (
        # Partial unique index: A student can only have ONE active ('Booked') booking per examination.
        # Once cancelled or completed, a new booking would be allowed if within booking timelines.
        db.Index(
            "uq_student_active_booking",
            "student_id",
            "exam_id",
            unique=True,
            sqlite_where=(status == "Booked")
        ),
    )

    # Relationships
    student = db.relationship("User", back_populates="bookings", foreign_keys=[student_id])
    slot = db.relationship("ExaminationSlot", back_populates="bookings")
    examination = db.relationship("Examination", back_populates="bookings")

    evaluation = db.relationship(
        "Evaluation",
        back_populates="booking",
        uselist=False,
        cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "student_id": self.student_id,
            "student_name": self.student.full_name if self.student else None,
            "student_email": self.student.email if self.student else None,
            "slot_id": self.slot_id,
            "exam_id": self.exam_id,
            "exam_name": self.examination.exam_name if self.examination else None,
            "booking_date": self.booking_date.isoformat() if self.booking_date else None,
            "status": self.status,
            "slot": self.slot.to_dict() if self.slot else None
        }
