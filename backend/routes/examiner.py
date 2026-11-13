from datetime import datetime, date, time
from flask import Blueprint, request, jsonify, session
from sqlalchemy import or_, and_

from backend.extensions import db
from backend.models import (
    User,
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

examiner_bp = Blueprint("examiner", __name__, url_prefix="/api/examiner")


def parse_date(date_str):
    """Parse YYYY-MM-DD string into a date object."""
    if not date_str:
        return None
    return datetime.strptime(date_str.strip(), "%Y-%m-%d").date()


def parse_time(time_str):
    """Parse HH:MM string into a time object."""
    if not time_str:
        return None
    cleaned = time_str.strip()[:5]  # in case HH:MM:SS is passed
    return datetime.strptime(cleaned, "%H:%M").time()


# -------------------------------------------------------------------------
# 1. Examiner Dashboard Statistics
# -------------------------------------------------------------------------
@examiner_bp.route("/dashboard", methods=["GET"])
@role_required("examiner")
def get_dashboard():
    """
    Returns basic count statistics for the logged-in examiner's dashboard:
    - Assigned examinations
    - Total slots created
    - Total booked students
    - Pending evaluations
    """
    examiner_id = session.get("user_id")

    # 1. Assigned examinations count
    assigned_count = ExamAssignment.query.filter_by(examiner_id=examiner_id).count()

    # 2. Total slots created
    slots = ExaminationSlot.query.filter_by(examiner_id=examiner_id).all()
    slots_count = len(slots)
    slot_ids = [s.id for s in slots]

    # 3. Total booked students
    total_booked_students = 0
    pending_evaluations = 0

    if slot_ids:
        # Bookings on this examiner's slots
        bookings = Booking.query.filter(
            Booking.slot_id.in_(slot_ids),
            Booking.status.in_(["Booked", "Completed"])
        ).all()
        total_booked_students = len(bookings)

        # Pending evaluations: active or completed bookings without an Evaluation
        for b in bookings:
            if not b.evaluation:
                pending_evaluations += 1

    return jsonify({
        "stats": {
            "assigned_examinations": assigned_count,
            "total_slots": slots_count,
            "booked_students": total_booked_students,
            "pending_evaluations": pending_evaluations
        }
    }), 200


# -------------------------------------------------------------------------
# 2. Assigned Examinations
# -------------------------------------------------------------------------
@examiner_bp.route("/examinations", methods=["GET"])
@role_required("examiner")
def list_assigned_examinations():
    """
    Returns only the examinations assigned to the currently logged-in examiner.
    Prevents access to examinations assigned to other examiners.
    """
    examiner_id = session.get("user_id")

    assignments = ExamAssignment.query.filter_by(examiner_id=examiner_id).all()
    exams_data = []

    now = datetime.now()

    for a in assignments:
        exam = a.examination
        if exam:
            d = exam.to_dict()
            # Determine if slot creation is currently open
            creation_open = (exam.status == "Slot Creation") or (
                exam.slot_creation_start_date <= now <= exam.slot_creation_end_date
                and exam.status not in ("Closed", "Completed", "Draft")
            )
            d["is_slot_creation_open"] = bool(creation_open)
            d["rubrics_count"] = len(exam.rubrics)
            d["my_slots_count"] = ExaminationSlot.query.filter_by(exam_id=exam.id, examiner_id=examiner_id).count()
            exams_data.append(d)

    return jsonify({"examinations": exams_data}), 200


# -------------------------------------------------------------------------
# 3. Examination Slot Management
# -------------------------------------------------------------------------
@examiner_bp.route("/slots", methods=["GET"])
@role_required("examiner")
def list_my_slots():
    """
    List slots created by the currently logged-in examiner.
    Optional query parameter: ?exam_id=...
    """
    examiner_id = session.get("user_id")
    exam_id = request.args.get("exam_id", type=int)

    query = ExaminationSlot.query.filter_by(examiner_id=examiner_id)
    if exam_id:
        query = query.filter_by(exam_id=exam_id)

    slots = query.order_by(ExaminationSlot.exam_date.desc(), ExaminationSlot.start_time.asc()).all()
    results = []

    for s in slots:
        d = s.to_dict()
        d["active_bookings_count"] = len([b for b in s.bookings if b.status == "Booked"])
        d["can_edit_or_delete"] = (
            len(s.bookings) == 0
            and datetime.now() < s.examination.slot_booking_start_date
            and s.examination.status != "Booking Open"
        )
        results.append(d)

    return jsonify({"slots": results}), 200


@examiner_bp.route("/slots", methods=["POST"])
@role_required("examiner")
def create_slot():
    """
    Create an examination slot.
    Validates:
    - Examiner must be assigned to the examination.
    - Slot creation must occur within allowed period.
    - Start time earlier than end time.
    - Capacity > 0.
    - Prevent conflicting/overlapping slots for this examiner.
    """
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    examiner_id = session.get("user_id")
    data = request.get_json() or {}

    exam_id = data.get("exam_id")
    date_str = data.get("exam_date")
    start_str = data.get("start_time")
    end_str = data.get("end_time")
    capacity = data.get("max_capacity")

    if not exam_id or not date_str or not start_str or not end_str or capacity is None:
        return jsonify({"error": "Exam ID, Date, Start Time, End Time, and Capacity are required."}), 400

    # 1. Verify examiner is assigned to this exam
    assignment = ExamAssignment.query.filter_by(exam_id=exam_id, examiner_id=examiner_id).first()
    if not assignment:
        return jsonify({"error": "You are not assigned to conduct this examination."}), 403

    exam = assignment.examination

    # 2. Check slot creation timeline window
    now = datetime.now()
    is_window_open = (exam.status == "Slot Creation") or (
        exam.slot_creation_start_date <= now <= exam.slot_creation_end_date
        and exam.status not in ("Closed", "Completed", "Draft")
    )
    if not is_window_open:
        return jsonify({
            "error": "Slot creation is currently closed for this examination. "
                     f"Allowed period: {exam.slot_creation_start_date} to {exam.slot_creation_end_date}."
        }), 400

    # 3. Parse date and times
    try:
        parsed_date = parse_date(date_str)
        parsed_start = parse_time(start_str)
        parsed_end = parse_time(end_str)
        capacity = int(capacity)
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid format for date, time, or capacity: {e}"}), 400

    if capacity <= 0:
        return jsonify({"error": "Capacity must be greater than zero."}), 400

    if parsed_start >= parsed_end:
        return jsonify({"error": "Start Time must be earlier than End Time."}), 400

    # 4. Prevent conflicting/overlapping slots for this examiner on the same date
    conflict = ExaminationSlot.query.filter(
        ExaminationSlot.examiner_id == examiner_id,
        ExaminationSlot.exam_date == parsed_date,
        ExaminationSlot.status != "Cancelled",
        ExaminationSlot.start_time < parsed_end,
        ExaminationSlot.end_time > parsed_start
    ).first()

    if conflict:
        return jsonify({
            "error": f"You already have a conflicting slot on {parsed_date} "
                     f"from {conflict.start_time.strftime('%H:%M')} to {conflict.end_time.strftime('%H:%M')}."
        }), 409

    slot = ExaminationSlot(
        exam_id=exam_id,
        examiner_id=examiner_id,
        exam_date=parsed_date,
        start_time=parsed_start,
        end_time=parsed_end,
        max_capacity=capacity,
        available_seats=capacity,
        status="Available"
    )
    db.session.add(slot)
    db.session.commit()

    return jsonify({"message": "Examination slot created successfully.", "slot": slot.to_dict()}), 201


@examiner_bp.route("/slots/<int:slot_id>", methods=["PUT"])
@role_required("examiner")
def update_slot(slot_id):
    """
    Edit slot before student booking begins.
    """
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    examiner_id = session.get("user_id")
    slot = db.session.get(ExaminationSlot, slot_id)
    if not slot:
        return jsonify({"error": "Slot not found."}), 404

    if slot.examiner_id != examiner_id:
        return jsonify({"error": "You do not have permission to modify this slot."}), 403

    # Check if student booking has begun or bookings exist
    now = datetime.now()
    if slot.bookings or now >= slot.examination.slot_booking_start_date or slot.examination.status == "Booking Open":
        return jsonify({"error": "Cannot edit slot: Student booking has already begun or bookings exist."}), 400

    data = request.get_json() or {}
    date_str = data.get("exam_date")
    start_str = data.get("start_time")
    end_str = data.get("end_time")
    capacity = data.get("max_capacity")

    if date_str:
        slot.exam_date = parse_date(date_str)
    if start_str and end_str:
        p_start = parse_time(start_str)
        p_end = parse_time(end_str)
        if p_start >= p_end:
            return jsonify({"error": "Start Time must be earlier than End Time."}), 400
        slot.start_time = p_start
        slot.end_time = p_end
    if capacity is not None:
        capacity = int(capacity)
        if capacity <= 0:
            return jsonify({"error": "Capacity must be greater than zero."}), 400
        slot.max_capacity = capacity
        slot.available_seats = capacity

    db.session.commit()
    return jsonify({"message": "Slot updated successfully.", "slot": slot.to_dict()}), 200


@examiner_bp.route("/slots/<int:slot_id>", methods=["DELETE"])
@role_required("examiner")
def delete_slot(slot_id):
    """
    Delete slot before booking begins. Prevented if active bookings exist.
    """
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    examiner_id = session.get("user_id")
    slot = db.session.get(ExaminationSlot, slot_id)
    if not slot:
        return jsonify({"error": "Slot not found."}), 404

    if slot.examiner_id != examiner_id:
        return jsonify({"error": "You do not have permission to delete this slot."}), 403

    # Check if bookings exist
    active_bookings = len([b for b in slot.bookings if b.status == "Booked"])
    if active_bookings > 0:
        return jsonify({"error": f"Cannot delete slot: There are {active_bookings} booked student(s)."}), 400

    now = datetime.now()
    if now >= slot.examination.slot_booking_start_date or slot.examination.status == "Booking Open":
        return jsonify({"error": "Cannot delete slot: Student booking period has already started."}), 400

    db.session.delete(slot)
    db.session.commit()
    return jsonify({"message": "Slot deleted successfully."}), 200


# -------------------------------------------------------------------------
# 4. Booked Students
# -------------------------------------------------------------------------
@examiner_bp.route("/booked-students", methods=["GET"])
@role_required("examiner")
def list_booked_students():
    """
    View students booked into this examiner's assigned slots.
    Display student name, examination name, slot date/time, booking status, evaluation status.
    """
    examiner_id = session.get("user_id")
    exam_id = request.args.get("exam_id", type=int)
    slot_id = request.args.get("slot_id", type=int)

    # Fetch slots for this examiner
    query = ExaminationSlot.query.filter_by(examiner_id=examiner_id)
    if exam_id:
        query = query.filter_by(exam_id=exam_id)
    if slot_id:
        query = query.filter_by(id=slot_id)

    slots = query.all()
    slot_ids = [s.id for s in slots]

    if not slot_ids:
        return jsonify({"booked_students": []}), 200

    bookings = Booking.query.filter(
        Booking.slot_id.in_(slot_ids)
    ).order_by(Booking.booking_date.asc()).all()

    results = []
    for b in bookings:
        eval_data = b.evaluation.to_dict() if b.evaluation else None
        results.append({
            "booking_id": b.id,
            "student_id": b.student_id,
            "student_name": b.student.full_name if b.student else None,
            "student_email": b.student.email if b.student else None,
            "exam_id": b.exam_id,
            "exam_name": b.examination.exam_name if b.examination else None,
            "course_code": b.examination.course.course_code if b.examination and b.examination.course else None,
            "slot_id": b.slot_id,
            "exam_date": b.slot.exam_date.isoformat() if b.slot else None,
            "slot_time": f"{b.slot.start_time.strftime('%H:%M')} - {b.slot.end_time.strftime('%H:%M')}" if b.slot else None,
            "booking_status": b.status,
            "is_evaluated": b.evaluation is not None,
            "evaluation": eval_data
        })

    return jsonify({"booked_students": results}), 200


# -------------------------------------------------------------------------
# 5. Student Evaluation
# -------------------------------------------------------------------------
@examiner_bp.route("/bookings/<int:booking_id>/rubrics", methods=["GET"])
@role_required("examiner")
def get_rubrics_for_booking(booking_id):
    """
    Returns the examination rubrics for a specific student booking.
    Verifies that the examiner is assigned to conduct this slot.
    """
    examiner_id = session.get("user_id")
    booking = db.session.get(Booking, booking_id)
    if not booking:
        return jsonify({"error": "Booking not found."}), 404

    if booking.slot.examiner_id != examiner_id:
        return jsonify({"error": "You are not authorized to evaluate this student."}), 403

    rubrics = Rubric.query.filter_by(exam_id=booking.exam_id).all()
    existing_eval = booking.evaluation.to_dict() if booking.evaluation else None

    return jsonify({
        "booking_id": booking.id,
        "student_name": booking.student.full_name,
        "exam_name": booking.examination.exam_name,
        "max_marks": booking.examination.max_marks,
        "rubrics": [r.to_dict() for r in rubrics],
        "existing_evaluation": existing_eval
    }), 200


@examiner_bp.route("/bookings/<int:booking_id>/evaluate", methods=["POST"])
@role_required("examiner")
def submit_evaluation(booking_id):
    """
    Submit evaluation marks and remarks for a booked student.
    - Validates examiner owns the slot.
    - Validates marks per rubric criterion (0 <= marks <= max_marks).
    - Calculates total marks as simple sum.
    - Prevents evaluating cancelled bookings.
    - Locks evaluation if slot is already completed.
    """
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    examiner_id = session.get("user_id")
    booking = db.session.get(Booking, booking_id)
    if not booking:
        return jsonify({"error": "Booking not found."}), 404

    # 1. Scoping check: Examiner can evaluate ONLY students booked into their slots
    if booking.slot.examiner_id != examiner_id:
        return jsonify({"error": "Forbidden: You can evaluate only students booked into your assigned slots."}), 403

    # 2. Cannot evaluate a cancelled booking
    if booking.status == "Cancelled":
        return jsonify({"error": "Cannot evaluate a cancelled booking."}), 400

    # 3. Check if slot is already marked completed
    if booking.slot.status == "Completed":
        return jsonify({"error": "Cannot modify evaluation: The examination slot has already been completed."}), 400

    data = request.get_json() or {}
    scores_data = data.get("rubric_scores") or []
    remarks = (data.get("remarks") or "").strip()

    if not scores_data:
        return jsonify({"error": "Rubric scores are required."}), 400

    # 4. Validate each rubric criterion
    total_marks = 0.0
    rubric_score_records = []

    for item in scores_data:
        rubric_id = item.get("rubric_id")
        marks = item.get("marks_obtained")

        if rubric_id is None or marks is None:
            return jsonify({"error": "Each score must include rubric_id and marks_obtained."}), 400

        try:
            marks = float(marks)
        except (ValueError, TypeError):
            return jsonify({"error": "Marks must be numeric."}), 400

        rubric = db.session.get(Rubric, rubric_id)
        if not rubric or rubric.exam_id != booking.exam_id:
            return jsonify({"error": f"Rubric ID {rubric_id} is invalid for this examination."}), 400

        if marks < 0:
            return jsonify({"error": f"Marks for '{rubric.criterion_name}' cannot be negative."}), 400

        if marks > rubric.max_marks:
            return jsonify({
                "error": f"Marks for '{rubric.criterion_name}' ({marks}) exceed maximum allowed ({rubric.max_marks})."
            }), 400

        total_marks += marks
        rubric_score_records.append((rubric_id, marks))

    # Verify total marks <= exam.max_marks
    if total_marks > booking.examination.max_marks:
        return jsonify({
            "error": f"Total marks ({total_marks}) exceed examination maximum ({booking.examination.max_marks})."
        }), 400

    # 5. Save or update evaluation
    evaluation = booking.evaluation
    if not evaluation:
        # Create new evaluation
        evaluation = Evaluation(
            booking_id=booking.id,
            student_id=booking.student_id,
            examiner_id=examiner_id,
            total_marks=total_marks,
            remarks=remarks,
            status="Evaluated"
        )
        db.session.add(evaluation)
        db.session.flush()
    else:
        # Update existing evaluation
        evaluation.total_marks = total_marks
        evaluation.remarks = remarks
        evaluation.evaluation_date = datetime.now()
        evaluation.status = "Evaluated"
        # Clear existing individual scores
        EvaluationScore.query.filter_by(evaluation_id=evaluation.id).delete()

    # Add normalized individual rubric scores
    for r_id, m in rubric_score_records:
        score_entry = EvaluationScore(
            evaluation_id=evaluation.id,
            rubric_id=r_id,
            marks_obtained=m
        )
        db.session.add(score_entry)

    # Mark booking completed once evaluated
    booking.status = "Completed"
    db.session.commit()

    return jsonify({
        "message": "Evaluation submitted successfully.",
        "evaluation": evaluation.to_dict()
    }), 200


# -------------------------------------------------------------------------
# 6. Slot Completion
# -------------------------------------------------------------------------
@examiner_bp.route("/slots/<int:slot_id>/complete", methods=["POST"])
@role_required("examiner")
def complete_slot(slot_id):
    """
    Mark an examination slot as Completed.
    Prevents completion if any booked student has a pending evaluation.
    """
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    examiner_id = session.get("user_id")
    slot = db.session.get(ExaminationSlot, slot_id)
    if not slot:
        return jsonify({"error": "Slot not found."}), 404

    if slot.examiner_id != examiner_id:
        return jsonify({"error": "You do not have permission to complete this slot."}), 403

    # Check for pending evaluations among booked students
    active_bookings = [b for b in slot.bookings if b.status in ("Booked", "Completed")]
    pending = [b for b in active_bookings if not b.evaluation]

    if pending:
        return jsonify({
            "error": f"Cannot complete slot: {len(pending)} student(s) still have pending evaluations."
        }), 400

    slot.status = "Completed"
    db.session.commit()

    return jsonify({"message": "Examination slot marked as Completed.", "slot": slot.to_dict()}), 200

