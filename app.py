# PART 1/4
import os
import shutil
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pymongo import MongoClient, ASCENDING
from bson.objectid import ObjectId
from datetime import datetime
from pymongo import MongoClient


# ---------------------------
# MongoDB connection
# ---------------------------
MONGO_URL = "mongodb+srv://myusere:mypassword123@cluster0.fpsihrb.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URL)
db = client["school_exam_db"]

# --------------------------------------------------------
# MongoDB (STUDENT DATABASE ONLY)
# --------------------------------------------------------
STUDENT_MONGO_URI = "mongodb+srv://school_students:Tushar2007@cluster0.upoywck.mongodb.net/school_erp?retryWrites=true&w=majority"

student_client = MongoClient(STUDENT_MONGO_URI)
student_db = student_client["school_erp"]
students_col = student_db["students"]


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

        return jsonify({
            "success": True,
            "student": {
                "id": str(student["_id"]),
                "name": student.get("student_name"),
                "class_name": student.get("class_name"),
                "section": student.get("section"),
                "roll": student.get("rollno"),
                "photo_url": student.get("photo_url", "")
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/portal/students", methods=["GET"])
def portal_list_students():
    students = []
    for s in students_col.find():
        students.append({
            "id": str(s["_id"]),
            "name": s.get("student_name"),
            "class_name": s.get("class_name"),
            "section": s.get("section"),
            "roll": s.get("rollno"),
            "photo_url": s.get("photo_url", "")
        })
    return jsonify({"success": True, "students": students})

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

    # Check missing fields
    if not session or not username or not password or not name:
        return jsonify({"success": False, "message": "Missing fields"}), 400

    # Check if username already exists
    if teachers_col.find_one({"username": username, "session": session}):
        return jsonify({"success": False, "message": "Username already exists"}), 400

    # -----------------------------
    # AUTO-GENERATE 4-DIGIT TEACHER ID
    # -----------------------------
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

    # ---------- ADMIN LOGIN (HARD-CODED) ----------
    if username == "Admin" and password == "PS*100":
        return jsonify({
            "success": True,
            "role": "admin",
            "token": "admin_token"
        })

    # ---------- PRINCIPAL LOGIN (HARD-CODED) ----------
    if username == "Naveen" and password == "14112017":
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
    teacher = teachers_col.find_one({"username": username, "password": password})
    if teacher:
        return jsonify({
            "success": True,
            "role": "teacher",
            "token": f"teacher_{username}_token",
            "teacher": {
                "id": str(teacher.get("_id")),
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
        return jsonify({
            "success": True,
            "role": "student",
            "token": f"student_{username}_token",
            "student": {
                "id": str(student["_id"]),
                "name": student.get("student_name"),
                "admission_no": student.get("admission_no"),
                "rollno": student.get("rollno"),
                "class": student.get("class_name"),
                "section": student.get("section"),
                "session": student.get("session"),
                "photo": student.get("photo_url", "")
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
    date = request.form.get("date")    # format: YYYY-MM-DD
    file = request.files.get("pdf")

    if not title or not date or not file:
        return jsonify({"success": False, "message": "Missing fields"}), 400

    try:
        # Create file path
        filename = f"{datetime.utcnow().timestamp()}_{file.filename}"
        filepath = os.path.join(NOTICE_DIR, filename)
        file.save(filepath)

        # Insert into Mongo
        res = notices_col.insert_one({
            "title": title,
            "description": description,
            "date": date,
            "file": filename,
            "uploaded_at": datetime.utcnow()
        })

        return jsonify({"success": True, "message": "Notice uploaded", "id": str(res.inserted_id)})
    
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
@app.route("/notice/list", methods=["GET"])
def list_notices():
    notices = []
    for n in notices_col.find().sort("uploaded_at", -1):
        notices.append({
            "id": str(n["_id"]),
            "title": n.get("title"),
            "description": n.get("description"),
            "date": n.get("date"),
            "file": n.get("file"),
            "url": f"/notice/get-file/{n.get('file')}"
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

        # delete file
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
        student_id = att.get("student_id")
        status = att.get("status")
        if student_id and status in ["present", "absent", "leave"]:
            to_insert.append({
                "session": session,
                "class_name": class_name,
                "date": date,
                "student_id": student_id,
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
        records.append({
            "student_id": att.get("student_id"),
            "status": att.get("status")
        })

    return jsonify({"success": True, "attendance": records})

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

# ---------------------------
# Run app
# ---------------------------
if __name__ == "__main__":
    # For local testing across devices, listen on 0.0.0.0
    app.run(host="0.0.0.0", port=5000, debug=True)
