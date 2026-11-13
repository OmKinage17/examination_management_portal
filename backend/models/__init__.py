"""
Database models package.
Exposes all SQLAlchemy models for easy import throughout the application.
"""
from backend.models.user import User, ExaminerProfile
from backend.models.course import Course
from backend.models.examination import Examination, ExamAssignment, Rubric
from backend.models.slot import ExaminationSlot
from backend.models.booking import Booking
from backend.models.evaluation import Evaluation, EvaluationScore

__all__ = [
    "User",
    "ExaminerProfile",
    "Course",
    "Examination",
    "ExamAssignment",
    "Rubric",
    "ExaminationSlot",
    "Booking",
    "Evaluation",
    "EvaluationScore",
]

