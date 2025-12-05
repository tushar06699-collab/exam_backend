# PART 1/4
import os
import shutil
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pymongo import MongoClient, ASCENDING
from bson.objectid import ObjectId
from datetime import datetime

# ---------------------------
# MongoDB connection
# ---------------------------
MONGO_URL = "mongodb+srv://myusere:mypassword123@cluster0.fpsihrb.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URL)
db = client["school_exam_db"]

# Collections (mirror of your sqlite tables)
exams_col = db["exams"]
exam_subjects_col = db["exam_subjects"]
datesheet_col = db["datesheet"]
exam_marks_col = db["exam_marks"]
class_incharge_col = db["class_incharge"]
teachers_col = db["teachers"]
timetable_col = db["timetable"]

# Create useful indexes to emulate UNIQUE constraints where used in sqlite
# Note: index creation is idempotent
exams_col.create_index([("exam_name", ASCENDING), ("session", ASCENDING)])
exam_subjects_col.create_index([("session", ASCENDING), ("class_name", ASCENDING), ("subject", ASCENDING)], unique=True)
datesheet_col.create_index([("session", ASCENDING), ("class_name", ASCENDING), ("exam_name", ASCENDING), ("subject", ASCENDING)], unique=True)
exam_marks_col.create_index([("session", ASCENDING), ("exam_id", ASCENDING), ("class_name", ASCENDING), ("subject", ASCENDING), ("roll", ASCENDING)], unique=True)
class_incharge_col.create_index([("session", ASCENDING), ("class_name", ASCENDING)], unique=True)
teachers_col.create_index([("session", ASCENDING), ("username", ASCENDING)], unique=True)
timetable_col.create_index([("session", ASCENDING), ("teacher_id", ASCENDING), ("period", ASCENDING), ("class", ASCENDING)])

# ---------------------------
# Flask app + uploads folder
# ---------------------------
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

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

    if not exam_name or not session or not exam_time or total_marks is None:
        return jsonify({"success": False, "message": "Missing fields"}), 400

    doc = {
        "exam_name": exam_name,
        "session": session,
        "exam_time": exam_time,
        "total_marks": int(total_marks),
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
                "total_marks": ex.get("total_marks")
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

    # 1) all subjects for class+session
    subjects = [row.get("subject") for row in exam_subjects_col.find({"class_name": class_name, "session": session})]

    # 2) datesheet entries
    ds_cursor = datesheet_col.find({"class_name": class_name, "session": session, "exam_name": exam_name})
    date_map = {d.get("subject"): d.get("date") for d in ds_cursor}

    final = []
    for sub in subjects:
        final.append({
            "subject": sub,
            "date": date_map.get(sub, "")
        })
    return jsonify({"success": True, "datesheet": final})

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
    username = data.get("username")
    password = data.get("password")
    name = data.get("name")

    if not session or not username or not password or not name:
        return jsonify({"success": False, "message": "Missing fields"}), 400
    try:
        teachers_col.insert_one({
            "session": session,
            "username": username,
            "password": password,
            "name": name
        })
        return jsonify({"success": True, "message": "Teacher added"})
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
    except Exception:
        return jsonify({"success": False, "message": "Invalid teacher id"}), 400
    # delete teacher
    teachers_col.delete_one({"_id": obj})
    # delete timetable entries for this teacher (we store teacher_id as string or ObjectId depending on usage)
    # some existing timetable docs might store teacher_id as string id, so remove both forms
    timetable_col.delete_many({"teacher_id": teacher_id})
    timetable_col.delete_many({"teacher_id": obj})
    return jsonify({"success": True, "message": "Teacher and their timetable deleted successfully"})

# ---------------------------
# Login (admin + teacher)
# ---------------------------
@app.route("/login", methods=["POST"])
def login():
    data = request.json or {}
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        return jsonify({"success": False, "message": "Missing login details"}), 400

    # admin case — keep as before
    if username == "admin" and password == "admin":
        return jsonify({"success": True, "role": "admin", "token": "admin_token"})

    # teacher login
    t = teachers_col.find_one({"username": username, "password": password})
    if t:
        teacher_id = str(t.get("_id"))
        return jsonify({
            "success": True,
            "role": "teacher",
            "token": f"teacher_{username}_token",
            "teacher": {
                "id": teacher_id,
                "name": t.get("name"),
                "username": username,
                "session": t.get("session")
            }
        })
    # else invalid
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

    if not session or not teacher_id or not timetable_list:
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
        return jsonify({"success": False, "message": "Missing parameters", "timetable": []}), 400

    # find timetable docs where class matches
    cursor = timetable_col.find({"session": session, "class": class_name}).sort("period", ASCENDING)
    out = []
    for r in cursor:
        # fetch teacher name if teacher_id provided
        teacher_name = ""
        t_id = r.get("teacher_id")
        if t_id:
            t_doc = teachers_col.find_one({"_id": ObjectId(t_id)}) if ObjectId.is_valid(str(t_id)) else teachers_col.find_one({"_id": t_id})
            if t_doc:
                teacher_name = t_doc.get("name", "")
        out.append({
            "period": r.get("period"),
            "class": r.get("class"),
            "Monday": f"{teacher_name} - {r.get('monday')}" if r.get("monday") else "",
            "Tuesday": f"{teacher_name} - {r.get('tuesday')}" if r.get("tuesday") else "",
            "Wednesday": f"{teacher_name} - {r.get('wednesday')}" if r.get("wednesday") else "",
            "Thursday": f"{teacher_name} - {r.get('thursday')}" if r.get("thursday") else "",
            "Friday": f"{teacher_name} - {r.get('friday')}" if r.get("friday") else "",
            "Saturday": f"{teacher_name} - {r.get('saturday')}" if r.get("saturday") else "",
            "startDay": int(r.get("startDay", 1)),
            "endDay": int(r.get("endDay", 1))
        })
    return jsonify({"success": True, "timetable": out})

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
# Run app
# ---------------------------
if __name__ == "__main__":
    # For local testing across devices, listen on 0.0.0.0
    app.run(host="0.0.0.0", port=5000, debug=True)
