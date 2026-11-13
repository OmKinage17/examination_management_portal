"""
Celery Background Tasks and Batch Jobs.
Implements:
1. send_daily_reminders (Daily scheduled reminder job)
2. send_monthly_admin_report (Monthly scheduled admin reporting job)
3. export_student_csv (Asynchronous on-demand student CSV export)
"""
import csv
import logging
import os
import uuid
from datetime import date, datetime, timedelta
from flask import current_app

from backend.cache import get_redis_client
from backend.extensions import db
from backend.models import (
    Booking,
    Course,
    Evaluation,
    Examination,
    ExaminationSlot,
    User,
)
from backend.tasks.celery_app import celery_app
from backend.tasks.email_service import send_email

logger = logging.getLogger(__name__)

# In-memory deduplication set as a resilient fallback when Redis is offline or mocked
_fallback_sent_reminders = set()


@celery_app.task(name="backend.tasks.jobs.send_daily_reminders")
def send_daily_reminders(target_date_str=None):
    """
    Daily examination reminder scheduled task.
    Identifies students with upcoming booked slots on target_date (defaults to tomorrow).
    Sends notifications via real SMTP email.
    Excludes cancelled and completed bookings.
    Deduplicates reminders to avoid multiple deliveries for the same booking.
    """
    if target_date_str:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    else:
        target_date = date.today() + timedelta(days=1)

    redis_client = get_redis_client()

    # Query active bookings for slots scheduled on target_date
    bookings = (
        Booking.query.join(ExaminationSlot)
        .filter(
            ExaminationSlot.exam_date == target_date,
            Booking.status == "Booked",
            ExaminationSlot.status != "Cancelled",
        )
        .all()
    )

    reminders_sent = 0
    reminders_skipped = 0
    errors = []

    for booking in bookings:
        student = booking.student
        slot = booking.slot
        examination = booking.examination

        if not student or not slot or not examination:
            continue

        # Unique deduplication key for this booking on this exam date
        dedup_key = f"reminder_sent:{booking.id}:{target_date.isoformat()}"

        # Check deduplication in Redis or memory fallback
        already_sent = False
        try:
            if redis_client and redis_client.get(dedup_key):
                already_sent = True
        except Exception:
            pass

        if not already_sent and dedup_key in _fallback_sent_reminders:
            already_sent = True

        if already_sent:
            reminders_skipped += 1
            continue

        # Format reminder details
        exam_name = examination.exam_name
        course_name = examination.course.course_name if examination.course else "Unknown Course"
        course_code = examination.course.course_code if examination.course else ""
        examiner_name = slot.examiner.full_name if slot.examiner else "Unassigned"
        slot_date = slot.exam_date.strftime("%Y-%m-%d")
        slot_time = f"{slot.start_time.strftime('%H:%M')} - {slot.end_time.strftime('%H:%M')}"

        subject = f"Reminder: Upcoming Examination - {exam_name}"
        body_text = (
            f"Dear {student.full_name},\n\n"
            f"This is a reminder for your upcoming examination scheduled for tomorrow:\n\n"
            f"  - Examination: {exam_name}\n"
            f"  - Course: {course_name} ({course_code})\n"
            f"  - Date: {slot_date}\n"
            f"  - Time: {slot_time}\n"
            f"  - Examiner: {examiner_name}\n\n"
            f"Please ensure you join on time.\n\n"
            f"Examination Management Portal V2"
        )

        body_html = (
            f"<p>Dear {student.full_name},</p>"
            f"<p>This is a reminder for your upcoming examination scheduled for tomorrow:</p>"
            f"<ul>"
            f"  <li><strong>Examination:</strong> {exam_name}</li>"
            f"  <li><strong>Course:</strong> {course_name} ({course_code})</li>"
            f"  <li><strong>Date:</strong> {slot_date}</li>"
            f"  <li><strong>Time:</strong> {slot_time}</li>"
            f"  <li><strong>Examiner:</strong> {examiner_name}</li>"
            f"</ul>"
            f"<p>Please ensure you join on time.</p>"
            f"<p><em>Examination Management Portal V2</em></p>"
        )

        try:
            # Deliver via real SMTP
            send_email(
                to_email=student.email,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
            )

            # Mark deduplication key (7 days TTL)
            try:
                if redis_client:
                    redis_client.set(dedup_key, "1", ex=86400 * 7)
            except Exception:
                pass
            _fallback_sent_reminders.add(dedup_key)
            reminders_sent += 1

        except Exception as e:
            logger.error(f"[Reminder Task] Failed to send email to {student.email}: {e}")
            errors.append(f"{student.email}: {str(e)}")

    return {
        "status": "success",
        "target_date": target_date.isoformat(),
        "reminders_sent": reminders_sent,
        "reminders_skipped": reminders_skipped,
        "errors": errors,
    }


@celery_app.task(name="backend.tasks.jobs.send_monthly_admin_report")
def send_monthly_admin_report():
    """
    Monthly administrative report scheduled task.
    Aggregates portal statistics:
    - Total examinations
    - Total bookings
    - Completed examinations
    - Completed evaluations
    - Examiner activity breakdown
    Dispatches report via SMTP email to the Admin.
    """
    admin_email = current_app.config.get("ADMIN_EMAIL", "admin@emp.local")

    total_exams = Examination.query.count()
    total_bookings = Booking.query.count()
    completed_exams = Examination.query.filter_by(status="Completed").count()
    completed_bookings = Booking.query.filter_by(status="Completed").count()
    completed_evaluations = Evaluation.query.count()

    # Aggregate examiner activity
    examiners = User.query.filter_by(role="examiner").all()
    examiner_activity = []
    for examiner in examiners:
        slots_count = ExaminationSlot.query.filter_by(examiner_id=examiner.id).count()
        evals_count = Evaluation.query.filter_by(examiner_id=examiner.id).count()
        examiner_activity.append({
            "examiner_id": examiner.id,
            "name": examiner.full_name,
            "email": examiner.email,
            "slots_created": slots_count,
            "evaluations_completed": evals_count,
        })

    month_str = datetime.now().strftime("%B %Y")
    subject = f"Monthly Examination Portal Activity Report - {month_str}"

    # Build plain text report
    lines = [
        f"Monthly Examination Activity Report — {month_str}",
        "=" * 50,
        f"Total Examinations: {total_exams}",
        f"Total Bookings: {total_bookings}",
        f"Completed Examinations: {completed_exams}",
        f"Completed Bookings: {completed_bookings}",
        f"Completed Evaluations: {completed_evaluations}",
        "",
        "Examiner Activity Breakdown:",
        "-" * 50,
    ]
    for ea in examiner_activity:
        lines.append(
            f"  - {ea['name']} ({ea['email']}): Slots Created={ea['slots_created']}, Evaluations Completed={ea['evaluations_completed']}"
        )
    if not examiner_activity:
        lines.append("  (No examiners registered)")
    lines.append("")
    lines.append("Automated message from Examination Management Portal V2.")
    body_text = "\n".join(lines)

    # Build HTML report
    html_lines = [
        f"<h2>Monthly Examination Activity Report — {month_str}</h2>",
        "<ul>",
        f"  <li><strong>Total Examinations:</strong> {total_exams}</li>",
        f"  <li><strong>Total Bookings:</strong> {total_bookings}</li>",
        f"  <li><strong>Completed Examinations:</strong> {completed_exams}</li>",
        f"  <li><strong>Completed Bookings:</strong> {completed_bookings}</li>",
        f"  <li><strong>Completed Evaluations:</strong> {completed_evaluations}</li>",
        "</ul>",
        "<h3>Examiner Activity Breakdown</h3>",
        "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse: collapse;'>",
        "  <tr><th>Examiner Name</th><th>Email</th><th>Slots Created</th><th>Evaluations Completed</th></tr>",
    ]
    for ea in examiner_activity:
        html_lines.append(
            f"  <tr><td>{ea['name']}</td><td>{ea['email']}</td><td>{ea['slots_created']}</td><td>{ea['evaluations_completed']}</td></tr>"
        )
    html_lines.append("</table>")
    html_lines.append("<p><em>Automated message from Examination Management Portal V2.</em></p>")
    body_html = "\n".join(html_lines)

    # Dispatch email
    send_email(
        to_email=admin_email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )

    return {
        "status": "success",
        "admin_email": admin_email,
        "total_exams": total_exams,
        "total_bookings": total_bookings,
        "completed_exams": completed_exams,
        "completed_evaluations": completed_evaluations,
        "examiner_count": len(examiner_activity),
    }


@celery_app.task(name="backend.tasks.jobs.export_student_csv")
def export_student_csv(student_id: int):
    """
    Asynchronous CSV export task for a student's examination history.
    Strictly queries only records belonging to student_id.
    Writes CSV with the mandatory 6 columns:
    [Examination Name, Course, Examination Date, Examiner, Marks Obtained, Booking Status]
    Stores file in a secure exports directory.
    """
    student = db.session.get(User, student_id)
    if not student or student.role != "student":
        raise ValueError(f"Student with ID {student_id} not found or invalid role.")

    bookings = Booking.query.filter_by(student_id=student_id).order_by(Booking.booking_date.desc()).all()

    exports_dir = current_app.config.get("EXPORTS_DIR")
    os.makedirs(exports_dir, exist_ok=True)

    filename = f"student_{student_id}_history_{uuid.uuid4().hex[:10]}.csv"
    filepath = os.path.join(exports_dir, filename)

    with open(filepath, mode="w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        # Write exact mandatory header row
        writer.writerow([
            "Examination Name",
            "Course",
            "Examination Date",
            "Examiner",
            "Marks Obtained",
            "Booking Status",
        ])

        for b in bookings:
            exam_name = b.examination.exam_name if b.examination else "Unknown Examination"
            course_info = (
                f"{b.examination.course.course_code} - {b.examination.course.course_name}"
                if b.examination and b.examination.course
                else "Unknown Course"
            )
            exam_date = b.slot.exam_date.strftime("%Y-%m-%d") if b.slot and b.slot.exam_date else "Unscheduled"
            examiner_name = b.slot.examiner.full_name if b.slot and b.slot.examiner else "Unassigned"
            marks = (
                str(b.evaluation.total_marks)
                if b.evaluation and b.evaluation.total_marks is not None
                else "Not Evaluated"
            )
            status = b.status

            writer.writerow([
                exam_name,
                course_info,
                exam_date,
                examiner_name,
                marks,
                status,
            ])

    return {
        "status": "SUCCESS",
        "student_id": student_id,
        "filename": filename,
        "filepath": filepath,
        "total_rows": len(bookings),
    }
