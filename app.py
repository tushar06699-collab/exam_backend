# PART 1/4
import os
import shutil
import random
import smtplib
import json
import re
import urllib.request
import urllib.error
from flask import Flask, request, jsonify, send_file, Response, make_response
from flask_cors import CORS
from pymongo import MongoClient, ASCENDING
from bson.objectid import ObjectId
from datetime import datetime, timedelta
from pymongo import MongoClient
from email.mime.text import MIMEText

def _load_dotenv_simple():
    # Load .env without extra dependency; process env already-set values take precedence.
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(__file__), ".env"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#") or "=" not in s:
                        continue
                    k, v = s.split("=", 1)
                    key = k.strip()
                    val = v.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
            break
        except Exception:
            pass

_load_dotenv_simple()


# ---------------------------
# MongoDB connection
# ---------------------------
MONGO_URL = os.environ.get(
    "MONGO_URL",
    "mongodb+srv://myusere:mypassword123@cluster0.fpsihrb.mongodb.net/?appName=Cluster0"
)
client = MongoClient(MONGO_URL)
db = client["school_exam_db"]

# --------------------------------------------------------
# MongoDB (STUDENT DATABASE ONLY)
# --------------------------------------------------------
STUDENT_MONGO_URI = os.environ.get(
    "STUDENT_MONGO_URI",
    "mongodb+srv://school_students:Tushar2007@cluster0.upoywck.mongodb.net/school_erp?retryWrites=true&w=majority"
)

student_client = MongoClient(STUDENT_MONGO_URI)
student_db = student_client["school_erp"]
students_col = student_db["students"]
student_teachers_col = student_db["teachers"]


# Collections (mirror of your sqlite tables)
exams_col = db["exams"]
exam_subjects_col = db["exam_subjects"]
datesheet_col = db["datesheet"]
exam_marks_col = db["exam_marks"]
class_incharge_col = db["class_incharge"]
teachers_col = db["teachers"]
timetable_col = db["timetable"]
internal_marks_col = db["internal_marks"]
internal_config_col = db["internal_config"]
result_publish_col = db["result_publish"]
exam_subject_config_col = db["exam_subject_config"]
teacher_daily_work_col = db["teacher_daily_work"]
rooms_col = db["rooms"]
student_access_col = db["student_exam_access"]

# Create useful indexes to emulate UNIQUE constraints where used in sqlite
# Note: index creation is idempotent
exams_col.create_index([("exam_name", ASCENDING), ("session", ASCENDING)])
exam_subjects_col.create_index([("session", ASCENDING), ("class_name", ASCENDING), ("subject", ASCENDING)], unique=True)
datesheet_col.create_index([("session", ASCENDING), ("class_name", ASCENDING), ("exam_name", ASCENDING), ("subject", ASCENDING)], unique=True)
exam_marks_col.create_index([("session", ASCENDING), ("exam_id", ASCENDING), ("class_name", ASCENDING), ("subject", ASCENDING), ("roll", ASCENDING)], unique=True)
class_incharge_col.create_index([("session", ASCENDING), ("class_name", ASCENDING)], unique=True)
teachers_col.create_index([("session", ASCENDING), ("username", ASCENDING)], unique=True)
timetable_col.create_index([("session", ASCENDING), ("teacher_id", ASCENDING), ("period", ASCENDING), ("class", ASCENDING)])
_internal_marks_index_old = "session_1_class_name_1_subject_1_student_id_1"
_internal_marks_index_new = "session_1_class_name_1_subject_1_exam_name_1_student_id_1"
try:
    existing_indexes = internal_marks_col.index_information()
    if _internal_marks_index_old in existing_indexes:
        internal_marks_col.drop_index(_internal_marks_index_old)
except Exception:
    # Avoid startup crash if index drop fails; app can still run.
    pass
internal_marks_col.create_index(
    [("session", ASCENDING), ("class_name", ASCENDING), ("subject", ASCENDING), ("exam_name", ASCENDING), ("student_id", ASCENDING)],
    unique=True,
    name=_internal_marks_index_new
)
internal_config_col.create_index([("session", ASCENDING), ("class_name", ASCENDING), ("subject", ASCENDING)], unique=True)
result_publish_col.create_index([("session", ASCENDING), ("class_name", ASCENDING), ("exam_name", ASCENDING)], unique=True)
exam_subject_config_col.create_index(
    [("session", ASCENDING), ("class_name", ASCENDING), ("exam_name", ASCENDING), ("subject", ASCENDING)],
    unique=True
)
student_access_col.create_index(
    [("session", ASCENDING), ("class_name", ASCENDING), ("student_id", ASCENDING)],
    unique=True
)

OTP_STORE = {}
SPECIAL_OTP_USERS = {"PSPSLIB", "PSPSSTU", "PSPSTEA", "ADMIN", "PRINCIPAL"}

def mask_mobile(mobile):
    raw = str(mobile or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 10:
        d = digits[-10:]
        return f"{d[:2]}XXXXXX{d[-2:]}"
    if len(digits) >= 4:
        return f"{digits[:2]}XX{digits[-2:]}"
    return raw

def normalize_sms_mobile(mobile):
    digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    if str(mobile or "").strip().startswith("+"):
        return str(mobile).strip()
    return str(mobile or "").strip()

def find_student_teacher_profile(exam_teacher):
    if not exam_teacher:
        return {}

    teacher_code = str(exam_teacher.get("teacher_id", "")).strip()
    username = str(exam_teacher.get("username", "")).strip()
    display_name = str(exam_teacher.get("name", "")).strip()

    lookup_filters = []
    if teacher_code:
        lookup_filters.extend([
            {"teacher_code": teacher_code},
            {"employee_id": teacher_code}
        ])
    if username:
        lookup_filters.append({"employee_id": username})
    if display_name:
        lookup_filters.append({"teacher_name": {"$regex": f"^{re.escape(display_name)}$", "$options": "i"}})

    profile = None
    for q in lookup_filters:
        profile = student_teachers_col.find_one(q)
        if profile:
            break

    return {
        "teacher_name": (profile or {}).get("teacher_name") or display_name or username,
        "mobile": str((profile or {}).get("mobile", "")).strip(),
        "photo_url": str((profile or {}).get("photo_url", "")).strip(),
        "dob": str((profile or {}).get("dob", "")).strip() or str((profile or {}).get("date_of_birth", "")).strip(),
        "teacher_code": str((profile or {}).get("teacher_code", "")).strip(),
    }

def get_teacher_profile_payload(username):
    username_u = str(username or "").strip().upper()
    if not username_u:
        return None, None, None
    teacher = teachers_col.find_one({"username": username_u})
    if not teacher:
        return None, None, None
    profile = find_student_teacher_profile(teacher)
    teacher_payload = {
        "username": username_u,
        "name": profile.get("teacher_name", teacher.get("name", username_u)),
        "photo_url": profile.get("photo_url", ""),
        "mobile_masked": mask_mobile(profile.get("mobile", ""))
    }
    return teacher, profile, teacher_payload

def _is_sms_response_success(data):
    if isinstance(data, dict):
        if data.get("success") is True:
            return True
        if data.get("status") in {"success", "ok", 200, "200"}:
            return True
        if data.get("message") in {"success", "queued", "sent"}:
            return True
    return False

def send_textbee_otp(mobile, otp_code, teacher_name):
    api_url = str(os.environ.get("TEXTBEE_API_URL", "")).strip()
    api_key = str(os.environ.get("TEXTBEE_API_KEY", "")).strip()
    device_id = str(os.environ.get("TEXTBEE_DEVICE_ID", "")).strip()

    if not api_url:
        return False, "TEXTBEE_API_URL not configured"
    if not api_key:
        return False, "TEXTBEE_API_KEY not configured"

    to_mobile = normalize_sms_mobile(mobile)
    sms_text = f"Dear {teacher_name or 'Teacher'}, your login OTP is {otp_code}. Valid for 5 minutes. - School ERP"

    auth_variants = [
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "x-api-key": api_key,
        },
        {
            "Content-Type": "application/json",
            "x-api-key": api_key,
        },
        {
            "Content-Type": "application/json",
            "Authorization": api_key,
        }
    ]

    base_url = api_url
    for suffix in ["/api/v1/messages", "/api/v1/message/send", "/api/v1/send-sms", "/api/v1/sms/send"]:
        if base_url.lower().endswith(suffix):
            base_url = base_url[: -len(suffix)]
            break
    base_url = base_url.rstrip("/")

    endpoint_candidates = [api_url]
    if base_url:
        endpoint_candidates.extend([
            f"{base_url}/api/v1/messages",
            f"{base_url}/api/v1/message/send",
            f"{base_url}/api/v1/send-sms",
            f"{base_url}/api/v1/sms/send",
        ])
        if device_id:
            endpoint_candidates.extend([
                f"{base_url}/api/v1/gateway/devices/{device_id}/send-sms",
                f"{base_url}/api/v1/gateway/devices/{device_id}/messages",
                f"{base_url}/api/v1/devices/{device_id}/messages",
            ])

    # preserve order while removing duplicates
    endpoint_candidates = list(dict.fromkeys([e for e in endpoint_candidates if e]))

    payload_templates = [
        {"recipients": [to_mobile], "message": sms_text},
        {"phone": to_mobile, "message": sms_text},
        {"to": to_mobile, "message": sms_text},
        {"mobile": to_mobile, "message": sms_text},
    ]
    payloads = []
    for p in payload_templates:
        payloads.append(dict(p))
        if device_id:
            with_device = dict(p)
            with_device["deviceId"] = device_id
            payloads.append(with_device)

    request_timeout = float(os.environ.get("TEXTBEE_TIMEOUT_SEC", "4"))
    max_attempts = int(os.environ.get("TEXTBEE_MAX_ATTEMPTS", "12"))
    attempts = 0
    last_err = "SMS send failed"
    for endpoint in endpoint_candidates:
        for headers in auth_variants:
            for payload in payloads:
                if attempts >= max_attempts:
                    return False, last_err
                attempts += 1
                req = urllib.request.Request(
                    endpoint,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST"
                )
                try:
                    with urllib.request.urlopen(req, timeout=request_timeout) as resp:
                        body = resp.read().decode("utf-8", errors="ignore")
                        parsed = {}
                        try:
                            parsed = json.loads(body) if body else {}
                        except Exception:
                            parsed = {}
                        if 200 <= resp.status < 300 and (not parsed or _is_sms_response_success(parsed)):
                            return True, ""
                        if 200 <= resp.status < 300 and parsed:
                            return True, ""
                        last_err = body or f"http {resp.status}"
                except urllib.error.HTTPError as e:
                    try:
                        err_body = e.read().decode("utf-8", errors="ignore")
                    except Exception:
                        err_body = str(e)
                    last_err = f"{endpoint} :: {err_body or str(e)}"
                except Exception as e:
                    last_err = f"{endpoint} :: {str(e)}"

    return False, last_err

def get_teacher_otp_config_status():
    api_url = str(os.environ.get("TEXTBEE_API_URL", "")).strip()
    api_key = str(os.environ.get("TEXTBEE_API_KEY", "")).strip()
    device_id = str(os.environ.get("TEXTBEE_DEVICE_ID", "")).strip()
    timeout_sec = str(os.environ.get("TEXTBEE_TIMEOUT_SEC", "4")).strip()
    max_attempts = str(os.environ.get("TEXTBEE_MAX_ATTEMPTS", "12")).strip()

    missing = []
    if not api_url:
        missing.append("TEXTBEE_API_URL")
    if not api_key:
        missing.append("TEXTBEE_API_KEY")
    if not device_id:
        missing.append("TEXTBEE_DEVICE_ID")

    return {
        "ok": len(missing) == 0,
        "api_url": api_url,
        "api_key_set": bool(api_key),
        "device_id_set": bool(device_id),
        "timeout_sec": timeout_sec,
        "max_attempts": max_attempts,
        "missing": missing
    }

def get_special_user_email(username):
    key = str(username or "").strip().upper()
    val = str(os.environ.get(f"OTP_MAIL_{key}", "")).strip()
    if key == "PRINCIPAL" and not val:
        val = str(os.environ.get("OTP_MAIL_NAVEEN", "")).strip()
    return val

def get_teacher_otp_email(username):
    key = str(username or "").strip().upper()
    candidates = [
        os.environ.get(f"OTP_MAIL_{key}", ""),
        os.environ.get("OTP_MAIL_PSPSTEA", ""),
        os.environ.get("OTP_MAIL_TEACHER", ""),
        os.environ.get("OTP_MAIL_ADMIN", ""),
    ]
    for c in candidates:
        c = str(c or "").strip()
        if c:
            return c
    return ""

def mask_email(email):
    e = str(email or "").strip()
    if "@" not in e:
        return e
    local, domain = e.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*" * (len(local) - 1)
    else:
        masked_local = local[:2] + "*" * max(1, len(local) - 2)
    return f"{masked_local}@{domain}"

def _normalize_teacher_name_for_password(name):
    return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())

def _dob_candidates_for_password(raw):
    s = str(raw or "").strip()
    if not s:
        return []
    candidates = []
    m = re.match(r"^(\d{4})[-\/](\d{2})[-\/](\d{2})$", s)  # yyyy-mm-dd
    if m:
        candidates.append(f"{m.group(3)}{m.group(2)}{m.group(1)}")
        candidates.append(f"{m.group(1)}{m.group(2)}{m.group(3)}")
    m = re.match(r"^(\d{2})[-\/](\d{2})[-\/](\d{4})$", s)  # dd-mm-yyyy
    if m:
        candidates.append(f"{m.group(1)}{m.group(2)}{m.group(3)}")
        candidates.append(f"{m.group(3)}{m.group(2)}{m.group(1)}")
    digits = re.sub(r"\D", "", s)
    if re.match(r"^\d{8}$", digits):
        candidates.append(digits)
        candidates.append(f"{digits[4:8]}{digits[2:4]}{digits[0:2]}")
    # Remove duplicates while preserving order
    out = []
    for c in candidates:
        if c not in out:
            out.append(c)
    return out

def _teacher_password_matches(name, dob, raw_password, teacher_code=""):
    if not raw_password:
        return False
    raw_norm = str(raw_password).strip().upper().replace(" ", "")
    code_digits = re.sub(r"\D", "", str(teacher_code or "")).zfill(4)
    if code_digits:
        for dob_code in _dob_candidates_for_password(dob):
            if raw_norm == f"{code_digits}@{dob_code}":
                return True
    name_part = _normalize_teacher_name_for_password(name)
    if name_part:
        for dob_code in _dob_candidates_for_password(dob):
            if raw_norm == f"{name_part}@{dob_code}":
                return True
    for dob_code in _dob_candidates_for_password(dob):
        if raw_norm == dob_code:
            return True
    return False

def send_otp_email(to_email, otp_code, username):
    smtp_host = os.environ.get("OTP_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("OTP_SMTP_PORT", "587"))
    smtp_user = os.environ.get("OTP_SMTP_USER", "")
    smtp_pass = os.environ.get("OTP_SMTP_PASS", "")
    from_email = os.environ.get("OTP_FROM_EMAIL", smtp_user)

    if not to_email:
        return False, "Recipient email not configured"
    if not smtp_user or not smtp_pass:
        return False, "SMTP credentials not configured"

    msg = MIMEText(
        f"Your OTP for School ERP login is: {otp_code}\n"
        f"This OTP expires in 5 minutes.\n"
        f"Username: {username}"
    )
    msg["Subject"] = "School ERP Login OTP"
    msg["From"] = from_email
    msg["To"] = to_email

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, [to_email], msg.as_string())
        return True, ""
    except Exception as e:
        return False, str(e)

def get_otp_config_status(username):
    user_key = str(username or "").strip().upper()
    smtp_host = os.environ.get("OTP_SMTP_HOST", "smtp.gmail.com")
    smtp_port = os.environ.get("OTP_SMTP_PORT", "587")
    smtp_user = os.environ.get("OTP_SMTP_USER", "")
    smtp_pass = os.environ.get("OTP_SMTP_PASS", "")
    from_email = os.environ.get("OTP_FROM_EMAIL", smtp_user)
    to_email = get_special_user_email(user_key)

    missing = []
    if not smtp_host:
        missing.append("OTP_SMTP_HOST")
    if not smtp_port:
        missing.append("OTP_SMTP_PORT")
    if not smtp_user:
        missing.append("OTP_SMTP_USER")
    if not smtp_pass:
        missing.append("OTP_SMTP_PASS")
    if not to_email:
        missing.append(f"OTP_MAIL_{user_key}")

    return {
        "ok": len(missing) == 0,
        "username": user_key,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_user_set": bool(smtp_user),
        "smtp_pass_set": bool(smtp_pass),
        "from_email": from_email,
        "recipient_masked": mask_email(to_email) if to_email else "",
        "missing": missing
    }

# ---------------------------
# Flask app + uploads folder
# ---------------------------
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

@app.before_request
def _handle_preflight():
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
        resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = request.headers.get(
            "Access-Control-Request-Headers",
            "Content-Type, Authorization"
        )
        resp.headers["Access-Control-Max-Age"] = "3600"
        return resp

@app.after_request
def _add_cors_headers(resp):
    if not resp.headers.get("Access-Control-Allow-Origin"):
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
    resp.headers.setdefault("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
    resp.headers.setdefault("Access-Control-Allow-Headers", "Content-Type, Authorization")
    return resp

PAPER_DIR = "papers"
os.makedirs(PAPER_DIR, exist_ok=True)

# Helper to convert Mongo documents to JSON-friendly dicts
def id_str(doc):
    if not doc:
        return None
    doc = dict(doc)
    if "_id" in doc:
        doc["id"] = str(doc["_id"])
        del doc["_id"]
    return doc

def to_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ["1", "true", "yes", "y"]
    return default

def session_variants(session_value):
    s = str(session_value or "").strip()
    if not s:
        return []
    alt = s.replace("_", "-") if "_" in s else s.replace("-", "_")
    if alt == s:
        return [s]
    return [s, alt]

def normalize_student_id(raw):
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        if isinstance(raw.get("$oid"), str):
            return raw.get("$oid").strip()
        if isinstance(raw.get("oid"), str):
            return raw.get("oid").strip()
        if isinstance(raw.get("id"), str):
            return raw.get("id").strip()
    try:
        return str(raw).strip()
    except Exception:
        return ""

def get_student_access_flags(student_doc):
    session = student_doc.get("session", "")
    class_name = student_doc.get("class_name", "")
    student_id = str(student_doc.get("_id", ""))

    access_doc = None
    if class_name and student_id:
        sessions = session_variants(session)
        if sessions:
            access_doc = student_access_col.find_one({
                "session": {"$in": sessions},
                "class_name": class_name,
                "student_id": student_id
            })
        else:
            access_doc = student_access_col.find_one({
                "class_name": class_name,
                "student_id": student_id
            })

    eligible = to_bool(access_doc.get("eligible"), False) if access_doc else False
    release_rollno = to_bool(access_doc.get("release_rollno"), False) if access_doc else False
    release_result = to_bool(access_doc.get("release_result"), False) if access_doc else False

    # Business rule: non-eligible students cannot have roll/result released.
    if not eligible:
        release_rollno = False
        release_result = False

    return {
        "eligible": eligible,
        "release_rollno": release_rollno,
        "release_result": release_result
    }

# ---------------------------
# Create exam (equivalent to /exam/create)
# ---------------------------
@app.route("/exam/create", methods=["POST"])
def create_exam():
    data = request.json or {}
    exam_name = data.get("exam_name")
    session = data.get("session")
    exam_time = data.get("exam_time")
    total_marks = data.get("total_marks")
    internal_marks = data.get("internal_marks")

    if not exam_name or not session or not exam_time or total_marks is None:
        return jsonify({"success": False, "message": "Missing fields"}), 400

    # Default to True for backward compatibility if not provided
    if internal_marks is None:
        internal_marks = True
    # Normalize to boolean
    if isinstance(internal_marks, str):
        internal_marks = internal_marks.strip().lower() in ["1", "true", "yes"]
    internal_marks = bool(internal_marks)

    doc = {
        "exam_name": exam_name,
        "session": session,
        "exam_time": exam_time,
        "total_marks": int(total_marks),
        "internal_marks": internal_marks,
        "created_at": datetime.utcnow()
    }
    res = exams_col.insert_one(doc)
    return jsonify({"success": True, "exam_id": str(res.inserted_id)})

# ---------------------------
# Delete an exam completely (and its datesheet + papers)
# ---------------------------
@app.route("/exam/delete/<exam_id>", methods=["DELETE"])
def delete_exam(exam_id):
    try:
        # support passing numeric id if user previously used numeric - but we store ObjectId now
        obj = ObjectId(exam_id)
    except Exception:
        return jsonify({"success": False, "message": "Invalid exam id"}), 400

    exam = exams_col.find_one({"_id": obj})
    if not exam:
        return jsonify({"success": False, "message": "Exam not found"}), 404

    # remove exam document
    exams_col.delete_one({"_id": obj})

    # remove datesheet entries tied to exam_name + session
    datesheet_col.delete_many({"exam_name": exam.get("exam_name"), "session": exam.get("session")})

    # remove uploaded papers folder
    folder_path = os.path.join(PAPER_DIR, exam.get("session", ""), exam.get("exam_name", ""))
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)

    return jsonify({"success": True, "message": "Exam deleted successfully"})

# ---------------------------
# List all exams
# ---------------------------
@app.route("/exam/list-all")
def list_all_exams():
    try:
        rows = []
        for ex in exams_col.find().sort("created_at", ASCENDING):
            rows.append({
                "exam_id": str(ex.get("_id")),
                "exam_name": ex.get("exam_name"),
                "session": ex.get("session"),
                "exam_time": ex.get("exam_time"),
                "total_marks": ex.get("total_marks"),
                "internal_marks": ex.get("internal_marks", False)
            })
        return jsonify({"success": True, "exams": rows})
    except Exception as e:
        return jsonify({"success": False, "message": str(e), "exams": []})
# PART 2/4
# ---------------------------
# Add subjects for a class
# ---------------------------
@app.route("/exam/subjects/add", methods=["POST"])
def add_subjects():
    data = request.json or {}
    session = data.get("session")
    class_name = data.get("class_name")
    subjects = data.get("subjects", [])

    if not session or not class_name or not subjects:
        return jsonify({"success": False, "message": "Missing fields"}), 400

    # delete existing subjects for session+class (same behavior as sqlite version)
    exam_subjects_col.delete_many({"session": session, "class_name": class_name})

    # insert provided subjects
    to_insert = []
    for s in subjects:
        to_insert.append({
            "session": session,
            "class_name": class_name,
            "subject": s
        })
    if to_insert:
        try:
            exam_subjects_col.insert_many(to_insert, ordered=False)
        except Exception:
            # ignore duplicates or errors — ordered=False avoids stopping on first duplicate
            pass

    return jsonify({"success": True})

# ---------------------------
# Get subjects for a class
# ---------------------------
@app.route("/exam/subjects/get")
def get_subjects():
    session = request.args.get("session")
    class_name = request.args.get("class_name")
    if not session or not class_name:
        return jsonify({"success": False, "message": "Missing parameters", "subjects": []}), 400
    subs = [r.get("subject") for r in exam_subjects_col.find({"session": session, "class_name": class_name})]
    return jsonify({"success": True, "subjects": subs})

# ---------------------------
# Add exam schedule / datesheet
# ---------------------------
@app.route("/exam/add-datesheet", methods=["POST"])
def add_datesheet():
    data = request.get_json() or {}
    session = data.get("session")
    class_name = data.get("class_name")
    exam_name = data.get("exam_name")
    datesheet = data.get("datesheet", [])

    if not session or not class_name or not exam_name or not datesheet:
        return jsonify({"success": False, "message": "Missing data"}), 400

    # delete existing entries for that session/class/exam
    datesheet_col.delete_many({"session": session, "class_name": class_name, "exam_name": exam_name})

    # insert each item
    to_insert = []
    for item in datesheet:
        doc = {
            "session": session,
            "class_name": class_name,
            "exam_name": exam_name,
            "subject": item.get("subject"),
            "date": item.get("date"),
            "total_marks": int(item.get("total_marks", 0)) if item.get("total_marks") is not None else 0,
            "duration": int(item.get("duration", 0)) if item.get("duration") is not None else 0
        }
        to_insert.append(doc)
    if to_insert:
        try:
            datesheet_col.insert_many(to_insert, ordered=False)
        except Exception:
            pass

    return jsonify({"success": True, "message": "Datesheet saved"})

# ---------------------------
# Get datesheet for class/exam/session (returns all subjects even if date missing)
# ---------------------------
@app.route("/exam/get-datesheet", methods=["GET"])
def get_datesheet():
    class_name = request.args.get("class_name")
    session = request.args.get("session")
    exam_name = request.args.get("exam_name")

    if not class_name or not session or not exam_name:
        return jsonify({"success": False, "message": "Missing parameters"}), 400

    # 1) Fetch exam info (time, total marks)
    exam_doc = exams_col.find_one({"exam_name": exam_name, "session": session})
    if not exam_doc:
        return jsonify({"success": False, "message": "Exam not found"}), 404

    exam_time = exam_doc.get("exam_time", "")
    total_marks = exam_doc.get("total_marks", "")

    # 2) Fetch subjects for class + session
    subjects = [row.get("subject") for row in exam_subjects_col.find({
        "class_name": class_name,
        "session": session
    })]

    # 3) Fetch datesheet entries
    ds_cursor = datesheet_col.find({
        "class_name": class_name,
        "session": session,
        "exam_name": exam_name
    })
    date_map = {d.get("subject"): d.get("date") for d in ds_cursor}

    # 4) Create final output
    final = []
    for sub in subjects:
        final.append({
            "subject": sub,
            "date": date_map.get(sub, ""),   # individual date
            "total_marks": total_marks,       # same for all subjects
            "duration": exam_time             # same for all subjects
        })

    return jsonify({"success": True, "datesheet": final})

@app.route("/portal/student/<student_id>", methods=["GET"])
def portal_get_student(student_id):
    try:
        student = students_col.find_one({"_id": ObjectId(student_id)})
        if not student:
            return jsonify({"success": False, "message": "Student not found"}), 404

        access = get_student_access_flags(student)
        # Roll number should remain visible in student portal; release flags
        # still control hall ticket/result access separately.
        roll_value = student.get("rollno", "")

        return jsonify({
            "success": True,
            "student": {
                "id": str(student["_id"]),
                "name": student.get("student_name"),
                "admission_no": student.get("admission_no", ""),
                "class_name": student.get("class_name"),
                "section": student.get("section"),
                "roll": roll_value,
                "photo_url": student.get("photo_url", ""),
                "session": student.get("session"),
                "father_name": student.get("father_name", ""),
                "eligible": access.get("eligible", False),
                "release_rollno": access.get("release_rollno", False),
                "release_result": access.get("release_result", False)
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/portal/students", methods=["GET"])
def portal_list_students():
    students = []
    for s in students_col.find():
        access = get_student_access_flags(s)
        roll_value = s.get("rollno", "")
        students.append({
            "id": str(s["_id"]),
            "name": s.get("student_name"),
            "admission_no": s.get("admission_no", ""),
            "class_name": s.get("class_name"),
            "section": s.get("section"),
            "roll": roll_value,
            "photo_url": s.get("photo_url", ""),
            "session": s.get("session"),
            "eligible": access.get("eligible", False),
            "release_rollno": access.get("release_rollno", False),
            "release_result": access.get("release_result", False)
        })
    return jsonify({"success": True, "students": students})

@app.route("/student-access/list", methods=["GET"])
def student_access_list():
    session = request.args.get("session")
    class_name = request.args.get("class_name")
    if not session or not class_name:
        return jsonify({"success": False, "message": "Missing parameters", "students": []}), 400

    sessions = session_variants(session)

    students = list(students_col.find({
        "session": {"$in": sessions},
        "class_name": class_name
    }))
    if not students:
        students = list(students_col.find({"class_name": class_name}))

    setting_docs = list(student_access_col.find({
        "session": {"$in": sessions},
        "class_name": class_name
    }))
    setting_map = {}
    for doc in setting_docs:
        sid = doc.get("student_id")
        if sid and sid not in setting_map:
            setting_map[sid] = doc

    def roll_num(s):
        try:
            return int(str(s.get("rollno", "")).strip())
        except Exception:
            return 10**9

    students.sort(key=roll_num)
    out = []
    for s in students:
        sid = str(s.get("_id"))
        setting = setting_map.get(sid, {})

        eligible = to_bool(setting.get("eligible"), False)
        release_rollno = to_bool(setting.get("release_rollno"), False)
        release_result = to_bool(setting.get("release_result"), False)
        if not eligible:
            release_rollno = False
            release_result = False

        out.append({
            "student_id": sid,
            "name": s.get("student_name", ""),
            "father_name": s.get("father_name", ""),
            "class_name": s.get("class_name", ""),
            "rollno": s.get("rollno", ""),
            "eligible": eligible,
            "release_rollno": release_rollno,
            "release_result": release_result
        })

    return jsonify({"success": True, "students": out})

@app.route("/student-access/save", methods=["POST"])
def student_access_save():
    data = request.get_json() or {}
    session = data.get("session")
    class_name = data.get("class_name")
    students = data.get("students", [])

    if not session or not class_name or not isinstance(students, list):
        return jsonify({"success": False, "message": "Missing data"}), 400

    sessions = session_variants(session)
    saved = 0
    for row in students:
        sid = str(row.get("student_id", "")).strip()
        if not sid:
            continue

        eligible = to_bool(row.get("eligible"), False)
        release_rollno = to_bool(row.get("release_rollno"), False)
        release_result = to_bool(row.get("release_result"), False)
        if not eligible:
            release_rollno = False
            release_result = False

        for sess in sessions:
            student_access_col.update_one(
                {"session": sess, "class_name": class_name, "student_id": sid},
                {"$set": {
                    "eligible": eligible,
                    "release_rollno": release_rollno,
                    "release_result": release_result,
                    "updated_at": datetime.utcnow()
                }},
                upsert=True
            )
        saved += 1

    return jsonify({"success": True, "message": "Student access settings saved", "saved": saved})

# ---------------------------
# debug datesheet (show collection's keys info)
# ---------------------------
@app.route("/debug/datesheet")
def debug_datesheet():
    # not a direct schema function in Mongo - we show sample doc & count
    sample = datesheet_col.find_one() or {}
    count = datesheet_col.count_documents({})
    return {"sample_keys": list(sample.keys()), "count": count}

# ---------------------------
# Upload exam paper (files)
# ---------------------------
@app.route("/exam/upload-paper", methods=["POST"])
def upload_paper():
    session = request.form.get("session")
    class_name = request.form.get("class_name")
    exam_name = request.form.get("exam_name")
    subject = request.form.get("subject")
    file = request.files.get("pdf")

    if not all([session, class_name, exam_name, subject, file]):
        return jsonify({"success": False, "message": "Missing data"}), 400

    try:
        folder = os.path.join(PAPER_DIR, session, exam_name, class_name)
        os.makedirs(folder, exist_ok=True)
        filepath = os.path.join(folder, f"{subject}.pdf")
        file.save(filepath)
        return jsonify({"success": True, "message": "Paper uploaded successfully"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ---------------------------
# Delete uploaded exam paper
# ---------------------------
@app.route("/exam/delete-paper", methods=["DELETE"])
def delete_paper():
    data = request.get_json() or {}
    session = data.get("session")
    class_name = data.get("class_name")
    exam_name = data.get("exam_name")
    subject = data.get("subject")

    if not all([session, class_name, exam_name, subject]):
        return jsonify({"success": False, "message": "Missing parameters"}), 400

    try:
        filepath = os.path.join(PAPER_DIR, session, exam_name, class_name, f"{subject}.pdf")
        if os.path.exists(filepath):
            os.remove(filepath)
            return jsonify({"success": True, "message": "Paper deleted successfully"})
        else:
            return jsonify({"success": False, "message": "Paper not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ---------------------------
# Get/View PDF
# ---------------------------
@app.route("/exam/get-paper")
def get_paper():
    session = request.args.get("session")
    class_name = request.args.get("class_name")
    exam_name = request.args.get("exam_name")
    subject = request.args.get("subject")

    if not all([session, class_name, exam_name, subject]):
        return "Missing parameters", 400

    filepath = os.path.join(PAPER_DIR, session, exam_name, class_name, f"{subject}.pdf")
    if os.path.exists(filepath):
        return send_file(filepath)
    else:
        return "Paper not found", 404

# ---------------------------
# class incharge set / get
# ---------------------------
@app.route("/incharge/set", methods=["POST"])
def set_incharge():
    data = request.json or {}
    session = data.get("session")
    class_name = data.get("class_name")
    incharge = data.get("incharge")

    if not session or not class_name or not incharge:
        return jsonify({"success": False, "message": "Missing fields"}), 400

    class_incharge_col.update_one(
        {"session": session, "class_name": class_name},
        {"$set": {"incharge": incharge}},
        upsert=True
    )
    return jsonify({"success": True, "message": "Incharge saved"})

@app.route("/incharge/get", methods=["GET"])
def get_incharge():
    session = request.args.get("session")
    if not session:
        return jsonify({"success": False, "message": "Missing session"}), 400

    rows = class_incharge_col.find({"session": session})
    data = {r.get("class_name"): r.get("incharge") for r in rows}
    return jsonify({"success": True, "incharge": data})

# ---------------------------
# Add marks for students (upsert)
# ---------------------------
@app.route("/exam/add-marks", methods=["POST"])
def add_marks():
    data = request.get_json() or {}
    session = data.get("session")
    class_name = data.get("class_name")
    exam_name = data.get("exam_name")
    marks_list = data.get("marks", [])

    if not session or not class_name or not exam_name or not marks_list:
        return jsonify({"success": False, "message": "Missing data"}), 400

    # find exam id (we store as ObjectId)
    exam_doc = exams_col.find_one({"exam_name": exam_name, "session": session})
    if not exam_doc:
        return jsonify({"success": False, "message": "Exam not found"}), 404
    exam_id = exam_doc.get("_id")

    # upsert each mark
    for item in marks_list:
        roll = item.get("roll")
        subject = item.get("subject")
        marks_value = item.get("marks")
        if roll is None or subject is None or marks_value is None:
            continue
        exam_marks_col.update_one(
            {"session": session, "exam_id": exam_id, "class_name": class_name, "subject": subject, "roll": roll},
            {"$set": {"marks": int(marks_value)}},
            upsert=True
        )

    return jsonify({"success": True, "message": "Marks added/updated successfully"})

# ---------------------------
# Get marks
# ---------------------------
@app.route("/exam/get-marks")
def get_marks():
    session = request.args.get("session")
    class_name = request.args.get("class_name")
    exam_name = request.args.get("exam_name")

    if not session or not class_name or not exam_name:
        return jsonify({"success": False, "message": "Missing parameters"}), 400

    exam_doc = exams_col.find_one({"exam_name": exam_name, "session": session})
    if not exam_doc:
        return jsonify({"success": False, "message": "Exam not found"}), 404
    exam_id = exam_doc.get("_id")

    cursor = exam_marks_col.find({"session": session, "class_name": class_name, "exam_id": exam_id})
    marks = []
    for row in cursor:
        marks.append({
            "student_id": row.get("roll"),
            "roll": row.get("roll"),
            "subject": row.get("subject"),
            "marks": row.get("marks")
        })
    return jsonify({"success": True, "marks": marks})

# ---------------------------
# Save internal marks (upsert)
# ---------------------------
@app.route("/internal-marks/save", methods=["POST"])
def save_internal_marks():
    data = request.get_json() or {}
    session = data.get("session")
    class_name = data.get("class_name")
    subject = data.get("subject")
    exam_name = data.get("exam_name")
    marks_list = data.get("marks", [])
    teacher_id = data.get("teacher_id", "")

    if not session or not class_name or not subject or not exam_name or not marks_list:
        return jsonify({"success": False, "message": "Missing data"}), 400

    try:
        for item in marks_list:
            student_id = item.get("student_id")
            student_name = item.get("student_name")
            marks_value = item.get("marks")
            if not student_id or not student_name or marks_value is None:
                continue

            internal_marks_col.update_one(
                {
                    "session": session,
                    "class_name": class_name,
                    "subject": subject,
                    "exam_name": exam_name,
                    "student_id": student_id
                },
                {
                    "$set": {
                        "student_name": student_name,
                        "marks": int(marks_value),
                        "teacher_id": teacher_id,
                        "exam_name": exam_name,
                        "updated_at": datetime.utcnow()
                    }
                },
                upsert=True
            )
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Failed to save internal marks: {str(e)}"
        }), 500

    return jsonify({"success": True, "message": "Internal marks saved successfully"})

# ---------------------------
# Get internal marks
# ---------------------------
@app.route("/internal-marks/list", methods=["GET"])
def list_internal_marks():
    session = request.args.get("session")
    class_name = request.args.get("class_name")
    subject = request.args.get("subject")
    exam_name = request.args.get("exam_name")

    if not session or not class_name or not subject or not exam_name:
        return jsonify({"success": False, "message": "Missing parameters", "marks": []}), 400

    cursor = internal_marks_col.find({
        "session": session,
        "class_name": class_name,
        "subject": subject,
        "exam_name": exam_name
    }).sort("student_name", ASCENDING)

    marks = []
    for row in cursor:
        marks.append({
            "student_id": row.get("student_id"),
            "student_name": row.get("student_name"),
            "marks": row.get("marks"),
            "teacher_id": row.get("teacher_id", "")
        })

    return jsonify({"success": True, "marks": marks})

# ---------------------------
# Get internal marks subjects for class+session+exam
# ---------------------------
@app.route("/internal-marks/subjects", methods=["GET"])
def list_internal_subjects():
    session = request.args.get("session")
    class_name = request.args.get("class_name")
    exam_name = request.args.get("exam_name")

    if not session or not class_name or not exam_name:
        return jsonify({"success": False, "message": "Missing parameters", "subjects": []}), 400

    try:
        subjects = internal_marks_col.distinct("subject", {
            "session": session,
            "class_name": class_name,
            "exam_name": exam_name
        })
        subjects = [s for s in subjects if s]
        return jsonify({"success": True, "subjects": subjects})
    except Exception as e:
        return jsonify({"success": False, "message": str(e), "subjects": []}), 500

# ---------------------------
# Publish/Unpublish Results
# ---------------------------
@app.route("/result/publish", methods=["POST"])
def publish_result():
    data = request.get_json() or {}
    session = data.get("session")
    class_name = data.get("class_name")
    exam_name = data.get("exam_name")
    published = data.get("published")

    if not session or not class_name or not exam_name or published is None:
        return jsonify({"success": False, "message": "Missing data"}), 400

    if isinstance(published, str):
        published = published.strip().lower() in ["1", "true", "yes"]
    published = bool(published)

    result_publish_col.update_one(
        {"session": session, "class_name": class_name, "exam_name": exam_name},
        {"$set": {"published": published, "updated_at": datetime.utcnow()}},
        upsert=True
    )
    return jsonify({"success": True, "published": published})

@app.route("/result/status", methods=["GET"])
def result_status():
    session = request.args.get("session")
    class_name = request.args.get("class_name")
    exam_name = request.args.get("exam_name")

    if not session or not class_name or not exam_name:
        return jsonify({"success": False, "message": "Missing parameters"}), 400

    doc = result_publish_col.find_one({
        "session": session,
        "class_name": class_name,
        "exam_name": exam_name
    })
    published = bool(doc.get("published")) if doc else False
    return jsonify({"success": True, "published": published})

# ---------------------------
# Save internal marks config (max/weightage) per subject
# ---------------------------
@app.route("/internal-config/save", methods=["POST"])
def save_internal_config():
    data = request.get_json() or {}
    session = data.get("session")
    class_name = data.get("class_name")
    subject = data.get("subject")
    max_marks = data.get("max_marks")
    weightage = data.get("weightage")

    if not session or not class_name or not subject:
        return jsonify({"success": False, "message": "Missing data"}), 400

    if max_marks is None and weightage is None:
        return jsonify({"success": False, "message": "Provide max_marks or weightage"}), 400

    update = {"updated_at": datetime.utcnow()}
    if max_marks is not None:
        update["max_marks"] = float(max_marks)
    if weightage is not None:
        update["weightage"] = float(weightage)

    internal_config_col.update_one(
        {"session": session, "class_name": class_name, "subject": subject},
        {"$set": update},
        upsert=True
    )

    return jsonify({"success": True, "message": "Internal config saved"})

# ---------------------------
# Get internal marks config per subject
# ---------------------------
@app.route("/internal-config/get", methods=["GET"])
def get_internal_config():
    session = request.args.get("session")
    class_name = request.args.get("class_name")
    subject = request.args.get("subject")

    if not session or not class_name or not subject:
        return jsonify({"success": False, "message": "Missing parameters"}), 400

    doc = internal_config_col.find_one({
        "session": session,
        "class_name": class_name,
        "subject": subject
    })

    if not doc:
        return jsonify({
            "success": True,
            "config": {"session": session, "class_name": class_name, "subject": subject}
        })

    return jsonify({
        "success": True,
        "config": {
            "session": session,
            "class_name": class_name,
            "subject": subject,
            "max_marks": doc.get("max_marks"),
            "weightage": doc.get("weightage")
        }
    })

# ---------------------------
# Save per-exam subject marks config (external/internal)
# ---------------------------
@app.route("/exam/subject-config/save", methods=["POST"])
def save_exam_subject_config():
    data = request.get_json() or {}
    session = data.get("session")
    class_name = data.get("class_name")
    exam_name = data.get("exam_name")
    subject = data.get("subject")
    external_max_marks = data.get("external_max_marks")
    internal_max_marks = data.get("internal_max_marks")

    if not session or not class_name or not exam_name or not subject:
        return jsonify({"success": False, "message": "Missing data"}), 400

    if external_max_marks is None and internal_max_marks is None:
        return jsonify({"success": False, "message": "Provide external_max_marks or internal_max_marks"}), 400

    update = {"updated_at": datetime.utcnow()}
    if external_max_marks is not None:
        update["external_max_marks"] = float(external_max_marks)
    if internal_max_marks is not None:
        update["internal_max_marks"] = float(internal_max_marks)

    exam_subject_config_col.update_one(
        {
            "session": session,
            "class_name": class_name,
            "exam_name": exam_name,
            "subject": subject
        },
        {"$set": update},
        upsert=True
    )

    # Keep existing internal-config flow working with latest entered internal max.
    if internal_max_marks is not None:
        internal_config_col.update_one(
            {"session": session, "class_name": class_name, "subject": subject},
            {"$set": {"max_marks": float(internal_max_marks), "updated_at": datetime.utcnow()}},
            upsert=True
        )

    return jsonify({"success": True, "message": "Subject marks config saved"})

# ---------------------------
# Get per-exam subject marks config
# ---------------------------
@app.route("/exam/subject-config/get", methods=["GET"])
def get_exam_subject_config():
    session = request.args.get("session")
    class_name = request.args.get("class_name")
    exam_name = request.args.get("exam_name")
    subject = request.args.get("subject")

    if not session or not class_name or not exam_name or not subject:
        return jsonify({"success": False, "message": "Missing parameters"}), 400

    doc = exam_subject_config_col.find_one({
        "session": session,
        "class_name": class_name,
        "exam_name": exam_name,
        "subject": subject
    })

    # Fallbacks for backward compatibility
    exam_doc = exams_col.find_one({"session": session, "exam_name": exam_name}) or {}
    internal_doc = internal_config_col.find_one({
        "session": session,
        "class_name": class_name,
        "subject": subject
    }) or {}

    external_fallback = exam_doc.get("total_marks")
    internal_fallback = internal_doc.get("max_marks")

    return jsonify({
        "success": True,
        "config": {
            "session": session,
            "class_name": class_name,
            "exam_name": exam_name,
            "subject": subject,
            "external_max_marks": (doc.get("external_max_marks") if doc else external_fallback),
            "internal_max_marks": (doc.get("internal_max_marks") if doc else internal_fallback)
        }
    })
# PART 3/4
# ---------------------------
# Get exam details by session + exam name
# (keeps the same URL structure as your sqlite version)
# ---------------------------
@app.route("/exam/get/<session>/<path:exam_name>")
def get_exam_details(session, exam_name):
    try:
        exam_name_clean = exam_name.replace("%20", " ")
        exam = exams_col.find_one({"session": session, "exam_name": exam_name_clean})
        if not exam:
            return jsonify({"success": False, "message": "Exam not found"}), 404
        exam_out = {
            "exam_id": str(exam.get("_id")),
            "exam_name": exam.get("exam_name"),
            "session": exam.get("session"),
            "exam_time": exam.get("exam_time"),
            "total_marks": exam.get("total_marks")
        }
        return jsonify({"success": True, "exam": exam_out})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ---------------------------
# Teacher add / list / delete
# ---------------------------
@app.route("/teacher/add", methods=["POST"])
def add_teacher():
    data = request.json or {}
    session = data.get("session")
    username = str(data.get("username", "")).strip().upper()
    password = data.get("password")
    name = data.get("name")
    req_teacher_id = str(data.get("teacher_id", "")).strip()

    # Check missing fields
    if not session or not username or not password or not name:
        return jsonify({"success": False, "message": "Missing fields"}), 400

    # Check if username already exists
    if teachers_col.find_one({"username": username, "session": session}):
        return jsonify({"success": False, "message": "Username already exists"}), 400

    # -----------------------------
    # USE PROVIDED TEACHER ID (IF VALID) ELSE AUTO-GENERATE
    # -----------------------------
    new_teacher_id = ""
    if req_teacher_id and req_teacher_id.isdigit() and len(req_teacher_id) == 4:
        exists = teachers_col.find_one({"session": session, "teacher_id": req_teacher_id})
        if not exists:
            new_teacher_id = req_teacher_id

    if not new_teacher_id:
        last_teacher = teachers_col.find({"session": session}).sort("teacher_id", -1).limit(1)
        last_id = 0
        last_teacher = list(last_teacher)
        if last_teacher:
            try:
                last_id = int(last_teacher[0]["teacher_id"])
            except:
                last_id = 0
        new_teacher_id = f"{last_id + 1:04d}"  # e.g., "0001"

    # -----------------------------
    # INSERT TEACHER
    # -----------------------------
    try:
        teachers_col.insert_one({
            "teacher_id": new_teacher_id,
            "session": session,
            "username": username,
            "password": password,
            "name": name
        })

        return jsonify({
            "success": True,
            "message": "Teacher added successfully",
            "teacher_id": new_teacher_id
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/teacher/list")
def list_teachers():
    session = request.args.get("session")
    if not session:
        return jsonify({"success": False, "message": "Missing session"}), 400
    rows = []
    for r in teachers_col.find({"session": session}):
        rows.append({"id": str(r.get("_id")), "username": r.get("username"), "name": r.get("name")})
    return jsonify({"success": True, "teachers": rows})

@app.route("/teacher/delete/<teacher_id>", methods=["DELETE"])
def delete_teacher(teacher_id):
    try:
        obj = ObjectId(teacher_id)
    except:
        obj = None

    # 1) Delete teacher
    try:
        teachers_col.delete_one({"_id": ObjectId(teacher_id)})
    except:
        teachers_col.delete_one({"teacher_id": teacher_id})

    # 2) Delete ALL timetable rows (string or ObjectId stored)
    delete_filter = {"$or": [
        {"teacher_id": teacher_id},      # stored as string
        {"teacher_id": str(teacher_id)}, # also string (safety)
    ]}

    if obj:
        delete_filter["$or"].append({"teacher_id": obj})  # stored as ObjectId

    timetable_col.delete_many(delete_filter)

    return jsonify({"success": True, "message": "Teacher and full timetable deleted"})

@app.route("/teacher/<teacher_id>", methods=["GET"])
def get_teacher(teacher_id):
    teacher = None

    # Try MongoDB ObjectId first
    try:
        obj_id = ObjectId(teacher_id)
        teacher = teachers_col.find_one({"_id": obj_id})
    except:
        pass

    # If not found, try custom teacher_id (your 4-digit ID)
    if not teacher:
        teacher = teachers_col.find_one({"teacher_id": teacher_id})

    if teacher:
        return jsonify({
            "id": str(teacher.get("_id")),
            "teacher_id": teacher.get("teacher_id"),
            "name": teacher.get("name"),
            "username": teacher.get("username"),
            "session": teacher.get("session")
        })
    else:
        return jsonify({"error": "Teacher not found"}), 404

@app.route("/teacher/update/<teacher_id>", methods=["PUT"])
def update_teacher(teacher_id):
    data = request.json or {}
    name = str(data.get("name", "")).strip()
    username = str(data.get("username", "")).strip().upper()
    password = str(data.get("password", "")).strip()
    session = str(data.get("session", "")).strip()

    teacher = None
    try:
        teacher = teachers_col.find_one({"_id": ObjectId(teacher_id)})
    except Exception:
        teacher = None

    if not teacher:
        teacher = teachers_col.find_one({"teacher_id": teacher_id})

    if not teacher:
        return jsonify({"success": False, "message": "Teacher not found"}), 404

    if not session:
        session = teacher.get("session")

    if not name or not username:
        return jsonify({"success": False, "message": "Name and username are required"}), 400

    # Check username uniqueness within session (exclude current teacher)
    existing = teachers_col.find_one({
        "username": username,
        "session": session,
        "_id": {"$ne": teacher["_id"]}
    })
    if existing:
        return jsonify({"success": False, "message": "Username already exists"}), 400

    update_doc = {"name": name, "username": username}
    if password:
        update_doc["password"] = password

    teachers_col.update_one({"_id": teacher["_id"]}, {"$set": update_doc})
    return jsonify({"success": True, "message": "Teacher updated"})

@app.route("/teacher/reset-password/<teacher_id>", methods=["PUT"])
def reset_teacher_password(teacher_id):
    data = request.json or {}
    new_password = str(data.get("password", "")).strip()

    if not new_password:
        return jsonify({"success": False, "message": "Password is required"}), 400

    teacher = None
    try:
        teacher = teachers_col.find_one({"_id": ObjectId(teacher_id)})
    except Exception:
        teacher = None

    if not teacher:
        teacher = teachers_col.find_one({"teacher_id": teacher_id})

    if not teacher:
        return jsonify({"success": False, "message": "Teacher not found"}), 404

    teachers_col.update_one({"_id": teacher["_id"]}, {"$set": {"password": new_password}})
    return jsonify({"success": True, "message": "Password updated"})

@app.route("/teacher/reset-password", methods=["PUT"])
def reset_teacher_password_by_identity():
    data = request.json or {}
    new_password = str(data.get("password", "")).strip()
    username = str(data.get("username", "")).strip().upper()
    session = str(data.get("session", "")).strip()
    teacher_id = str(data.get("teacher_id", "")).strip()

    if not new_password:
        return jsonify({"success": False, "message": "Password is required"}), 400

    teacher = None
    if username and session:
        teacher = teachers_col.find_one({"username": username, "session": session})
    if (not teacher) and teacher_id:
        teacher = teachers_col.find_one({"teacher_id": teacher_id})
    if (not teacher) and username:
        teacher = teachers_col.find_one({"username": username})

    if not teacher:
        return jsonify({"success": False, "message": "Teacher not found"}), 404

    teachers_col.update_one({"_id": teacher["_id"]}, {"$set": {"password": new_password}})
    return jsonify({"success": True, "message": "Password updated"})

@app.route("/auth/otp/request", methods=["POST"])
def request_login_otp():
    data = request.json or {}
    username = str(data.get("username", "")).strip().upper()

    if username not in SPECIAL_OTP_USERS:
        return jsonify({"success": False, "message": "OTP not enabled for this user"}), 400

    cfg = get_otp_config_status(username)
    to_email = get_special_user_email(username)
    if not cfg["ok"]:
        return jsonify({
            "success": False,
            "message": "OTP config missing",
            "debug": cfg
        }), 400

    otp_code = f"{random.randint(0, 999999):06d}"
    expires_at = datetime.utcnow() + timedelta(minutes=5)
    OTP_STORE[username] = {
        "otp": otp_code,
        "expires_at": expires_at,
        "attempts": 0
    }

    sent, err = send_otp_email(to_email, otp_code, username)
    if not sent:
        return jsonify({
            "success": False,
            "message": f"OTP send failed: {err}",
            "debug": cfg
        }), 500

    return jsonify({
        "success": True,
        "message": f"OTP sent to {mask_email(to_email)}"
    })

@app.route("/auth/otp/config-check", methods=["GET"])
def otp_config_check():
    username = str(request.args.get("username", "")).strip().upper()
    if not username:
        return jsonify({"success": False, "message": "username query param is required"}), 400
    if username not in SPECIAL_OTP_USERS:
        return jsonify({"success": False, "message": "OTP not enabled for this user"}), 400
    return jsonify({"success": True, "config": get_otp_config_status(username)})

@app.route("/auth/otp/verify", methods=["POST"])
def verify_login_otp():
    data = request.json or {}
    username = str(data.get("username", "")).strip().upper()
    otp = str(data.get("otp", "")).strip()

    rec = OTP_STORE.get(username)
    if not rec:
        return jsonify({"success": False, "message": "OTP not requested"}), 400

    if datetime.utcnow() > rec["expires_at"]:
        OTP_STORE.pop(username, None)
        return jsonify({"success": False, "message": "OTP expired"}), 400

    if rec.get("attempts", 0) >= 5:
        OTP_STORE.pop(username, None)
        return jsonify({"success": False, "message": "Too many invalid attempts"}), 429

    if otp != rec.get("otp"):
        rec["attempts"] = rec.get("attempts", 0) + 1
        OTP_STORE[username] = rec
        return jsonify({"success": False, "message": "Invalid OTP"}), 401

    OTP_STORE.pop(username, None)
    return jsonify({"success": True, "message": "OTP verified"})

@app.route("/teacher/auth/otp/request", methods=["POST"])
def request_teacher_login_otp():
    data = request.json or {}
    username = str(data.get("username", "")).strip().upper()

    if not username:
        return jsonify({"success": False, "message": "username is required"}), 400

    teacher, profile, teacher_payload = get_teacher_profile_payload(username)
    if not teacher:
        return jsonify({"success": False, "message": "Teacher not found"}), 404

    mobile = str(profile.get("mobile", "")).strip()
    if not mobile:
        return jsonify({
            "success": False,
            "message": "Teacher mobile number not found in backend",
            "teacher": teacher_payload
        }), 400

    otp_code = f"{random.randint(0, 999999):06d}"
    expires_at = datetime.utcnow() + timedelta(minutes=5)
    otp_key = f"TEACHER::{username}"
    OTP_STORE[otp_key] = {
        "otp": otp_code,
        "expires_at": expires_at,
        "attempts": 0,
        "username": username,
    }

    sent, err = send_textbee_otp(mobile, otp_code, profile.get("teacher_name", "Teacher"))
    if not sent:
        fallback_email = get_teacher_otp_email(username)
        if fallback_email:
            email_sent, email_err = send_otp_email(fallback_email, otp_code, username)
            if email_sent:
                return jsonify({
                    "success": True,
                    "message": f"OTP sent to email {mask_email(fallback_email)}",
                    "channel": "email",
                    "teacher": teacher_payload
                })
            err = f"{err}; email failed: {email_err}"
        return jsonify({
            "success": False,
            "message": f"OTP send failed: {err}",
            "teacher": teacher_payload
        }), 500

    return jsonify({
        "success": True,
        "message": f"OTP sent to {mask_mobile(mobile)}",
        "channel": "sms",
        "teacher": teacher_payload
    })

@app.route("/teacher/auth/otp/config-check", methods=["GET"])
def teacher_auth_otp_config_check():
    return jsonify({
        "success": True,
        "config": get_teacher_otp_config_status()
    })

@app.route("/teacher/auth/profile", methods=["GET"])
def teacher_auth_profile():
    username = str(request.args.get("username", "")).strip().upper()
    if not username:
        return jsonify({"success": False, "message": "username query param is required"}), 400
    teacher, profile, teacher_payload = get_teacher_profile_payload(username)
    if not teacher:
        return jsonify({"success": False, "message": "Teacher not found"}), 404
    return jsonify({"success": True, "teacher": teacher_payload})

@app.route("/teacher/auth/otp/verify", methods=["POST"])
def verify_teacher_login_otp():
    data = request.json or {}
    username = str(data.get("username", "")).strip().upper()
    otp = str(data.get("otp", "")).strip()
    otp_key = f"TEACHER::{username}"
    rec = OTP_STORE.get(otp_key)

    if not rec:
        return jsonify({"success": False, "message": "OTP not requested"}), 400

    if datetime.utcnow() > rec["expires_at"]:
        OTP_STORE.pop(otp_key, None)
        return jsonify({"success": False, "message": "OTP expired"}), 400

    if rec.get("attempts", 0) >= 5:
        OTP_STORE.pop(otp_key, None)
        return jsonify({"success": False, "message": "Too many invalid attempts"}), 429

    if otp != rec.get("otp"):
        rec["attempts"] = rec.get("attempts", 0) + 1
        OTP_STORE[otp_key] = rec
        return jsonify({"success": False, "message": "Invalid OTP"}), 401

    OTP_STORE.pop(otp_key, None)
    return jsonify({"success": True, "message": "OTP verified"})
# ---------------------------
# Login (admin + teacher)
# ---------------------------
@app.route("/login", methods=["POST"])
def login():
    data = request.json or {}
    username = str(data.get("username", "")).strip()
    username_upper = username.upper()
    password = data.get("password")

    if not username or not password:
        return jsonify({"success": False, "message": "Missing login details"}), 400

    # ---------- ADMIN LOGIN (HARD-CODED) ----------
    if username_upper == "ADMIN" and password == "PS*100":
        return jsonify({
            "success": True,
            "role": "admin",
            "token": "admin_token"
        })

    # ---------- PRINCIPAL LOGIN (HARD-CODED) ----------
    if username_upper == "PRINCIPAL" and password == "14112017":
        return jsonify({
            "success": True,
            "role": "principal",
            "token": "principal_token",
            "principal": {
                "name": "School Principal",
                "username": "principal"
            }
        })

    # ---------- TEACHER LOGIN (FROM DATABASE) ----------
    teacher = teachers_col.find_one({"username": username.upper()})
    if teacher:
        profile = find_student_teacher_profile(teacher)
        if teacher.get("password") == password or _teacher_password_matches(
            profile.get("teacher_name") or teacher.get("name"),
            profile.get("dob"),
            password,
            profile.get("teacher_code") or teacher.get("teacher_id") or teacher.get("teacher_code")
        ):
            return jsonify({
                "success": True,
                "role": "teacher",
                "token": f"teacher_{teacher.get('username')}_token",
                "teacher": {
                    "id": str(teacher.get("_id")),
                    "teacher_id": teacher.get("teacher_id", ""),
                    "name": teacher.get("name"),
                    "username": teacher.get("username"),
                    "session": teacher.get("session")
                }
            })
    # -------- STUDENT LOGIN (MONGODB) --------
    student = students_col.find_one({
        "admission_no": username,
        "dob": password   # OR change to roll / mobile if needed
    })

    if student:
        access = get_student_access_flags(student)
        roll_value = student.get("rollno", "")
        return jsonify({
            "success": True,
            "role": "student",
            "token": f"student_{username}_token",
            "student": {
                "id": str(student["_id"]),
                "name": student.get("student_name"),
                "admission_no": student.get("admission_no"),
                "rollno": roll_value,
                "class": student.get("class_name"),
                "section": student.get("section"),
                "session": student.get("session"),
                "photo_url": student.get("photo_url", ""),
                "eligible": access.get("eligible", False),
                "release_rollno": access.get("release_rollno", False),
                "release_result": access.get("release_result", False)
            }
        })
    # ---------- INVALID LOGIN ----------
    return jsonify({"success": False, "message": "Invalid username or password"}), 401
# ---------------------------
# Get timetable for a teacher
# ---------------------------
@app.route("/timetable/get")
def get_timetable():
    session = request.args.get("session")
    teacher_id = request.args.get("teacher_id")
    if not session or not teacher_id:
        return jsonify({"success": False, "message": "Missing parameters", "timetable": []}), 400

    # find docs
    rows = []
    cursor = timetable_col.find({"session": session, "teacher_id": teacher_id}).sort("period", ASCENDING)
    for r in cursor:
        # normalize document fields to expected keys
        rows.append({
            "period": r.get("period"),
            "Monday": r.get("monday", ""),
            "Tuesday": r.get("tuesday", ""),
            "Wednesday": r.get("wednesday", ""),
            "Thursday": r.get("thursday", ""),
            "Friday": r.get("friday", ""),
            "Saturday": r.get("saturday", ""),
            "class": r.get("class", ""),
            "startDay": int(r.get("startDay", 1)),
            "endDay": int(r.get("endDay", 1))
        })
    return jsonify({"success": True, "timetable": rows})

# ---------------------------
# Save timetable for a teacher (/timetable/set)
# ---------------------------
@app.route("/timetable/set", methods=["POST"])
def set_timetable():
    data = request.get_json() or {}
    session = data.get("session")
    teacher_id = data.get("teacher_id")
    timetable_list = data.get("timetable", [])

    if not session or not teacher_id:
        return jsonify({"success": False, "message": "Missing data"}), 400

    # delete existing entries for this teacher+session (mirror sqlite behavior)
    timetable_col.delete_many({"session": session, "teacher_id": teacher_id})

    # insert each row (supports multiple rows with same period if UI sends them)
    to_insert = []
    for periodData in timetable_list:
        # periodData expected keys: period, class, Monday..Saturday, startDay, endDay
        doc = {
            "session": session,
            "teacher_id": teacher_id,
            "period": int(periodData.get("period", 0)),
            "class": periodData.get("class", ""),
            "monday": periodData.get("Monday", "") or periodData.get("monday", ""),
            "tuesday": periodData.get("Tuesday", "") or periodData.get("tuesday", ""),
            "wednesday": periodData.get("Wednesday", "") or periodData.get("wednesday", ""),
            "thursday": periodData.get("Thursday", "") or periodData.get("thursday", ""),
            "friday": periodData.get("Friday", "") or periodData.get("friday", ""),
            "saturday": periodData.get("Saturday", "") or periodData.get("saturday", ""),
            "startDay": int(periodData.get("startDay", 1)),
            "endDay": int(periodData.get("endDay", 1))
        }
        # skip rows without class (keeps same behavior)
        if not doc["class"]:
            continue
        to_insert.append(doc)

    if to_insert:
        try:
            timetable_col.insert_many(to_insert, ordered=False)
        except Exception:
            pass

    return jsonify({"success": True, "message": "Timetable saved successfully"})
# PART 4/4
# ---------------------------
# CLASSWISE TIMETABLE (/timetable/classwise)
# ---------------------------
@app.route("/timetable/classwise")
def timetable_classwise():
    session = request.args.get("session")
    class_name = request.args.get("class_name")

    if not session or not class_name:
        return jsonify({
            "success": False,
            "message": "Missing parameters",
            "timetable": []
        }), 400

    # Fetch all rows for this class+session
    cursor = timetable_col.find(
        {"session": session, "class": class_name}
    ).sort("period", ASCENDING)

    output = []

    for row in cursor:

        # ------------- FETCH TEACHER NAME CORRECTLY -------------
        teacher_name = ""
        teacher_id = str(row.get("teacher_id", "")).strip()

        if teacher_id:
            # CASE 1: valid ObjectId
            if ObjectId.is_valid(teacher_id):
                t_doc = teachers_col.find_one({"_id": ObjectId(teacher_id)})
            else:
                # CASE 2: teacher_id stored as normal string
                t_doc = teachers_col.find_one({"teacher_id": teacher_id})

            if t_doc:
                teacher_name = t_doc.get("name", "")

        # ------------- BUILD WEEKDAY ENTRIES -------------
        output.append({
            "period": row.get("period"),
            "class": row.get("class"),

            "Monday": f"{teacher_name} - {row.get('monday')}" if row.get("monday") else "",
            "Tuesday": f"{teacher_name} - {row.get('tuesday')}" if row.get("tuesday") else "",
            "Wednesday": f"{teacher_name} - {row.get('wednesday')}" if row.get("wednesday") else "",
            "Thursday": f"{teacher_name} - {row.get('thursday')}" if row.get("thursday") else "",
            "Friday": f"{teacher_name} - {row.get('friday')}" if row.get("friday") else "",
            "Saturday": f"{teacher_name} - {row.get('saturday')}" if row.get("saturday") else "",

            "startDay": int(row.get("startDay", 1)),
            "endDay": int(row.get("endDay", 1))
        })

    return jsonify({"success": True, "timetable": output})

# ---------------------------
# Get used days for a class+period (/timetable/used_days)
# Returns list of weekday indexes used (1-based, Monday=1 .. Saturday=6)
# ---------------------------
@app.route("/timetable/used_days")
def get_used_days():
    session = request.args.get("session")
    class_name = request.args.get("class_name")
    period = request.args.get("period")
    try:
        period = int(period) if period is not None else 0
    except Exception:
        period = 0

    if not session or not class_name or not period:
        return jsonify({"success": False, "used_days": []}), 400

    cursor = timetable_col.find({"session": session, "class": class_name, "period": period})
    used_days = set()
    # weekday mapping as in sqlite code
    week_fields = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
    for r in cursor:
        for idx, fld in enumerate(week_fields):
            val = r.get(fld)
            if val and str(val).strip():
                used_days.add(idx + 1)
    return jsonify({"success": True, "used_days": sorted(list(used_days))})


# ---------------------------
# Get distinct teachers for a session timetable (/timetable/teachers)
# ---------------------------
@app.route("/timetable/teachers")
def get_timetable_teachers():
    session = request.args.get("session")
    if not session:
        return jsonify({"success": False, "teachers": []}), 400

    teacher_ids = timetable_col.distinct("teacher_id", {"session": session})
    teachers = []
    for tid in teacher_ids:
        name = ""
        try:
            if ObjectId.is_valid(str(tid)):
                t_doc = teachers_col.find_one({"_id": ObjectId(str(tid))})
            else:
                t_doc = teachers_col.find_one({"teacher_id": str(tid)})
            if t_doc:
                name = t_doc.get("name", "") or t_doc.get("teacher_name", "")
        except Exception:
            name = ""
        teachers.append({"id": str(tid), "name": name})

    teachers.sort(key=lambda t: (t.get("name") or "", t.get("id") or ""))
    return jsonify({"success": True, "teachers": teachers})


@app.route("/", methods=["GET"])
def home():
    return "Backend Running", 200

# ============================
# SESSION MANAGEMENT (CUSTOM)
# ============================

sessions_col = db["sessions"]  # create new collection

# ➤ ADD SESSION
@app.route("/session/add", methods=["POST"])
def add_session():
    data = request.json
    session = data.get("session")

    if not session:
        return jsonify({"success": False, "message": "Session required"}), 400

    # Check duplicate
    exists = sessions_col.find_one({"session": session})
    if exists:
        return jsonify({"success": False, "message": "Session already exists"}), 400

    sessions_col.insert_one({"session": session})
    return jsonify({"success": True, "message": "Session added"}), 200


# ➤ LIST SESSIONS
@app.route("/session/list", methods=["GET"])
def list_sessions():
    all_sessions = [s["session"] for s in sessions_col.find()]
    return jsonify({"success": True, "sessions": all_sessions}), 200


# ➤ DELETE SESSION
@app.route("/session/delete", methods=["POST"])
def delete_session():
    data = request.json
    session = data.get("session")

    if not session:
        return jsonify({"success": False, "message": "Session required"}), 400

    delete_result = sessions_col.delete_one({"session": session})

    if delete_result.deleted_count == 0:
        return jsonify({"success": False, "message": "Session not found"}), 404

    return jsonify({"success": True, "message": "Session deleted"}), 200

notices_col = db["notices"]

NOTICE_DIR = "notices"
os.makedirs(NOTICE_DIR, exist_ok=True)

@app.route("/notice/upload", methods=["POST"])
def upload_notice():
    title = request.form.get("title")
    description = request.form.get("description", "")
    date = request.form.get("date")        # YYYY-MM-DD
    target = request.form.get("target")    # student | teacher | both
    file = request.files.get("pdf")

    if not title or not date:
        return jsonify({"success": False, "message": "Title and date required"}), 400

    if target not in ["student", "teacher", "both"]:
        target = "student"  # safe default

    filename = None
    if file:
        filename = f"{int(datetime.utcnow().timestamp())}_{file.filename}"
        filepath = os.path.join(NOTICE_DIR, filename)
        file.save(filepath)

    res = notices_col.insert_one({
        "title": title,
        "description": description,
        "date": date,
        "target": target,                # ✅ STORED
        "file": filename,
        "uploaded_at": datetime.utcnow()
    })

    return jsonify({
        "success": True,
        "message": "Notice uploaded",
        "id": str(res.inserted_id)
    })
@app.route("/notice/list", methods=["GET"])
def list_notices():
    role = request.args.get("role")  # student | teacher | None

    query = {}
    if role in ["student", "teacher"]:
        query = {
            "target": {"$in": [role, "both"]}
        }

    notices = []
    for n in notices_col.find(query).sort("uploaded_at", -1):
        notices.append({
            "id": str(n["_id"]),
            "title": n.get("title"),
            "description": n.get("description"),
            "date": n.get("date"),
            "target": n.get("target", "student"),
            "file": n.get("file"),
            "url": f"/notice/get-file/{n.get('file')}" if n.get("file") else None
        })

    return jsonify({"success": True, "notices": notices})
@app.route("/notice/get-file/<filename>")
def get_notice_file(filename):
    filepath = os.path.join(NOTICE_DIR, filename)
    if os.path.exists(filepath):
        return send_file(filepath)
    return "File Not Found", 404
@app.route("/notice/delete/<notice_id>", methods=["DELETE"])
def delete_notice(notice_id):
    try:
        doc = notices_col.find_one({"_id": ObjectId(notice_id)})
        if not doc:
            return jsonify({"success": False, "message": "Notice not found"}), 404

        if doc.get("file"):
            filepath = os.path.join(NOTICE_DIR, doc["file"])
            if os.path.exists(filepath):
                os.remove(filepath)

        notices_col.delete_one({"_id": ObjectId(notice_id)})

        return jsonify({"success": True, "message": "Notice deleted"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ---------------------------
# Attendance collection
# ---------------------------
attendance_col = db["attendance"]

# ---------------------------
# Add or update attendance
# ---------------------------
@app.route("/attendance/save", methods=["POST"])
def save_attendance():
    """
    Payload Example:
    {
        "session": "2025-2026",
        "class_name": "Class 1",
        "date": "2025-12-08",
        "attendance": [
            {"student_id": "64fb...a", "status": "present"},
            {"student_id": "64fb...b", "status": "absent"},
            {"student_id": "64fb...c", "status": "leave"}
        ]
    }
    """
    data = request.json or {}
    session = data.get("session")
    class_name = data.get("class_name")
    date = data.get("date")
    attendance_list = data.get("attendance", [])

    if not session or not class_name or not date or not attendance_list:
        return jsonify({"success": False, "message": "Missing fields"}), 400

    # Remove old attendance for same class+date
    attendance_col.delete_many({"session": session, "class_name": class_name, "date": date})

    # Insert new attendance
    to_insert = []
    for att in attendance_list:
        student_id = normalize_student_id(att.get("student_id"))
        student_roll = att.get("student_roll")
        student_admission = att.get("student_admission")
        status = att.get("status")
        if student_id and status in ["present", "absent", "leave"]:
            to_insert.append({
                "session": session,
                "class_name": class_name,
                "date": date,
                "student_id": student_id,
                "student_roll": str(student_roll or "").strip(),
                "student_admission": str(student_admission or "").strip(),
                "status": status
            })

    if to_insert:
        attendance_col.insert_many(to_insert)

    return jsonify({"success": True, "message": "Attendance saved successfully"})

# ---------------------------
# Get attendance for class + date
# ---------------------------
@app.route("/attendance/list", methods=["GET"])
def list_attendance():
    """
    Query params:
        session=2025-2026
        class_name=Class 1
        date=2025-12-08
    """
    session = request.args.get("session")
    class_name = request.args.get("class_name")
    date = request.args.get("date")

    if not session or not class_name or not date:
        return jsonify({"success": False, "attendance": [], "message": "Missing parameters"}), 400

    cursor = attendance_col.find({"session": session, "class_name": class_name, "date": date})
    records = []
    for att in cursor:
        sid = normalize_student_id(att.get("student_id"))
        records.append({
            "student_id": sid,
            "student_roll": att.get("student_roll", ""),
            "student_admission": att.get("student_admission", ""),
            "status": att.get("status")
        })

    return jsonify({"success": True, "attendance": records})


# ---------------------------
# Get attendance for class + month (/attendance/list-monthly)
# ---------------------------
@app.route("/attendance/list-monthly", methods=["GET"])
def list_attendance_monthly():
    """
    Query params:
        session=2025_26
        class_name=Class 1
        month=2025-12
    """
    session = request.args.get("session")
    class_name = request.args.get("class_name")
    month = request.args.get("month")
    if not session or not class_name or not month:
        return jsonify({"success": False, "attendance": [], "message": "Missing parameters"}), 400

    sessions = session_variants(session)
    # match any date within the month
    month_prefix = str(month).strip()
    cursor = attendance_col.find({
        "session": {"$in": sessions} if sessions else session,
        "class_name": class_name,
        "date": {"$regex": f"^{month_prefix}-"}
    })
    records = []
    for att in cursor:
        sid = normalize_student_id(att.get("student_id"))
        records.append({
            "student_id": sid,
            "student_roll": att.get("student_roll", ""),
            "student_admission": att.get("student_admission", ""),
            "status": att.get("status"),
            "date": att.get("date", "")
        })
    return jsonify({"success": True, "attendance": records})


# ----------------------------
# Teacher Daily Work
# ----------------------------
@app.route("/teacher/daily-work/save", methods=["POST"])
def save_teacher_daily_work():
    data = request.get_json() or {}
    session = (data.get("session") or "").strip()
    class_name = (data.get("class_name") or "").strip()
    date = (data.get("date") or "").strip()
    teacher_id = str(data.get("teacher_id") or "").strip()
    teacher_name = (data.get("teacher_name") or "").strip()
    subject = (data.get("subject") or "").strip()
    work = (data.get("work") or "").strip()

    if not session or not class_name or not date or not teacher_id or not work:
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    payload = {
        "session": session,
        "class_name": class_name,
        "date": date,
        "teacher_id": teacher_id,
        "teacher_name": teacher_name,
        "subject": subject,
        "work": work,
        "updated_at": datetime.utcnow(),
    }
    teacher_daily_work_col.update_one(
        {"session": session, "class_name": class_name, "date": date, "teacher_id": teacher_id, "subject": subject},
        {"$set": payload, "$setOnInsert": {"created_at": datetime.utcnow()}},
        upsert=True,
    )
    return jsonify({"success": True, "message": "Work saved"})


@app.route("/teacher/daily-work/get", methods=["GET"])
def get_teacher_daily_work():
    session = (request.args.get("session") or "").strip()
    class_name = (request.args.get("class_name") or "").strip()
    date = (request.args.get("date") or "").strip()
    teacher_id = str(request.args.get("teacher_id") or "").strip()
    subject = (request.args.get("subject") or "").strip()
    if not session or not class_name or not date or not teacher_id or not subject:
        return jsonify({"success": False, "message": "Missing parameters"}), 400

    doc = teacher_daily_work_col.find_one(
        {"session": session, "class_name": class_name, "date": date, "teacher_id": teacher_id, "subject": subject},
        {"_id": 0}
    )
    return jsonify({"success": True, "work": doc})


@app.route("/teacher/daily-work/list", methods=["GET"])
def list_teacher_daily_work():
    session = (request.args.get("session") or "").strip()
    teacher_id = str(request.args.get("teacher_id") or "").strip()
    date = (request.args.get("date") or "").strip()
    class_name = (request.args.get("class_name") or "").strip()
    if not session or not teacher_id or not date:
        return jsonify({"success": False, "message": "Missing parameters"}), 400

    q = {"session": session, "teacher_id": teacher_id, "date": date}
    if class_name:
        q["class_name"] = class_name
    docs = list(teacher_daily_work_col.find(q, {"_id": 0}))
    return jsonify({"success": True, "rows": docs})


@app.route("/student/daily-work/list", methods=["GET"])
def list_student_daily_work():
    session = (request.args.get("session") or "").strip()
    class_name = (request.args.get("class_name") or "").strip()
    date = (request.args.get("date") or "").strip()
    if not session or not class_name or not date:
        return jsonify({"success": False, "message": "Missing parameters"}), 400

    docs = list(teacher_daily_work_col.find(
        {"session": session, "class_name": class_name, "date": date},
        {"_id": 0}
    ))
    return jsonify({"success": True, "rows": docs})


# ----------------------------
# Room Management
# ----------------------------
@app.route("/rooms/list", methods=["GET"])
def list_rooms():
    session = (request.args.get("session") or "").strip()
    if not session:
        return jsonify({"success": False, "message": "Missing session"}), 400
    rows = list(rooms_col.find({"session": session}, {"_id": 0}).sort([("room_no", ASCENDING)]))
    return jsonify({"success": True, "rooms": rows})


@app.route("/rooms/save", methods=["POST"])
def save_room():
    data = request.get_json() or {}
    session = (data.get("session") or "").strip()
    room_no = (data.get("room_no") or "").strip()
    rows = int(data.get("rows") or 0)
    benches_per_row = int(data.get("benches_per_row") or 0)
    seats_per_bench = int(data.get("seats_per_bench") or 0)
    if not session or not room_no or rows <= 0 or benches_per_row <= 0 or seats_per_bench <= 0:
        return jsonify({"success": False, "message": "Invalid room data"}), 400

    rooms_col.update_one(
        {"session": session, "room_no": room_no},
        {"$set": {
            "session": session,
            "room_no": room_no,
            "rows": rows,
            "benches_per_row": benches_per_row,
            "seats_per_bench": seats_per_bench,
            "updated_at": datetime.utcnow()
        }, "$setOnInsert": {"created_at": datetime.utcnow()}},
        upsert=True
    )
    return jsonify({"success": True, "message": "Room saved"})


@app.route("/rooms/delete", methods=["POST"])
def delete_room():
    data = request.get_json() or {}
    session = (data.get("session") or "").strip()
    room_no = (data.get("room_no") or "").strip()
    if not session or not room_no:
        return jsonify({"success": False, "message": "Missing session/room"}), 400
    rooms_col.delete_one({"session": session, "room_no": room_no})
    return jsonify({"success": True, "message": "Room deleted"})

# =========================
# HOLIDAY MANAGEMENT
# =========================
holiday_col = db["holidays"]

@app.route("/holiday/add", methods=["POST"])
def add_holiday():
    data = request.json
    name = data.get("name")
    date = data.get("date")      # YYYY-MM-DD
    session = data.get("session")

    if not name or not date or not session:
        return jsonify({"success": False, "message": "Missing fields"}), 400

    holiday_col.insert_one({
        "name": name,
        "date": date,
        "session": session,
        "created_at": datetime.utcnow()
    })
    return jsonify({"success": True})


@app.route("/holiday/list", methods=["GET"])
def list_holidays():
    session = request.args.get("session")
    q = {"session": session} if session else {}

    holidays = []
    for h in holiday_col.find(q).sort("date", 1):
        holidays.append({
            "id": str(h["_id"]),
            "name": h["name"],
            "date": h["date"]
        })

    return jsonify({"success": True, "holidays": holidays})


@app.route("/holiday/delete/<hid>", methods=["DELETE"])
def delete_holiday(hid):
    holiday_col.delete_one({"_id": ObjectId(hid)})
    return jsonify({"success": True})

# ---------------------------
# Leave Applications Collection
# ---------------------------
leave_col = db["leave_applications"]

LEAVE_DIR = "leave_docs"
os.makedirs(LEAVE_DIR, exist_ok=True)

# ---------------------------
# Helper: Resolve teacher document reliably
# ---------------------------
def get_teacher_doc(tid, session=None):

    tid = str(tid).strip().rstrip(",")
    session = session.strip().rstrip(",") if session else None

    if session:
        doc = teachers_col.find_one({"teacher_id": tid, "session": session})
        if doc:
            return doc

    if session:
        doc = teachers_col.find_one({"username": tid, "session": session})
        if doc:
            return doc

    if ObjectId.is_valid(tid):
        doc = teachers_col.find_one({"_id": ObjectId(tid)})
        if doc:
            return doc

    doc = teachers_col.find_one({"teacher_id": tid})
    if doc:
        return doc

    doc = teachers_col.find_one({"username": tid})
    if doc:
        return doc

    return None


# ---------------------------
# Submit leave request
# ---------------------------
@app.route("/leave/submit", methods=["POST"])
def submit_leave():
    teacher_id = request.form.get("teacher_id")
    session = request.form.get("session")
    start_date = request.form.get("start_date")
    end_date = request.form.get("end_date")
    reason = request.form.get("reason")
    purpose = request.form.get("purpose")   # ✅ ADDED
    file = request.files.get("document")

    if not all([teacher_id, session, start_date, end_date, reason, purpose]):
        return jsonify({"success": False, "message": "Missing fields"}), 400

    filename = ""
    if file:
        filename = f"{datetime.utcnow().timestamp()}_{file.filename}"
        filepath = os.path.join(LEAVE_DIR, filename)
        file.save(filepath)

    doc = {
        "teacher_id": teacher_id.strip().rstrip(","),
        "session": session.strip().rstrip(","),
        "start_date": start_date,
        "end_date": end_date,
        "reason": reason,
        "purpose": purpose,   # ✅ ADDED
        "document": filename,
        "status": "pending",
        "submitted_at": datetime.utcnow(),
        "admin_message": ""
    }

    res = leave_col.insert_one(doc)

    return jsonify({
        "success": True,
        "message": "Leave submitted",
        "leave_id": str(res.inserted_id)
    })


# ---------------------------
# Admin: List all leave requests
# ---------------------------
@app.route("/leave/list", methods=["GET"])
def list_leave():
    query = {}
    status = request.args.get("status")
    teacher_id = request.args.get("teacher_id")

    if status:
        query["status"] = status.strip().lower()
    if teacher_id:
        query["teacher_id"] = teacher_id.strip().rstrip(",")

    leaves = []

    for l in leave_col.find(query).sort("submitted_at", -1):
        t_id = l.get("teacher_id")
        session = l.get("session")

        teacher_name = "Unknown"
        t_doc = None

        if t_id and session:
            t_doc = get_teacher_doc(t_id, session)
            if t_doc:
                teacher_name = t_doc.get("name", "Unknown").strip()

        leaves.append({
            "id": str(l["_id"]),
            "teacher_id": t_id,
            "teacher_name": teacher_name,
            "session": session,
            "start_date": l.get("start_date"),
            "end_date": l.get("end_date"),
            "reason": l.get("reason"),
            "purpose": l.get("purpose", ""),   # ✅ ADDED
            "document": l.get("document", ""),
            "document_url": f"/leave/get-document/{l['document']}" if l.get("document") else "",
            "status": l.get("status"),
            "admin_message": l.get("admin_message", ""),
            "submitted_at": l.get("submitted_at").strftime("%Y-%m-%d %H:%M:%S")
        })

    return jsonify({"success": True, "leaves": leaves})


# ---------------------------
# Admin: Approve or Reject leave
# ---------------------------
@app.route("/leave/update-status/<leave_id>", methods=["POST"])
def update_leave_status(leave_id):
    data = request.json or {}
    status = data.get("status")
    message = data.get("message", "")

    if status not in ["approved", "rejected"]:
        return jsonify({"success": False, "message": "Invalid status"}), 400

    res = leave_col.update_one(
        {"_id": ObjectId(leave_id)},
        {"$set": {"status": status, "admin_message": message}}
    )

    if res.modified_count == 0:
        return jsonify({"success": False, "message": "Leave not found"}), 404

    return jsonify({"success": True, "message": f"Leave {status} successfully"})


# ---------------------------
# Teacher: View own leave applications
# ---------------------------
@app.route("/leave/teacher/<teacher_id>", methods=["GET"])
def teacher_leave_status(teacher_id):
    teacher_doc = get_teacher_doc(teacher_id)

    if not teacher_doc:
        return jsonify({"success": False, "message": "Teacher not found"}), 404

    ids = [
        str(teacher_doc["_id"]),
        teacher_doc.get("teacher_id"),
        teacher_doc.get("username")
    ]

    leaves = []
    for l in leave_col.find({"teacher_id": {"$in": ids}}).sort("submitted_at", -1):
        leaves.append({
            "leave_id": str(l["_id"]),
            "teacher_id": l.get("teacher_id"),
            "teacher_name": teacher_doc.get("name", "Unknown").strip(),
            "session": l.get("session"),
            "start_date": l.get("start_date"),
            "end_date": l.get("end_date"),
            "reason": l.get("reason"),
            "purpose": l.get("purpose", ""),  # ✅ ADDED
            "status": l.get("status"),
            "admin_message": l.get("admin_message", ""),
            "submitted_at": l.get("submitted_at").strftime("%Y-%m-%d %H:%M:%S")
        })

    return jsonify({"success": True, "leaves": leaves})


# ---------------------------
# Download leave document
# ---------------------------
@app.route("/leave/get-document/<filename>")
def get_leave_document(filename):
    filepath = os.path.join(LEAVE_DIR, filename)
    if os.path.exists(filepath):
        return send_file(filepath)
    return "File Not Found", 404

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
