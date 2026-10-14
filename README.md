# HROne Attendance & Analytics API

A REST API built with **FastAPI, Python, and MongoDB** for employee management, attendance tracking, manual attendance corrections, and workforce analytics.

## Features

- Create and list employees.
- Record employee punch-in and punch-out.
- Retrieve attendance records with pagination and filters.
- Correct attendance records and maintain an audit history.
- Generate monthly attendance analytics.
- View department summaries, late-arrival rankings, and department attendance trends.
- Health-check endpoint for service availability.

## Tech Stack

- Python 3.11+
- FastAPI
- MongoDB and PyMongo
- Pydantic
- Uvicorn

## Prerequisites

- Python 3.11 or later
- A MongoDB instance, such as MongoDB Atlas or a local MongoDB server
- Git

## Setup and Run

Clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/hrone-attendance-api.git
cd hrone-attendance-api
```

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure the MongoDB connection using the environment variables expected by the application:

```bash
export MONGO_URI="your-mongodb-connection-string"
export MONGO_DB="your-database-name"
```

Do not commit credentials or secrets to GitHub.

Start the API:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## API Documentation

After starting the server, open:

- Swagger UI: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

Use Swagger UI to inspect and test the available endpoints and request schemas.

## Design and Review Notes

See [DECISIONS.md](DECISIONS.md) for implementation decisions and trade-offs.

See [REVIEW.md](REVIEW.md) for code-review findings, observed issues, and fixes or remaining limitations.

## Scope

This project was developed as part of the HROne Software Engineer Trainee assignment. Refer to the API implementation and review notes for the current implementation status and known limitations.



# Code Review

## Finding 1: Database indexes and concurrent punch-in

- **Location:** Attendance punch-in handler and application startup.
- **Problem:** Without an appropriate unique index, simultaneous punch-in requests may create duplicate attendance records.
- **Expected behavior:** One request succeeds and competing requests receive a conflict response.
- **Fix or status:** Document the actual index and concurrency handling implemented, or mark this as a remaining limitation.
- **Verification:** Record the test performed and its result.

## Finding 2: Attendance calculations

- **Location:** Time conversion, work-hour, late-arrival, and overtime helper functions.
- **Problem:** Verify handling of overnight shifts, timestamps truncated to whole seconds, half-up rounding, and the minimum overtime threshold.
- **Fix or status:** Describe the actual changes made, or identify the behavior that remains incomplete.
- **Verification:** Include test cases and observed results.

## Finding 3: Attendance correction and audit history

- **Location:** Attendance PATCH endpoint.
- **Problem:** Verify that corrections recalculate derived fields and that concurrent corrections cannot overwrite audit-history entries.
- **Fix or status:** Describe the implementation or clearly state what remains unresolved.
- **Verification:** Record the tests performed.

## Finding 4: Analytics and query plans

- **Location:** Monthly analytics, department summary, late leaderboard, department trend, and explain endpoint.
- **Problem:** Verify all assignment rules, including headcount, ranking ties, missing calendar dates, moving averages, and indexed query plans.
- **Fix or status:** List the changes actually implemented and any remaining gaps.
- **Verification:** Record the relevant tests and explain-plan results.