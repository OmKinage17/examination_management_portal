from datetime import datetime, timezone
from backend.extensions import db


def get_utc_now():
    return datetime.now(timezone.utc)


class Course(db.Model):
    """
    Course model representing academic courses under which examinations are conducted.
    """
    __tablename__ = "courses"

    id = db.Column(db.Integer, primary_key=True)
    course_code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    course_name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="active")  # 'active', 'inactive'
    created_at = db.Column(db.DateTime, default=get_utc_now)

    # 1-to-many relationship with Examination
    examinations = db.relationship(
        "Examination",
        back_populates="course",
        cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "course_code": self.course_code,
            "course_name": self.course_name,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
