
"""
Employee Attendance & Analytics API
HROne Software Engineer Trainee Assignment

Run:
    uvicorn app.main:app --reload --port 8000

Environment variables:
    MONGO_URI
    MONGO_DB
"""

import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))

client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    serverSelectionTimeoutMS=5000,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]

app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
)


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def now_ist() -> datetime:
    return datetime.now(IST)


def timestamp_to_datetime(timestamp: Optional[int]) -> datetime:
    if timestamp is None:
        return now_ist()

    # Input timestamp is expected in milliseconds.
    return datetime.fromtimestamp(timestamp / 1000, tz=IST)


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, "Date must use YYYY-MM-DD format")


def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    hours, minutes = map(int, shift_start.split(":"))

    start = punch_in.replace(
        hour=hours, minute=minutes, second=0, microsecond=0
    )

    late = int((punch_in - start).total_seconds() / 60)
    return late if late > 10 else 0


def compute_work_hours(
    punch_in: datetime, punch_out: datetime
) -> float:
    return round(
        max(0, (punch_out - punch_in).total_seconds() / 3600),
        2,
    )


def compute_overtime(
    punch_out: datetime, shift_end: str, date_str: str
) -> int:
    hours, minutes = map(int, shift_end.split(":"))
    day = parse_date(date_str)

    shift_end_dt = datetime.combine(
        day, time(hours, minutes), tzinfo=IST
    )

    return max(
        0, int((punch_out - shift_end_dt).total_seconds() / 60)
    )


def clean_document(doc: dict) -> dict:
    """Convert MongoDB ObjectId into a JSON-friendly string."""
    if "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


def get_employee(emp_code: str) -> dict:
    employee = db.employees.find_one({"emp_code": emp_code})

    if employee is None:
        raise HTTPException(404, "Employee not found")

    return employee


# --------------------------------------------------
# Request models
# --------------------------------------------------

class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


class AttendanceCorrection(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[datetime] = None
    punch_out: Optional[datetime] = None
    reason: str
    corrected_by: str


# --------------------------------------------------
# Health
# --------------------------------------------------

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok", "database": "connected"}
    except Exception:
        raise HTTPException(503, "Database unavailable")


# --------------------------------------------------
# Employees
# --------------------------------------------------


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")

    doc = body.model_dump()
    doc["created_at"] = now_ist()

    try:
        result = db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")

    # Fix: remove MongoDB's ObjectId from the response
    doc.pop("_id", None)
    doc["id"] = str(result.inserted_id)

    return doc



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {}

    if department:
        query["department"] = department

    total = db.employees.count_documents(query)
    skip = (page - 1) * page_size

    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# --------------------------------------------------
# Punch in
# --------------------------------------------------


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = get_employee(body.emp_code)
    timestamp = timestamp_to_datetime(body.punched_at)
    attendance_date = timestamp.date().isoformat()

    existing = db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "date": attendance_date,
    })

    if existing:
        raise HTTPException(
            409, "Attendance already exists for this date"
        )

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": timestamp,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            timestamp, employee["shift_start"]
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        result = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            409, "Attendance already exists for this date"
        )

    # FIX: Remove MongoDB ObjectId before returning
    doc.pop("_id", None)
    doc["id"] = str(result.inserted_id)

    return doc



# --------------------------------------------------
# Punch out
# --------------------------------------------------

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    timestamp = timestamp_to_datetime(body.punched_at)
    attendance_date = timestamp.date().isoformat()

    employee = get_employee(body.emp_code)

    attendance = db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "date": attendance_date,
    })

    if attendance is None:
        raise HTTPException(
            404, "No punch-in record found for this date"
        )

    if attendance.get("punch_out") is not None:
        raise HTTPException(
            409, "Already punched out for this date"
        )

    punch_in_time = attendance["punch_in"]

    # MongoDB may return a naive datetime. Interpret it as IST
    # for this provisional implementation.
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=IST)

    if timestamp < punch_in_time:
        raise HTTPException(
            400, "Punch-out cannot be before punch-in"
        )

    hours = compute_work_hours(punch_in_time, timestamp)
    overtime = compute_overtime(
        timestamp, employee["shift_end"], attendance_date
    )

    result = db.attendance_logs.update_one(
        {
            "_id": attendance["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": timestamp,
                "work_hours": hours,
                "overtime_minutes": overtime,
            }
        },
    )

    if result.modified_count == 0:
        raise HTTPException(
            409, "Attendance record was changed concurrently"
        )

    updated = db.attendance_logs.find_one(
        {"_id": attendance["_id"]}
    )
    return clean_document(updated)


# --------------------------------------------------
# List attendance
# --------------------------------------------------

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {}

    if emp_code:
        query["emp_code"] = emp_code

    if date_from or date_to:
        date_filter = {}

        if date_from:
            parse_date(date_from)
            date_filter["$gte"] = date_from

        if date_to:
            parse_date(date_to)
            date_filter["$lte"] = date_to

        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                400, "date_from cannot be after date_to"
            )

        query["date"] = date_filter

    if status:
        query["status"] = status

    total = db.attendance_logs.count_documents(query)
    skip = (page - 1) * page_size

    docs = list(
        db.attendance_logs.find(query)
        .sort("date", DESCENDING)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": [clean_document(doc) for doc in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# --------------------------------------------------
# Manual correction with audit history
# --------------------------------------------------

@app.patch("/attendance/{emp_code}/{date_str}")
def correct_attendance(
    emp_code: str,
    date_str: str,
    body: AttendanceCorrection,
):
    parse_date(date_str)
    get_employee(emp_code)

    attendance = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date_str,
    })

    if attendance is None:
        raise HTTPException(404, "Attendance record not found")

    changes = body.model_dump(
        exclude_unset=True,
        exclude={"reason", "corrected_by"},
    )

    if not changes:
        raise HTTPException(400, "No correction fields provided")

    # Preserve the old and new values for every corrected field.
    history_entry = {
        "corrected_at": now_ist(),
        "corrected_by": body.corrected_by,
        "reason": body.reason,
        "changes": {},
    }

    for field, new_value in changes.items():
        old_value = attendance.get(field)

        if isinstance(new_value, datetime) and new_value.tzinfo is None:
            new_value = new_value.replace(tzinfo=IST)

        history_entry["changes"][field] = {
            "old": old_value,
            "new": new_value,
        }

        changes[field] = new_value

    changes["history"] = attendance.get("history", []) + [
        history_entry
    ]

    db.attendance_logs.update_one(
        {"_id": attendance["_id"]},
        {"$set": changes},
    )

    return clean_document(
        db.attendance_logs.find_one({"_id": attendance["_id"]})
    )


# --------------------------------------------------
# Employee monthly analytics
# --------------------------------------------------

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(
    emp_code: str,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
):
    get_employee(emp_code)

    start = date(year, month, 1)

    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": start.isoformat(),
                    "$lt": end.isoformat(),
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "total_records": {"$sum": 1},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "PRESENT"]}, 1, 0
                        ]
                    }
                },
                "total_work_hours": {
                    "$sum": {"$ifNull": ["$work_hours", 0]}
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
        {"$project": {"_id": 0}},
    ]

    result = list(db.attendance_logs.aggregate(pipeline))

    return result[0] if result else {
        "total_records": 0,
        "present_days": 0,
        "total_work_hours": 0,
        "total_late_minutes": 0,
        "total_overtime_minutes": 0,
    }


# --------------------------------------------------
# Department summary
# --------------------------------------------------

@app.get("/analytics/departments/summary")
def department_summary():
    pipeline = [
        {
            "$group": {
                "_id": "$department",
                "employee_count": {"$sum": 1},
            }
        },
        {"$sort": {"_id": 1}},
    ]

    employee_counts = list(db.employees.aggregate(pipeline))

    attendance_pipeline = [
        {
            "$group": {
                "_id": "$emp_code",
                "attendance_days": {"$sum": 1},
                "work_hours": {
                    "$sum": {"$ifNull": ["$work_hours", 0]}
                },
                "late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
        {
            "$group": {
                "_id": "$employee.department",
                "attendance_days": {"$sum": "$attendance_days"},
                "work_hours": {"$sum": "$work_hours"},
                "late_minutes": {"$sum": "$late_minutes"},
            }
        },
    ]

    attendance = {
        item["_id"]: item
        for item in db.attendance_logs.aggregate(attendance_pipeline)
    }

    summaries = []

    for item in employee_counts:
        department = item["_id"]
        stats = attendance.get(department, {})

        summaries.append({
            "department": department,
            "employee_count": item["employee_count"],
            "attendance_days": stats.get("attendance_days", 0),
            "work_hours": round(stats.get("work_hours", 0), 2),
            "late_minutes": stats.get("late_minutes", 0),
        })

    return {"items": summaries}


# --------------------------------------------------
# Late arrival leaderboard
# --------------------------------------------------

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    limit: int = Query(10, ge=1, le=100),
):
    pipeline = [
        {
            "$group": {
                "_id": "$emp_code",
                "late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "late_days": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]}, 1, 0
                        ]
                    }
                },
            }
        },
        {"$sort": {"late_minutes": -1, "_id": 1}},
        {"$limit": limit},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "late_minutes": 1,
                "late_days": 1,
                "name": {"$arrayElemAt": ["$employee.name", 0]},
                "department": {
                    "$arrayElemAt": ["$employee.department", 0]
                },
            }
        },
    ]

    return {"items": list(db.attendance_logs.aggregate(pipeline))}


# --------------------------------------------------
# Department attendance trend
# --------------------------------------------------

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    employee_codes = db.employees.distinct(
        "emp_code", {"department": department}
    )

    if not employee_codes:
        return {"department": department, "items": []}

    match = {"emp_code": {"$in": employee_codes}}

    if date_from or date_to:
        date_filter = {}

        if date_from:
            parse_date(date_from)
            date_filter["$gte"] = date_from

        if date_to:
            parse_date(date_to)
            date_filter["$lte"] = date_to

        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                400, "date_from cannot be after date_to"
            )

        match["date"] = date_filter

    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": "$date",
                "attendance_records": {"$sum": 1},
                "present_records": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "PRESENT"]}, 1, 0
                        ]
                    }
                },
                "work_hours": {
                    "$sum": {"$ifNull": ["$work_hours", 0]}
                },
            }
        },
        {"$sort": {"_id": 1}},
        {
            "$project": {
                "_id": 0,
                "date": "$_id",
                "attendance_records": 1,
                "present_records": 1,
                "work_hours": 1,
            }
        },
    ]

    return {
        "department": department,
        "items": list(db.attendance_logs.aggregate(pipeline)),
    }


# --------------------------------------------------
# MongoDB query execution plan
# --------------------------------------------------

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str):
    # Restrict this endpoint to known, read-only queries.
    queries = {
        "employees": (
            db.employees,
            {},
        ),
        "attendance": (
            db.attendance_logs,
            {},
        ),
        "attendance-by-employee": (
            db.attendance_logs,
            {"emp_code": {"$exists": True}},
        ),
    }

    if endpoint not in queries:
        raise HTTPException(404, "Unknown explain endpoint")

    collection, query = queries[endpoint]

    return collection.find(query).explain()


# --------------------------------------------------
# End of application
# --------------------------------------------------
