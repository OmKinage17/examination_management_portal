import os
from datetime import datetime, date
from flask import Blueprint, request, jsonify, session, current_app, send_from_directory
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from celery.result import AsyncResult

from backend.cache import cache_get, cache_set
from backend.extensions import db
from backend.models import (
    User,
    Course,
    Examination,
    Rubric,
    ExaminationSlot,
    Booking,
    Evaluation,
    EvaluationScore,
)
from backend.routes.auth import role_required, verify_csrf
from backend.tasks.celery_app import celery_app
from backend.tasks.jobs import export_student_csv

student_bp = Blueprint("student", __name__, url_prefix="/api/student")


def _is_booking_open(exam, now=None):
    """Booking requires both the configured time window and the Booking Open status."""
    now = now or datetime.now()
    return (
        exam.status == "Booking Open"
        and exam.slot_booking_start_date <= now <= exam.slot_booking_end_date
    )


# -------------------------------------------------------------------------
# 1. Student Dashboard Statistics
# -------------------------------------------------------------------------
@student_bp.route("/dashboard", methods=["GET"])
@role_required("student")
def get_student_dashboard():
    """
    Returns basic count statistics and upcoming exams for the logged-in student.
    """
    student_id = session.get("user_id")
    # 1. Total bookings placed by student
    all_bookings = Booking.query.filter_by(student_id=student_id).all()
    total_bookings = len(all_bookings)

    # 2. Upcoming booked examinations (status 'Booked' with slot date in future or today)
    upcoming_bookings = [
        b for b in all_bookings
        if b.status == "Booked" and b.slot and b.slot.exam_date >= date.today()
    ]

    # 3. Completed examinations
    completed_bookings = [b for b in all_bookings if b.status == "Completed"]
    total_completed = len(completed_bookings)

    # 4. Published results (completed and evaluated)
    published_results = [b for b in completed_bookings if b.evaluation]
    total_results = sum(
        1 for booking in published_results
        if booking.evaluation.status == "Evaluated"
    )

    # 5. Examinations with at least one bookable seat
    now = datetime.now()
    candidate_exams = Examination.query.filter(
        Examination.status == "Booking Open",
        Examination.slot_booking_start_date <= now,
        Examination.slot_booking_end_date >= now
    ).all()
    available_exams = sum(
        1 for exam in candidate_exams
        if _is_booking_open(exam, now)
        and any(slot.status == "Available" and slot.available_seats > 0 for slot in exam.slots)
    )

    return jsonify({
        "stats": {
            "available_examinations": available_exams,
            "upcoming_examinations": len(upcoming_bookings),
            "total_bookings": total_bookings,
            "completed_examinations": total_completed,
            "published_results": total_results
        },
        "upcoming_bookings": [b.to_dict() for b in upcoming_bookings[:5]]
    }), 200


# -------------------------------------------------------------------------
# 2. Examination Search & Available Examinations
# -------------------------------------------------------------------------
@student_bp.route("/examinations", methods=["GET"])
@role_required("student")
def search_examinations():
    """
    Allows students to view and search available examinations.
    Search by exam name or course code, filter by status.
    Uses Redis caching with explicit TTL for public examination listings,
    while keeping private student booking status computed per request.
    """
    student_id = session.get("user_id")
    search = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip()

    cache_key = f"public_exams:{search}:{status_filter}"
    cached_public_exams = cache_get(cache_key)

    if cached_public_exams is not None:
        public_exams = cached_public_exams
    else:
        now = datetime.now()
        query = Examination.query.join(Course)

        if search:
            query = query.filter(
                or_(
                    Examination.exam_name.ilike(f"%{search}%"),
                    Course.course_code.ilike(f"%{search}%"),
                    Course.course_name.ilike(f"%{search}%"),
                )
            )

        query = query.filter(Examination.status != "Draft")
        if status_filter:
            query = query.filter(Examination.status == status_filter)

        exams = query.order_by(Examination.slot_booking_start_date.asc()).all()
        public_exams = []

        for e in exams:
            d = e.to_dict()
            # Determine if booking is currently permitted by timeline and status
            d["is_booking_open"] = _is_booking_open(e, now)
            # Available seats across all active slots for this exam
            open_seats = sum(
                s.available_seats for s in e.slots
                if s.status == "Available" and s.available_seats > 0
            )
            d["total_available_seats"] = open_seats
            public_exams.append(d)

        # Store public metadata in Redis with explicit TTL (default 60s)
        timeout = current_app.config.get("CACHE_DEFAULT_TIMEOUT", 60)
        cache_set(cache_key, public_exams, timeout=timeout)

    # Decorate with student's individual booking status (private data never stored in public cache)
    results = []
    for item in public_exams:
        d = dict(item)
        prior_booking = (
            Booking.query.filter_by(student_id=student_id, exam_id=d["id"])
            .order_by(Booking.id.desc())
            .first()
        )
        d["student_booking_status"] = prior_booking.status if prior_booking else "None"
        d["has_active_booking"] = prior_booking.status == "Booked" if prior_booking else False
        d["has_completed_booking"] = prior_booking.status == "Completed" if prior_booking else False
        results.append(d)

    return jsonify({"examinations": results}), 200


# -------------------------------------------------------------------------
# 3. Available Slots for an Examination
# -------------------------------------------------------------------------
@student_bp.route("/examinations/<int:exam_id>/slots", methods=["GET"])
@role_required("student")
def get_available_slots(exam_id):
    """
    Returns available examination slots for a specific examination.
    Displays exam details, examiner name, schedule, and open seats.
    """
    exam = db.session.get(Examination, exam_id)
    if not exam:
        return jsonify({"error": "Examination not found."}), 404

    # Fetch slots for this examination that are not Cancelled
    slots = ExaminationSlot.query.filter(
        ExaminationSlot.exam_id == exam_id,
        ExaminationSlot.status.in_(["Available", "Full"])
    ).order_by(ExaminationSlot.exam_date.asc(), ExaminationSlot.start_time.asc()).all()

    now = datetime.now()
    booking_open = _is_booking_open(exam, now)
    student_id = session.get("user_id")
    prior_booking = Booking.query.filter_by(
        student_id=student_id,
        exam_id=exam.id
    ).order_by(Booking.id.desc()).first()

    slot_list = []
    for s in slots:
        slot_list.append({
            "id": s.id,
            "exam_id": s.exam_id,
            "exam_name": exam.exam_name,
            "course_code": exam.course.course_code if exam.course else None,
            "examiner_id": s.examiner_id,
            "examiner_name": s.examiner.full_name if s.examiner else None,
            "exam_date": s.exam_date.isoformat(),
            "start_time": s.start_time.strftime("%H:%M"),
            "end_time": s.end_time.strftime("%H:%M"),
            "max_capacity": s.max_capacity,
            "available_seats": s.available_seats,
            "status": s.status,
            "is_open_for_booking": booking_open and s.available_seats > 0
        })

    return jsonify({
        "examination": exam.to_dict(),
        "is_booking_open": booking_open,
        "student_booking_status": prior_booking.status if prior_booking else "None",
        "slots": slot_list
    }), 200


# -------------------------------------------------------------------------
# 4. Slot Booking (Atomic Transaction & Concurrency Safe)
# -------------------------------------------------------------------------
@student_bp.route("/book", methods=["POST"])
@role_required("student")
def book_slot():
    """
    Books an available examination slot for the logged-in student.
    Enforces:
    - Booking window validation.
    - Single active or completed booking per student per exam.
    - Atomic conditional SQL update to prevent race condition overbooking.
    - Automatic status update to 'Full' when capacity reaches zero.
    - Rollback on failure.
    """
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    student_id = session.get("user_id")
    data = request.get_json() or {}
    slot_id = data.get("slot_id")

    if not slot_id:
        return jsonify({"error": "slot_id is required."}), 400

    # 1. Fetch slot
    slot = db.session.get(ExaminationSlot, slot_id)
    if not slot:
        return jsonify({"error": "Examination slot not found."}), 404

    exam = slot.examination
    now = datetime.now()

    # 2. Validate booking timeline and examination status
    booking_open = _is_booking_open(exam, now)
    if not booking_open:
        return jsonify({
            "error": "Slot booking is not open for this examination. "
                     f"Booking window: {exam.slot_booking_start_date} to {exam.slot_booking_end_date}."
        }), 400

    # 3. Rule: Student cannot book more than one slot per examination
    # AND cannot book another slot if previously completed/evaluated
    prior_booking = Booking.query.filter(
        Booking.student_id == student_id,
        Booking.exam_id == exam.id,
        Booking.status.in_(["Booked", "Completed"])
    ).first()

    if prior_booking:
        if prior_booking.status == "Completed":
            return jsonify({
                "error": "You have already completed this examination and cannot book another slot."
            }), 400
        else:
            return jsonify({
                "error": "You already have an active booking for this examination."
            }), 400

    # 4. Check slot status and available capacity
    if slot.status != "Available" or slot.available_seats <= 0:
        return jsonify({"error": "This examination slot is full or unavailable."}), 400

    # 5. Atomic conditional SQL update: reduce available seats only if seats > 0
    # Prevents simultaneous requests from overbooking beyond capacity
    try:
        updated_rows = ExaminationSlot.query.filter(
            ExaminationSlot.id == slot.id,
            ExaminationSlot.available_seats > 0,
            ExaminationSlot.status == "Available"
        ).update({
            ExaminationSlot.available_seats: ExaminationSlot.available_seats - 1
        })

        if updated_rows == 0:
            db.session.rollback()
            return jsonify({"error": "Failed to book: Slot just became full or unavailable."}), 400

        # Reload slot to check updated capacity
        db.session.refresh(slot)
        if slot.available_seats == 0:
            slot.status = "Full"

        booking = Booking(
            student_id=student_id,
            slot_id=slot.id,
            exam_id=exam.id,
            status="Booked"
        )
        db.session.add(booking)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "You already have an active booking for this examination."}), 409
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Booking could not be completed because of a database error."}), 500

    return jsonify({
        "message": "Slot booked successfully!",
        "booking": booking.to_dict()
    }), 201


# -------------------------------------------------------------------------
# 5. Booking Cancellation
# -------------------------------------------------------------------------
@student_bp.route("/bookings/<int:booking_id>/cancel", methods=["POST"])
@role_required("student")
def cancel_booking(booking_id):
    """
    Cancels an active booking before the permitted booking deadline.
    Restores available capacity and updates slot status.
    """
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    student_id = session.get("user_id")
    booking = db.session.get(Booking, booking_id)
    if not booking:
        return jsonify({"error": "Booking not found."}), 404

    # 1. Ownership check: Must belong to logged-in student
    if booking.student_id != student_id:
        return jsonify({"error": "You do not have permission to cancel this booking."}), 403

    # 2. Check booking is active
    if booking.status != "Booked":
        return jsonify({"error": f"Cannot cancel a booking with status '{booking.status}'."}), 400

    # 3. Check for completed or evaluated booking
    if booking.evaluation:
        return jsonify({"error": "Cannot cancel: This examination booking has already been evaluated."}), 400

    # 4. Deadline check: Cancellation is allowed only before slot_booking_end_date
    now = datetime.now()
    exam = booking.examination

    if now > exam.slot_booking_end_date or exam.status in ("Closed", "Completed"):
        return jsonify({
            "error": "Cancellation deadline has passed. Bookings cannot be cancelled after the booking deadline "
                     f"({exam.slot_booking_end_date})."
        }), 400

    # 5. Cancel and restore capacity in the same transaction. The conditional
    # update also prevents concurrent cancellation requests restoring twice.
    slot = booking.slot
    try:
        updated_bookings = Booking.query.filter(
            Booking.id == booking.id,
            Booking.student_id == student_id,
            Booking.status == "Booked",
            ~db.session.query(Evaluation.id).filter(
                Evaluation.booking_id == Booking.id
            ).exists()
        ).update({Booking.status: "Cancelled"}, synchronize_session=False)

        if updated_bookings == 0:
            db.session.rollback()
            return jsonify({"error": "This booking can no longer be cancelled."}), 400

        restored_slots = ExaminationSlot.query.filter(
            ExaminationSlot.id == slot.id,
            ExaminationSlot.status.in_(["Available", "Full"]),
            ExaminationSlot.available_seats < ExaminationSlot.max_capacity
        ).update({
            ExaminationSlot.available_seats: ExaminationSlot.available_seats + 1
        }, synchronize_session=False)

        if restored_slots == 0:
            db.session.rollback()
            return jsonify({"error": "The slot capacity could not be restored safely."}), 409

        db.session.refresh(slot)
        if slot.status == "Full":
            slot.status = "Available"
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Cancellation could not be completed because of a database error."}), 500

    db.session.refresh(booking)
    return jsonify({
        "message": "Booking cancelled successfully. Your slot seat has been restored.",
        "booking": booking.to_dict()
    }), 200


# -------------------------------------------------------------------------
# 6. Booking History
# -------------------------------------------------------------------------
@student_bp.route("/bookings", methods=["GET"])
@role_required("student")
def list_student_bookings():
    """
    Returns all bookings for the logged-in student (active, cancelled, completed).
    """
    student_id = session.get("user_id")
    bookings = Booking.query.filter_by(student_id=student_id).order_by(Booking.booking_date.desc()).all()

    now = datetime.now()
    results = []

    for b in bookings:
        d = b.to_dict()
        exam = b.examination
        d["course_name"] = exam.course.course_name if exam and exam.course else None
        d["duration_minutes"] = exam.duration_minutes if exam else None
        d["max_marks"] = exam.max_marks if exam else None
        d["is_evaluated"] = b.evaluation is not None
        d["can_cancel"] = (
            b.status == "Booked"
            and not b.evaluation
            and exam
            and now <= exam.slot_booking_end_date
            and exam.status not in ("Closed", "Completed")
            and b.slot
            and b.slot.status not in ("Cancelled", "Completed")
        )
        d["cancellation_deadline"] = exam.slot_booking_end_date.isoformat() if exam else None
        results.append(d)

    return jsonify({"bookings": results}), 200


# -------------------------------------------------------------------------
# 7. Results and Feedback
# -------------------------------------------------------------------------
@student_bp.route("/results", methods=["GET"])
@role_required("student")
def get_student_results():
    """
    View published results and examiner feedback for completed examinations.
    Does not expose unpublished or pending evaluations.
    """
    student_id = session.get("user_id")

    # Fetch completed bookings with evaluations
    evaluations = Evaluation.query.filter_by(
        student_id=student_id,
        status="Evaluated"
    ).join(Booking).filter(
        Booking.status == "Completed"
    ).order_by(Evaluation.evaluation_date.desc()).all()

    results = []
    for ev in evaluations:
        b = ev.booking
        exam = b.examination if b else None
        results.append({
            "evaluation_id": ev.id,
            "booking_id": ev.booking_id,
            "exam_name": exam.exam_name if exam else None,
            "course_code": exam.course.course_code if exam and exam.course else None,
            "course_name": exam.course.course_name if exam and exam.course else None,
            "examiner_name": ev.examiner.full_name if ev.examiner else None,
            "total_marks": ev.total_marks,
            "max_marks": exam.max_marks if exam else None,
            "percentage": round((ev.total_marks / exam.max_marks) * 100, 1) if exam and exam.max_marks else 0,
            "remarks": ev.remarks,
            "evaluation_date": ev.evaluation_date.isoformat(),
            "rubric_scores": [s.to_dict() for s in ev.rubric_scores]
        })

    return jsonify({"results": results}), 200


# -------------------------------------------------------------------------
# 8. Profile Management
# -------------------------------------------------------------------------
@student_bp.route("/profile", methods=["GET"])
@role_required("student")
def get_profile():
    """Returns student profile details."""
    student_id = session.get("user_id")
    student = db.session.get(User, student_id)
    return jsonify({"profile": student.to_dict()}), 200


@student_bp.route("/profile", methods=["PUT"])
@role_required("student")
def update_profile():
    """Edit student full name and permitted fields."""
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    student_id = session.get("user_id")
    student = db.session.get(User, student_id)

    data = request.get_json() or {}
    name = (data.get("full_name") or "").strip()

    if not name:
        return jsonify({"error": "Full name cannot be empty."}), 400

    student.full_name = name
    db.session.commit()

    return jsonify({
        "message": "Profile updated successfully.",
        "profile": student.to_dict()
    }), 200


# -------------------------------------------------------------------------
# 9. Asynchronous CSV Export of Student Examination History
# -------------------------------------------------------------------------
@student_bp.route("/export", methods=["POST"])
@role_required("student")
def trigger_export():
    """
    Triggers asynchronous CSV export of the logged-in student's examination history.
    Returns 202 Accepted with Celery task ID.
    """
    if not verify_csrf():
        return jsonify({"error": "CSRF token missing or invalid."}), 403

    student_id = session.get("user_id")
    task = export_student_csv.delay(student_id)

    return jsonify({
        "message": "Export initiated successfully.",
        "task_id": task.id,
        "status": "started"
    }), 202


@student_bp.route("/export/status/<task_id>", methods=["GET"])
@role_required("student")
def get_export_status(task_id):
    """
    Checks the status of the CSV export task.
    Enforces student ownership of the completed export.
    """
    student_id = session.get("user_id")
    try:
        result = AsyncResult(task_id, app=celery_app)
        status = result.state  # PENDING, STARTED, SUCCESS, FAILURE
    except Exception as e:
        return jsonify({
            "task_id": task_id,
            "status": "FAILURE",
            "ready": True,
            "error": str(e)
        }), 200

    if status == "SUCCESS":
        res_data = result.result or {}
        if res_data.get("student_id") != student_id:
            return jsonify({"error": "Unauthorized access to this export task."}), 403
        return jsonify({
            "task_id": task_id,
            "status": "SUCCESS",
            "ready": True,
            "filename": res_data.get("filename"),
            "total_rows": res_data.get("total_rows", 0)
        }), 200
    elif status == "FAILURE":
        return jsonify({
            "task_id": task_id,
            "status": "FAILURE",
            "ready": True,
            "error": str(result.info)
        }), 200
    else:
        return jsonify({
            "task_id": task_id,
            "status": status,
            "ready": False
        }), 200


@student_bp.route("/export/download/<task_id>", methods=["GET"])
@role_required("student")
def download_export(task_id):
    """
    Downloads the completed CSV export file.
    Verifies task readiness and strictly enforces student ownership.
    """
    student_id = session.get("user_id")
    try:
        result = AsyncResult(task_id, app=celery_app)
        if not result.ready():
            return jsonify({"error": "Export is still processing. Please try again shortly."}), 400

        if result.state != "SUCCESS":
            return jsonify({"error": f"Export task failed: {str(result.info)}"}), 400
    except Exception as e:
        return jsonify({"error": f"Unable to retrieve export task: {str(e)}"}), 400

    res_data = result.result or {}
    if res_data.get("student_id") != student_id:
        return jsonify({"error": "Unauthorized access to download this export file."}), 403

    filename = res_data.get("filename")
    if not filename:
        return jsonify({"error": "Export file not found."}), 404

    exports_dir = current_app.config.get("EXPORTS_DIR")
    file_path = os.path.join(exports_dir, filename)
    if not os.path.isfile(file_path):
        return jsonify({"error": "Export file no longer exists on server."}), 404

    return send_from_directory(
        exports_dir,
        filename,
        as_attachment=True,
        download_name=f"examination_history_{student_id}.csv",
        mimetype="text/csv"
    )


