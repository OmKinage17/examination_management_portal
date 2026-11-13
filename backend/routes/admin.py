import re
from datetime import datetime, date, time
from flask import Blueprint, request, jsonify
from sqlalchemy import or_, func

from backend.extensions import db
from backend.cache import invalidate_exam_cache
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
from backend.routes.auth import role_required, verify_csrf

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def parse_datetime(dt_str):
    """Parse ISO or standard date/time string into a naive datetime object."""
    if not dt_str:
        return None
    cleaned = dt_str.replace("Z", "").strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid date/time format: '{dt_str}'. Expected YYYY-MM-DD or YYYY-MM-DDTHH:MM.")


# -------------------------------------------------------------------------
# 1. Dashboard Statistics
# -------------------------------------------------------------------------
@admin_bp.route("/dashboard", methods=["GET"])
@role_required("admin")
def get_dashboard_stats():
    """
    Returns basic count statistics and recent activities for the Admin dashboard.
    """
    total_courses = Course.query.count()
    total_examinations = Examination.query.count()
    total_students = User.query.filter_by(role="student").count()
    total_examiners = User.query.filter_by(role="examiner").count()
    total_slots = ExaminationSlot.query.count()
    total_bookings = Booking.query.count()

    recent_exams = Examination.query.order_by(Examination.created_at.desc()).limit(5).all()
    recent_bookings = Booking.query.order_by(Booking.booking_date.desc()).limit(5).all()

    return jsonify({
        "stats": {
            "total_courses": total_courses,
            "total_examinations": total_examinations,
            "total_students": total_students,
            "total_examiners": total_examiners,
            "total_slots": total_slots,
            "total_bookings": total_bookings
        },
        "recent_examinations": [e.to_dict() for e in recent_exams],
        "recent_bookings": [b.to_dict() for b in recent_bookings]
    }), 200


# -------------------------------------------------------------------------
# 2. Course CRUD
# -------------------------------------------------------------------------
@admin_bp.route("/courses", methods=["GET"])
@role_required("admin")
def list_courses():
    """List courses with optional search filter."""
    search = request.args.get("search", "").strip()
    query = Course.query
    if search:
        query = query.filter(
            or_(
                Course.course_code.ilike(f"%{search}%"),
                Course.course_name.ilike(f"%{search}%")
            )
        )
    courses = query.order_by(Course.course_code.asc()).all()
    return jsonify({"courses": [c.to_dict() for c in courses]}), 200


@admin_bp.route("/courses", methods=["POST"])
@role_required("admin")
def create_course():
    """Add a new course."""
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    data = request.get_json() or {}
    code = (data.get("course_code") or "").strip().upper()
    name = (data.get("course_name") or "").strip()
    desc = (data.get("description") or "").strip()
    status = (data.get("status") or "active").strip().lower()

    if not code or not name:
        return jsonify({"error": "Course Code and Course Name are required."}), 400

    if Course.query.filter_by(course_code=code).first():
        return jsonify({"error": f"Course code '{code}' already exists."}), 409

    course = Course(course_code=code, course_name=name, description=desc, status=status)
    db.session.add(course)
    db.session.commit()
    invalidate_exam_cache()

    return jsonify({"message": "Course created successfully.", "course": course.to_dict()}), 201


@admin_bp.route("/courses/<int:course_id>", methods=["GET"])
@role_required("admin")
def get_course(course_id):
    """View details of a single course."""
    course = db.session.get(Course, course_id)
    if not course:
        return jsonify({"error": "Course not found."}), 404
    data = course.to_dict()
    data["examinations"] = [e.to_dict() for e in course.examinations]
    return jsonify({"course": data}), 200


@admin_bp.route("/courses/<int:course_id>", methods=["PUT"])
@role_required("admin")
def update_course(course_id):
    """Edit an existing course."""
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    course = db.session.get(Course, course_id)
    if not course:
        return jsonify({"error": "Course not found."}), 404

    data = request.get_json() or {}
    code = (data.get("course_code") or "").strip().upper()
    name = (data.get("course_name") or "").strip()
    desc = (data.get("description") or "").strip()
    status = (data.get("status") or course.status).strip().lower()

    if not code or not name:
        return jsonify({"error": "Course Code and Course Name are required."}), 400

    # If code changed, check uniqueness
    if code != course.course_code:
        if Course.query.filter_by(course_code=code).first():
            return jsonify({"error": f"Course code '{code}' already exists."}), 409

    course.course_code = code
    course.course_name = name
    course.description = desc
    course.status = status
    db.session.commit()
    invalidate_exam_cache()

    return jsonify({"message": "Course updated successfully.", "course": course.to_dict()}), 200


@admin_bp.route("/courses/<int:course_id>", methods=["DELETE"])
@role_required("admin")
def delete_course(course_id):
    """Delete a course. Protects relationship integrity."""
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    course = db.session.get(Course, course_id)
    if not course:
        return jsonify({"error": "Course not found."}), 404

    if course.examinations:
        return jsonify({
            "error": "Cannot delete course: One or more examinations are attached to it. Please remove or reassign examinations first."
        }), 400

    db.session.delete(course)
    db.session.commit()
    invalidate_exam_cache()
    return jsonify({"message": f"Course '{course.course_code}' deleted successfully."}), 200


# -------------------------------------------------------------------------
# 3. Examination CRUD & Timeline Validation
# -------------------------------------------------------------------------
@admin_bp.route("/examinations", methods=["GET"])
@role_required("admin")
def list_examinations():
    """List examinations with search, course, and status filters."""
    search = request.args.get("search", "").strip()
    course_id = request.args.get("course_id", type=int)
    status = request.args.get("status", "").strip()

    query = Examination.query
    if search:
        query = query.filter(Examination.exam_name.ilike(f"%{search}%"))
    if course_id:
        query = query.filter(Examination.course_id == course_id)
    if status:
        query = query.filter(Examination.status == status)

    exams = query.order_by(Examination.created_at.desc()).all()
    results = []
    for e in exams:
        d = e.to_dict()
        d["assigned_examiners_count"] = len(e.assigned_examiners)
        d["slots_count"] = len(e.slots)
        d["rubrics_count"] = len(e.rubrics)
        results.append(d)

    return jsonify({"examinations": results}), 200


@admin_bp.route("/examinations", methods=["POST"])
@role_required("admin")
def create_examination():
    """Create a new examination with strict timeline validation."""
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    data = request.get_json() or {}
    course_id = data.get("course_id")
    name = (data.get("exam_name") or "").strip()
    desc = (data.get("description") or "").strip()
    duration = data.get("duration_minutes")
    max_marks = data.get("max_marks")

    if not course_id or not name or duration is None or max_marks is None:
        return jsonify({"error": "Course ID, Exam Name, Duration, and Max Marks are required."}), 400

    course = db.session.get(Course, course_id)
    if not course:
        return jsonify({"error": "Selected Course does not exist."}), 404

    try:
        duration = int(duration)
        max_marks = float(max_marks)
        if duration <= 0 or max_marks <= 0:
            return jsonify({"error": "Duration and Maximum Marks must be positive numbers."}), 400
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid numerical values for duration or maximum marks."}), 400

    # Parse timelines
    try:
        sc_start = parse_datetime(data.get("slot_creation_start_date"))
        sc_end = parse_datetime(data.get("slot_creation_end_date"))
        sb_start = parse_datetime(data.get("slot_booking_start_date"))
        sb_end = parse_datetime(data.get("slot_booking_end_date"))
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400

    if not (sc_start and sc_end and sb_start and sb_end):
        return jsonify({"error": "All 4 timeline dates are required."}), 400

    # Validate timeline rules
    if sc_start >= sc_end:
        return jsonify({"error": "Slot Creation Start Date must be earlier than Slot Creation End Date."}), 400
    if sb_start >= sb_end:
        return jsonify({"error": "Slot Booking Start Date must be earlier than Slot Booking End Date."}), 400
    if sc_start > sb_start:
        return jsonify({"error": "Slot Creation Start Date must not be after Booking Start Date."}), 400

    status = (data.get("status") or "Draft").strip()
    valid_statuses = {"Draft", "Slot Creation", "Booking Open", "Closed", "Completed"}
    if status not in valid_statuses:
        status = "Draft"

    exam = Examination(
        course_id=course_id,
        exam_name=name,
        description=desc,
        duration_minutes=duration,
        max_marks=max_marks,
        slot_creation_start_date=sc_start,
        slot_creation_end_date=sc_end,
        slot_booking_start_date=sb_start,
        slot_booking_end_date=sb_end,
        status=status
    )
    db.session.add(exam)
    db.session.commit()
    invalidate_exam_cache()

    return jsonify({"message": "Examination created successfully.", "examination": exam.to_dict()}), 201


@admin_bp.route("/examinations/<int:exam_id>", methods=["GET"])
@role_required("admin")
def get_examination(exam_id):
    """View detailed examination info including assigned examiners, rubrics, and slots."""
    exam = db.session.get(Examination, exam_id)
    if not exam:
        return jsonify({"error": "Examination not found."}), 404

    data = exam.to_dict()
    data["assigned_examiners"] = [a.to_dict() for a in exam.assigned_examiners]
    data["rubrics"] = [r.to_dict() for r in exam.rubrics]
    data["slots"] = [s.to_dict() for s in exam.slots]
    return jsonify({"examination": data}), 200


@admin_bp.route("/examinations/<int:exam_id>", methods=["PUT"])
@role_required("admin")
def update_examination(exam_id):
    """Update examination details and timelines."""
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    exam = db.session.get(Examination, exam_id)
    if not exam:
        return jsonify({"error": "Examination not found."}), 404

    data = request.get_json() or {}
    name = (data.get("exam_name") or exam.exam_name).strip()
    desc = data.get("description", exam.description)
    duration = data.get("duration_minutes", exam.duration_minutes)
    max_marks = data.get("max_marks", exam.max_marks)

    try:
        duration = int(duration)
        max_marks = float(max_marks)
        if duration <= 0 or max_marks <= 0:
            return jsonify({"error": "Duration and Maximum Marks must be positive."}), 400
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid numerical values."}), 400

    # Timelines
    try:
        sc_start = parse_datetime(data.get("slot_creation_start_date")) or exam.slot_creation_start_date
        sc_end = parse_datetime(data.get("slot_creation_end_date")) or exam.slot_creation_end_date
        sb_start = parse_datetime(data.get("slot_booking_start_date")) or exam.slot_booking_start_date
        sb_end = parse_datetime(data.get("slot_booking_end_date")) or exam.slot_booking_end_date
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400

    if sc_start >= sc_end:
        return jsonify({"error": "Slot Creation Start Date must be earlier than Slot Creation End Date."}), 400
    if sb_start >= sb_end:
        return jsonify({"error": "Slot Booking Start Date must be earlier than Slot Booking End Date."}), 400
    if sc_start > sb_start:
        return jsonify({"error": "Slot Creation Start Date must not be after Booking Start Date."}), 400

    exam.exam_name = name
    exam.description = desc
    exam.duration_minutes = duration
    exam.max_marks = max_marks
    exam.slot_creation_start_date = sc_start
    exam.slot_creation_end_date = sc_end
    exam.slot_booking_start_date = sb_start
    exam.slot_booking_end_date = sb_end

    if "status" in data:
        valid_statuses = {"Draft", "Slot Creation", "Booking Open", "Closed", "Completed"}
        if data["status"] in valid_statuses:
            exam.status = data["status"]

    db.session.commit()
    invalidate_exam_cache()
    return jsonify({"message": "Examination updated successfully.", "examination": exam.to_dict()}), 200


@admin_bp.route("/examinations/<int:exam_id>", methods=["DELETE"])
@role_required("admin")
def delete_examination(exam_id):
    """Delete an examination. Blocked if active student bookings exist."""
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    exam = db.session.get(Examination, exam_id)
    if not exam:
        return jsonify({"error": "Examination not found."}), 404

    active_bookings = Booking.query.filter_by(exam_id=exam_id, status="Booked").count()
    if active_bookings > 0:
        return jsonify({
            "error": f"Cannot delete examination: There are {active_bookings} active student bookings. Please cancel or complete bookings first."
        }), 400

    db.session.delete(exam)
    db.session.commit()
    invalidate_exam_cache()
    return jsonify({"message": f"Examination '{exam.exam_name}' deleted successfully."}), 200


# -------------------------------------------------------------------------
# 4. Manual Window Overrides & Status Management
# -------------------------------------------------------------------------
@admin_bp.route("/examinations/<int:exam_id>/status", methods=["PUT"])
@role_required("admin")
def update_examination_status(exam_id):
    """
    Manually toggle or override examination lifecycle status.
    Allowed statuses: 'Draft', 'Slot Creation', 'Booking Open', 'Closed', 'Completed'.
    """
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    exam = db.session.get(Examination, exam_id)
    if not exam:
        return jsonify({"error": "Examination not found."}), 404

    data = request.get_json() or {}
    new_status = (data.get("status") or "").strip()
    valid_statuses = {"Draft", "Slot Creation", "Booking Open", "Closed", "Completed"}

    if new_status not in valid_statuses:
        return jsonify({"error": f"Invalid status '{new_status}'. Allowed: {sorted(list(valid_statuses))}"}), 400

    exam.status = new_status
    db.session.commit()
    invalidate_exam_cache()
    return jsonify({
        "message": f"Examination status manually updated to '{new_status}'.",
        "examination": exam.to_dict()
    }), 200


# -------------------------------------------------------------------------
# 5. Rubric CRUD
# -------------------------------------------------------------------------
@admin_bp.route("/examinations/<int:exam_id>/rubrics", methods=["GET"])
@role_required("admin")
def list_rubrics(exam_id):
    """List all rubrics for an examination."""
    exam = db.session.get(Examination, exam_id)
    if not exam:
        return jsonify({"error": "Examination not found."}), 404
    return jsonify({"rubrics": [r.to_dict() for r in exam.rubrics]}), 200


@admin_bp.route("/examinations/<int:exam_id>/rubrics", methods=["POST"])
@role_required("admin")
def add_rubric(exam_id):
    """Add a grading criterion to an examination."""
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    exam = db.session.get(Examination, exam_id)
    if not exam:
        return jsonify({"error": "Examination not found."}), 404

    data = request.get_json() or {}
    name = (data.get("criterion_name") or "").strip()
    desc = (data.get("description") or "").strip()
    max_marks = data.get("max_marks")

    if not name or max_marks is None:
        return jsonify({"error": "Criterion Name and Maximum Marks are required."}), 400

    try:
        max_marks = float(max_marks)
        if max_marks <= 0:
            return jsonify({"error": "Maximum Marks must be greater than zero."}), 400
    except (ValueError, TypeError):
        return jsonify({"error": "Maximum Marks must be a valid number."}), 400

    rubric = Rubric(exam_id=exam_id, criterion_name=name, max_marks=max_marks, description=desc)
    db.session.add(rubric)
    db.session.commit()

    return jsonify({"message": "Rubric criterion added successfully.", "rubric": rubric.to_dict()}), 201


@admin_bp.route("/rubrics/<int:rubric_id>", methods=["PUT"])
@role_required("admin")
def update_rubric(rubric_id):
    """Edit an existing rubric."""
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    rubric = db.session.get(Rubric, rubric_id)
    if not rubric:
        return jsonify({"error": "Rubric not found."}), 404

    data = request.get_json() or {}
    name = (data.get("criterion_name") or rubric.criterion_name).strip()
    desc = data.get("description", rubric.description)
    max_marks = data.get("max_marks", rubric.max_marks)

    try:
        max_marks = float(max_marks)
        if max_marks <= 0:
            return jsonify({"error": "Maximum Marks must be greater than zero."}), 400
    except (ValueError, TypeError):
        return jsonify({"error": "Maximum Marks must be a valid number."}), 400

    rubric.criterion_name = name
    rubric.description = desc
    rubric.max_marks = max_marks
    db.session.commit()

    return jsonify({"message": "Rubric updated successfully.", "rubric": rubric.to_dict()}), 200


@admin_bp.route("/rubrics/<int:rubric_id>", methods=["DELETE"])
@role_required("admin")
def delete_rubric(rubric_id):
    """Delete a rubric. Prevented if already used in evaluations."""
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    rubric = db.session.get(Rubric, rubric_id)
    if not rubric:
        return jsonify({"error": "Rubric not found."}), 404

    used_count = EvaluationScore.query.filter_by(rubric_id=rubric_id).count()
    if used_count > 0:
        return jsonify({
            "error": "Cannot delete rubric: It has already been used in completed evaluations."
        }), 400

    db.session.delete(rubric)
    db.session.commit()
    return jsonify({"message": "Rubric deleted successfully."}), 200


# -------------------------------------------------------------------------
# 6. Examiner Management & Assignment
# -------------------------------------------------------------------------
@admin_bp.route("/examiners", methods=["GET"])
@role_required("admin")
def list_examiners():
    """List all examiners with optional search filter."""
    search = request.args.get("search", "").strip()
    query = User.query.filter_by(role="examiner")
    if search:
        query = query.filter(
            or_(
                User.full_name.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%")
            )
        )
    examiners = query.order_by(User.full_name.asc()).all()

    result = []
    for ex in examiners:
        d = ex.to_dict()
        d["assigned_exams"] = [a.examination.exam_name for a in ex.assigned_exams if a.examination]
        d["slots_count"] = len(ex.created_slots)
        result.append(d)

    return jsonify({"examiners": result}), 200


@admin_bp.route("/examiners", methods=["POST"])
@role_required("admin")
def create_examiner():
    """Admin creates a new examiner account."""
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    full_name = (data.get("full_name") or "").strip()
    department = (data.get("department") or "").strip()
    contact = (data.get("contact_details") or "").strip()

    if not email or not password or not full_name:
        return jsonify({"error": "Email, password, and full name are required."}), 400

    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"error": f"An account with email '{email}' already exists."}), 409

    examiner = User(
        email=email,
        full_name=full_name,
        role="examiner"
    )
    examiner.set_password(password)
    db.session.add(examiner)
    db.session.flush()  # populate examiner.id

    profile = ExaminerProfile(
        user_id=examiner.id,
        department=department,
        contact_details=contact,
        status="active"
    )
    db.session.add(profile)
    db.session.commit()

    return jsonify({
        "message": "Examiner account created successfully.",
        "examiner": examiner.to_dict()
    }), 201


@admin_bp.route("/examinations/<int:exam_id>/assign", methods=["POST"])
@role_required("admin")
def assign_examiner_to_exam(exam_id):
    """Assign an examiner to an examination. Prevents duplicates."""
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    exam = db.session.get(Examination, exam_id)
    if not exam:
        return jsonify({"error": "Examination not found."}), 404

    data = request.get_json() or {}
    examiner_id = data.get("examiner_id")
    if not examiner_id:
        return jsonify({"error": "examiner_id is required."}), 400

    examiner = db.session.get(User, examiner_id)
    if not examiner or examiner.role != "examiner":
        return jsonify({"error": "Target user is not a valid examiner."}), 404

    # Check duplicate assignment
    existing = ExamAssignment.query.filter_by(exam_id=exam_id, examiner_id=examiner_id).first()
    if existing:
        return jsonify({"error": "This examiner is already assigned to this examination."}), 409

    assignment = ExamAssignment(exam_id=exam_id, examiner_id=examiner_id)
    db.session.add(assignment)
    db.session.commit()

    return jsonify({"message": f"Assigned Dr./Prof. {examiner.full_name} to {exam.exam_name}."}), 201


@admin_bp.route("/examinations/<int:exam_id>/unassign/<int:examiner_id>", methods=["DELETE"])
@role_required("admin")
def unassign_examiner_from_exam(exam_id, examiner_id):
    """Unassign an examiner from an examination."""
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    assignment = ExamAssignment.query.filter_by(exam_id=exam_id, examiner_id=examiner_id).first()
    if not assignment:
        return jsonify({"error": "Assignment not found."}), 404

    # Check if examiner has active slots with bookings
    slots = ExaminationSlot.query.filter_by(exam_id=exam_id, examiner_id=examiner_id).all()
    slot_ids = [s.id for s in slots]
    if slot_ids:
        active_bookings = Booking.query.filter(Booking.slot_id.in_(slot_ids), Booking.status == "Booked").count()
        if active_bookings > 0:
            return jsonify({
                "error": f"Cannot unassign examiner: Examiner has active slots with {active_bookings} booked student(s). Reassign those slots first."
            }), 400

    db.session.delete(assignment)
    db.session.commit()
    return jsonify({"message": "Examiner unassigned successfully."}), 200


# -------------------------------------------------------------------------
# 7. Search & Oversight (Students, Slots, Bookings)
# -------------------------------------------------------------------------
@admin_bp.route("/students", methods=["GET"])
@role_required("admin")
def list_students():
    """List students with search filter."""
    search = request.args.get("search", "").strip()
    query = User.query.filter_by(role="student")
    if search:
        query = query.filter(
            or_(
                User.full_name.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%")
            )
        )
    students = query.order_by(User.full_name.asc()).all()
    results = []
    for s in students:
        d = s.to_dict()
        d["bookings_count"] = len(s.bookings)
        results.append(d)
    return jsonify({"students": results}), 200


@admin_bp.route("/slots", methods=["GET"])
@role_required("admin")
def list_slots():
    """View and filter all examination slots."""
    exam_id = request.args.get("exam_id", type=int)
    examiner_id = request.args.get("examiner_id", type=int)
    status = request.args.get("status", "").strip()

    query = ExaminationSlot.query
    if exam_id:
        query = query.filter(ExaminationSlot.exam_id == exam_id)
    if examiner_id:
        query = query.filter(ExaminationSlot.examiner_id == examiner_id)
    if status:
        query = query.filter(ExaminationSlot.status == status)

    slots = query.order_by(ExaminationSlot.exam_date.asc(), ExaminationSlot.start_time.asc()).all()
    results = []
    for s in slots:
        d = s.to_dict()
        d["booked_students_count"] = len([b for b in s.bookings if b.status == "Booked"])
        results.append(d)
    return jsonify({"slots": results}), 200


@admin_bp.route("/bookings", methods=["GET"])
@role_required("admin")
def list_bookings():
    """View and filter all student bookings."""
    exam_id = request.args.get("exam_id", type=int)
    student_id = request.args.get("student_id", type=int)
    status = request.args.get("status", "").strip()

    query = Booking.query
    if exam_id:
        query = query.filter(Booking.exam_id == exam_id)
    if student_id:
        query = query.filter(Booking.student_id == student_id)
    if status:
        query = query.filter(Booking.status == status)

    bookings = query.order_by(Booking.booking_date.desc()).all()
    return jsonify({"bookings": [b.to_dict() for b in bookings]}), 200


# -------------------------------------------------------------------------
# 8. Admin Rescheduling of Bookings & Examiner Reassignment
# -------------------------------------------------------------------------
@admin_bp.route("/bookings/<int:booking_id>/reschedule", methods=["POST"])
@role_required("admin")
def reschedule_booking(booking_id):
    """
    Reschedule a student's booking to another slot for the same examination.
    Uses atomic conditional updates to protect capacity and transactional integrity.
    """
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    booking = db.session.get(Booking, booking_id)
    if not booking:
        return jsonify({"error": "Booking not found."}), 404

    if booking.status != "Booked":
        return jsonify({"error": f"Cannot reschedule a booking with status '{booking.status}'."}), 400

    data = request.get_json() or {}
    new_slot_id = data.get("new_slot_id")
    if not new_slot_id:
        return jsonify({"error": "new_slot_id is required."}), 400

    if new_slot_id == booking.slot_id:
        return jsonify({"error": "Target slot is the same as the current slot."}), 400

    new_slot = db.session.get(ExaminationSlot, new_slot_id)
    if not new_slot:
        return jsonify({"error": "Target examination slot does not exist."}), 404

    # Must be for the same examination
    if new_slot.exam_id != booking.exam_id:
        return jsonify({"error": "Cannot reschedule booking to a slot belonging to a different examination."}), 400

    if new_slot.status not in ("Available", "Full") or new_slot.available_seats <= 0:
        return jsonify({"error": "Target slot is full or unavailable."}), 400

    old_slot = booking.slot

    # Atomic seat transfer in a single transaction
    old_slot.available_seats += 1
    if old_slot.status == "Full":
        old_slot.status = "Available"

    new_slot.available_seats -= 1
    if new_slot.available_seats == 0:
        new_slot.status = "Full"

    booking.slot_id = new_slot.id
    db.session.commit()

    return jsonify({
        "message": "Booking rescheduled successfully.",
        "booking": booking.to_dict()
    }), 200


@admin_bp.route("/slots/<int:slot_id>/reassign", methods=["POST"])
@role_required("admin")
def reassign_slot_examiner(slot_id):
    """
    Change the assigned examiner on an existing examination slot.
    """
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    slot = db.session.get(ExaminationSlot, slot_id)
    if not slot:
        return jsonify({"error": "Slot not found."}), 404

    data = request.get_json() or {}
    new_examiner_id = data.get("new_examiner_id")
    if not new_examiner_id:
        return jsonify({"error": "new_examiner_id is required."}), 400

    new_examiner = db.session.get(User, new_examiner_id)
    if not new_examiner or new_examiner.role != "examiner":
        return jsonify({"error": "New examiner not found or role is not examiner."}), 404

    # Ensure new examiner is assigned to this examination
    assignment = ExamAssignment.query.filter_by(exam_id=slot.exam_id, examiner_id=new_examiner_id).first()
    if not assignment:
        # Auto-assign or link to exam so consistency is maintained
        assignment = ExamAssignment(exam_id=slot.exam_id, examiner_id=new_examiner_id)
        db.session.add(assignment)

    slot.examiner_id = new_examiner_id
    db.session.commit()

    return jsonify({
        "message": f"Slot reassigned to Dr./Prof. {new_examiner.full_name}.",
        "slot": slot.to_dict()
    }), 200


# -------------------------------------------------------------------------
# 9. Historical Statistics & Reports
# -------------------------------------------------------------------------
@admin_bp.route("/reports/stats", methods=["GET"])
@role_required("admin")
def get_historical_reports():
    """
    Aggregates historical data and examination statistics.
    """
    exams_conducted = Examination.query.filter(Examination.status.in_(["Completed", "Closed"])).count()
    students_evaluated = Evaluation.query.count()
    completed_bookings = Booking.query.filter_by(status="Completed").count()
    active_bookings = Booking.query.filter_by(status="Booked").count()

    # Pending evaluations = Booked slots that don't have an evaluation record yet
    pending_evals = Booking.query.filter(
        Booking.status.in_(["Booked", "Completed"]),
        ~Booking.evaluation.has()
    ).count()

    # Most active examiners
    examiners = User.query.filter_by(role="examiner").all()
    examiner_activity = []
    for ex in examiners:
        eval_count = len(ex.conducted_evaluations)
        slots_count = len(ex.created_slots)
        examiner_activity.append({
            "examiner_id": ex.id,
            "full_name": ex.full_name,
            "email": ex.email,
            "slots_created": slots_count,
            "students_evaluated": eval_count
        })

    examiner_activity.sort(key=lambda x: x["students_evaluated"], reverse=True)

    return jsonify({
        "historical_stats": {
            "examinations_conducted": exams_conducted,
            "students_evaluated": students_evaluated,
            "completed_bookings": completed_bookings,
            "active_bookings": active_bookings,
            "pending_evaluations": pending_evals
        },
        "examiner_activity": examiner_activity
    }), 200

