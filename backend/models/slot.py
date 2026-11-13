from datetime import datetime, timezone
from backend.extensions import db


def get_utc_now():
    return datetime.now(timezone.utc)


class ExaminationSlot(db.Model):
    """
    Examination Slot created by an assigned examiner for a specific examination.
    Includes database check constraints to strictly prevent overbooking.
    """
    __tablename__ = "examination_slots"

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("examinations.id", ondelete="CASCADE"), nullable=False)
    examiner_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    exam_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)

    max_capacity = db.Column(db.Integer, nullable=False)
    available_seats = db.Column(db.Integer, nullable=False)

    # Status: 'Available', 'Full', 'Cancelled', 'Completed'
    status = db.Column(db.String(20), default="Available", nullable=False)
    created_at = db.Column(db.DateTime, default=get_utc_now)

    __table_args__ = (
        db.CheckConstraint("available_seats >= 0", name="chk_available_seats_non_negative"),
        db.CheckConstraint("available_seats <= max_capacity", name="chk_available_seats_le_max"),
        db.CheckConstraint("max_capacity > 0", name="chk_max_capacity_positive"),
    )

    # Relationships
    examination = db.relationship("Examination", back_populates="slots")
    examiner = db.relationship("User", back_populates="created_slots", foreign_keys=[examiner_id])
    bookings = db.relationship("Booking", back_populates="slot", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "exam_id": self.exam_id,
            "exam_name": self.examination.exam_name if self.examination else None,
            "examiner_id": self.examiner_id,
            "examiner_name": self.examiner.full_name if self.examiner else None,
            "exam_date": self.exam_date.isoformat() if self.exam_date else None,
            "start_time": self.start_time.strftime("%H:%M") if self.start_time else None,
            "end_time": self.end_time.strftime("%H:%M") if self.end_time else None,
            "max_capacity": self.max_capacity,
            "available_seats": self.available_seats,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
