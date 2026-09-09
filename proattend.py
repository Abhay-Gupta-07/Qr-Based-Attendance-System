import os
import math
import time
import socket
import sqlite3
import hmac
import hashlib
import json
import urllib.request as urlrequest
from zoneinfo import ZoneInfo
from datetime import datetime, date, timedelta
from functools import wraps
from flask import send_file
from openpyxl import Workbook
from io import BytesIO
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import mysql.connector
from mysql.connector import Error
from werkzeug.security import generate_password_hash, check_password_hash
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pytz

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'change-this-secret-key')


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


EMAIL_CONFIG = {
    'smtp_server': os.getenv('SMTP_SERVER', os.getenv('SMTP_HOST', 'smtp.gmail.com')),
    'smtp_port': int(os.getenv('SMTP_PORT', '587')),
    'username': os.getenv('SMTP_USERNAME', os.getenv('SMTP_USER', '')),
    'password': os.getenv('SMTP_PASSWORD', os.getenv('SMTP_PASS', '')),
    'from_email': os.getenv('SMTP_FROM', ''),
}

SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY', '')
SENDGRID_FROM_EMAIL = os.getenv('SENDGRID_FROM_EMAIL', '')

APP_BASE_URL = os.getenv('APP_BASE_URL', f"http://{get_local_ip()}:5000")

QR_SECRET = os.getenv('QR_SECRET', 'change-this-qr-secret')
OFFICE_LAT = float(os.getenv('OFFICE_LAT', '17.297821'))
OFFICE_LNG = float(os.getenv('OFFICE_LNG', '78.443803'))
OFFICE_RADIUS_METERS = float(os.getenv('OFFICE_RADIUS_METERS', '250'))


def get_db():
    db_path = os.getenv('DATABASE_PATH', os.path.join(os.path.dirname(__file__), 'database.db'))
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = get_db()
    cur = con.cursor()
    cur.executescript('''
    CREATE TABLE IF NOT EXISTS admins (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      full_name TEXT NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS employees (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      employee_code TEXT UNIQUE NOT NULL,
      full_name TEXT NOT NULL,
      department TEXT NOT NULL,
      designation TEXT NOT NULL,
      gmail TEXT,
      password_hash TEXT,
      active INTEGER DEFAULT 1,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS attendance (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      employee_id INTEGER NOT NULL,
      attendance_date TEXT NOT NULL,
      check_in_time TEXT NULL,
      check_out_time TEXT NULL,
      status TEXT DEFAULT 'PRESENT',
      gps_lat REAL NULL,
      gps_lng REAL NULL,
      face_verified INTEGER DEFAULT 0,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE (employee_id, attendance_date),
      FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS leave_requests (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      employee_id INTEGER NOT NULL,
      from_date TEXT NOT NULL,
      to_date TEXT NOT NULL,
      reason TEXT,
      status TEXT DEFAULT 'APPROVED',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS holidays (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      holiday_date TEXT UNIQUE NOT NULL,
      name TEXT NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      admin_id INTEGER,
      action TEXT NOT NULL,
      details TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')
    con.commit()
    con.close()


def query_all(sql, params=None):
    con = get_db()
    cur = con.cursor()
    cur.execute(sql, params or ())
    rows = [dict(row) for row in cur.fetchall()]
    cur.close()
    con.close()
    return rows


def query_one(sql, params=None):
    con = get_db()
    cur = con.cursor()
    cur.execute(sql, params or ())
    row = cur.fetchone()
    cur.close()
    con.close()
    return dict(row) if row else None


def execute(sql, params=None, many=False):
    con = get_db()
    cur = con.cursor()
    if many:
        cur.executemany(sql, params)
    else:
        cur.execute(sql, params or ())
    con.commit()
    last_id = cur.lastrowid
    cur.close()
    con.close()
    return last_id


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('admin_id'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def employee_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('employee_id'):
            return redirect(url_for('employee_login'))
        return f(*args, **kwargs)
    return wrapper


def write_audit(action, details=''):
    admin_id = session.get('admin_id')
    execute(
        'INSERT INTO audit_logs(admin_id, action, details) VALUES(?, ?, ?)',
        (admin_id, action, details)
    )


def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def verify_location(lat, lng):
    distance = haversine_meters(float(lat), float(lng), OFFICE_LAT, OFFICE_LNG)
    return distance <= OFFICE_RADIUS_METERS, round(distance, 2)


def dynamic_qr_token_for_employee(employee_code, window=None):
    if window is None:
        window = int(time.time() // 30)
    payload = f'{employee_code}|{window}'
    return hmac.new(QR_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:24]


def qr_token_for_employee(employee_id, day=None):
    kolkata_tz = pytz.timezone('Asia/Kolkata')
    now_kolkata = datetime.now(kolkata_tz)
    
    day = day or now_kolkata.date().isoformat()
    payload = f'{employee_id}|{day}'
    return hmac.new(QR_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:24]


def verify_qr_token(token):
    token = (token or '').strip()
    if not token:
        return None

    current_window = int(time.time() // 30)
    windows_to_check = [current_window, current_window - 1]

    kolkata_tz = pytz.timezone('Asia/Kolkata')
    now_kolkata = datetime.now(kolkata_tz)
    today = now_kolkata.date().isoformat()
    
    employees = query_all(
        'SELECT id, employee_code, full_name, department FROM employees WHERE active=1 ORDER BY id'
    )
    for emp in employees:
        for w in windows_to_check:
            if hmac.compare_digest(dynamic_qr_token_for_employee(emp['employee_code'], w), token):
                return emp
        if hmac.compare_digest(qr_token_for_employee(emp['employee_code'], today), token):
            return emp
    return None


def ensure_default_admin():
    row = query_one('SELECT id FROM admins LIMIT 1')
    if not row:
        execute(
            'INSERT INTO admins(username, password_hash, full_name) VALUES(?, ?, ?)',
            ('admin', generate_password_hash('admin123'), 'System Admin')
        )


def seed_default_holidays():
    default_holidays = [
        ('2026-01-01', "New Year's Day"),
        ('2026-01-14', 'Makar Sankranti / Pongal'),
        ('2026-01-26', 'Republic Day'),
        ('2026-03-04', 'Maha Shivratri'),
        ('2026-03-06', 'Holi'),
        ('2026-03-20', 'Id-ul-Fitr (Ramzan Eid)'),
        ('2026-04-03', 'Good Friday'),
        ('2026-04-14', 'Dr. B.R. Ambedkar Jayanti'),
        ('2026-05-01', 'May Day / Labor Day'),
        ('2026-05-27', 'Bakrid / Eid ul-Adha'),
        ('2026-06-25', 'Muharram'),
        ('2026-08-15', 'Independence Day'),
        ('2026-08-26', 'Milad-un-Nabi'),
        ('2026-09-04', 'Janmashtami'),
        ('2026-09-15', 'Ganesh Chaturthi'),
        ('2026-10-02', 'Mahatma Gandhi Jayanti'),
        ('2026-10-20', 'Dussehra (Vijaya Dashami)'),
        ('2026-11-08', 'Diwali (Deepavali)'),
        ('2026-11-24', 'Guru Nanak Jayanti'),
        ('2026-12-25', 'Christmas Day'),
    ]
    for h_date, name in default_holidays:
        execute('INSERT OR IGNORE INTO holidays(holiday_date, name) VALUES(?, ?)', (h_date, name))


def send_email_sendgrid(to_email, subject, body_text, body_html=None):
    # 1. Try SMTP (Gmail) first if password configured
    smtp_user = EMAIL_CONFIG.get('username', '').strip()
    smtp_pass = EMAIL_CONFIG.get('password', '').strip()
    if smtp_user and smtp_pass:
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = EMAIL_CONFIG.get('from_email') or smtp_user
            msg['To'] = to_email
            msg['Subject'] = subject
            msg.attach(MIMEText(body_text, 'plain'))
            if body_html:
                msg.attach(MIMEText(body_html, 'html'))
            
            server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], int(EMAIL_CONFIG['smtp_port']))
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
            server.quit()
            return 200
        except Exception as e:
            raise RuntimeError(f"SMTP Email Error: {e}")

    # 2. Try SendGrid if API Key is configured
    if SENDGRID_API_KEY:
        contents = [{"type": "text/plain", "value": body_text}]
        if body_html:
            contents.append({"type": "text/html", "value": body_html})

        data = {
            "personalizations": [
                {
                    "to": [{"email": to_email}]
                }
            ],
            "from": {
                "email": SENDGRID_FROM_EMAIL
            },
            "subject": subject,
            "content": contents
        }
        req = urlrequest.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=json.dumps(data).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY.strip()}",
                "Content-Type": "application/json"
            },
            method="POST"
        )

        try:
            with urlrequest.urlopen(req) as response:
                return response.status
        except urlrequest.HTTPError as err:
            error_body = ""
            try:
                error_body = err.read().decode('utf-8')
            except Exception:
                pass
            if "Maximum credits exceeded" in error_body:
                raise RuntimeError("SendGrid Credit Limit Exceeded: Please enter your Gmail App Password on the Settings page to send emails via Gmail SMTP.")
            elif err.code == 401:
                raise RuntimeError(f"SendGrid 401 Unauthorized: Please enter your Gmail App Password on the Settings page to send emails via Gmail SMTP. API details: {error_body}")
            elif err.code == 403:
                raise RuntimeError(f"SendGrid 403 Forbidden: Sender Email '{SENDGRID_FROM_EMAIL}' is not authorized or verified in SendGrid. API details: {error_body}")
            else:
                raise RuntimeError(f"SendGrid HTTP Error {err.code}: {err.reason}. Details: {error_body}")

    raise RuntimeError("SMTP Password missing. Please enter your Gmail App Password on the Settings page or set the SMTP_PASSWORD environment variable.")


try:
    ensure_default_admin()
except Exception as e:
    print('Startup database check error:', e)


@app.route('/')
@app.route('/home')
def index():
    if session.get('admin_id'):
        return redirect(url_for('dashboard'))
    if session.get('employee_id'):
        return redirect(url_for('employee_dashboard'))
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        admin_code = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        admin = query_one('SELECT * FROM admins WHERE username=?', (admin_code,))
        if admin and check_password_hash(admin['password_hash'], password):
            session['admin_id'] = admin['id']
            session['admin_name'] = admin['full_name']
            write_audit('LOGIN', f'Admin {admin_code} logged in')
            return redirect(url_for('dashboard'))
        flash('Invalid admin code or password', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    if session.get('admin_id'):
        write_audit('LOGOUT', 'Admin logged out')
    session.clear()
    return redirect(url_for('index'))


@app.route('/employee_login', methods=['GET', 'POST'])
def employee_login():
    if request.method == 'POST':
        employee_code = request.form.get('employee_code', '').strip().upper()
        password = request.form.get('password', '').strip()
        employee = query_one('SELECT * FROM employees WHERE employee_code=? AND active=1', (employee_code,))
        if employee and check_password_hash(employee['password_hash'], password):
            session['employee_id'] = employee['id']
            session['employee_name'] = employee['full_name']
            session['employee_code'] = employee['employee_code']
            return redirect(url_for('employee_dashboard'))
        flash('Invalid employee code or password', 'danger')
    return render_template('employee_login.html')


@app.route('/employee_dashboard')
@employee_required
def employee_dashboard():
    employee_id = session['employee_id']
    employee = query_one('SELECT * FROM employees WHERE id=?', (employee_id,))

    from_date = request.args.get('from_date') or date.today().replace(day=1).isoformat()
    to_date = request.args.get('to_date') or date.today().isoformat()

    attendance_records = query_all('''
        SELECT attendance_date, check_in_time, check_out_time, status
        FROM attendance
        WHERE employee_id = ?
        ORDER BY attendance_date DESC
        LIMIT 30
    ''', (employee_id,))

    filtered_records = query_all('''
        SELECT attendance_date, check_in_time, check_out_time, status
        FROM attendance
        WHERE employee_id = ? AND attendance_date BETWEEN ? AND ?
        ORDER BY attendance_date DESC
    ''', (employee_id, from_date, to_date))

    return render_template(
        'employee_dashboard.html',
        employee=employee,
        attendance_records=attendance_records,
        filtered_records=filtered_records,
        from_date=from_date,
        to_date=to_date
    )


@app.route('/employee_logout')
def employee_logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/dashboard')
@admin_required
def dashboard():
    employees = query_all('SELECT * FROM employees ORDER BY id DESC')
    today = date.today().isoformat()

    today_list = query_all('''
        SELECT a.attendance_date, e.employee_code, e.full_name, e.department,
               a.check_in_time, a.check_out_time, a.status
        FROM attendance a
        JOIN employees e ON e.id = a.employee_id
        WHERE a.attendance_date=?
        ORDER BY a.id DESC
    ''', (today,))

    stats = {
        'employees': query_one('SELECT COUNT(*) AS c FROM employees WHERE active=1')['c'],
        'today_present': query_one('SELECT COUNT(*) AS c FROM attendance WHERE attendance_date=?', (today,))['c'],
        'today_leaves': query_one(
            "SELECT COUNT(*) AS c FROM leave_requests WHERE status='APPROVED' AND ? BETWEEN from_date AND to_date",
            (today,)
        )['c'],
        'holidays': query_one('SELECT COUNT(*) AS c FROM holidays')['c'],
    }
    stats['working_days'] = 365 - stats['holidays']

    reports = query_all('''
        SELECT al.created_at, al.action, COALESCE(ad.full_name, 'System') AS admin_name, al.details
        FROM audit_logs al
        LEFT JOIN admins ad ON ad.id = al.admin_id
        ORDER BY al.id DESC LIMIT 10
    ''')

    return render_template(
        'dashboard.html',
        employees=employees,
        today_list=today_list,
        stats=stats,
        audit_logs=reports
    )


@app.route('/employees/add', methods=['POST'])
@admin_required
def add_employee():
    employee_code = request.form.get('employee_code', '').strip().upper()
    full_name = request.form.get('full_name', '').strip()
    department = request.form.get('department', '').strip()
    designation = request.form.get('designation', '').strip()
    gmail = request.form.get('gmail', '').strip()
    password = request.form.get('password', '').strip()

    if not all([employee_code, full_name, department, designation, password]):
        flash('Fill all required employee fields', 'danger')
        return redirect(url_for('dashboard'))

    exists = query_one('SELECT id FROM employees WHERE employee_code=?', (employee_code,))
    if exists:
        flash('Employee code already exists', 'danger')
        return redirect(url_for('dashboard'))

    password_hash = generate_password_hash(password)
    execute(
        'INSERT INTO employees(employee_code, full_name, department, designation, gmail, password_hash) VALUES(?, ?, ?, ?, ?, ?)',
        (employee_code, full_name, department, designation, gmail, password_hash)
    )
    write_audit('ADD_EMPLOYEE', f'{employee_code} - {full_name}')
    flash('Employee added successfully', 'success')
    return redirect(url_for('dashboard'))


@app.route('/clear_audit_logs', methods=['POST'])
@admin_required
def clear_audit_logs():
    execute('DELETE FROM audit_logs')
    write_audit('AUDIT_LOGS_CLEARED', 'All logs deleted')
    flash('All audit logs cleared successfully', 'danger')
    return redirect(url_for('dashboard'))


@app.route('/employees/delete_confirm/<int:employee_id>', methods=['GET'])
@admin_required
def delete_employee_confirm(employee_id):
    emp = query_one('SELECT * FROM employees WHERE id=?', (employee_id,))
    if not emp:
        flash('Employee not found', 'danger')
        return redirect(url_for('dashboard'))

    attendance_count = query_one('SELECT COUNT(*) AS c FROM attendance WHERE employee_id=?', (employee_id,))['c']
    leave_count = query_one('SELECT COUNT(*) AS c FROM leave_requests WHERE employee_id=?', (employee_id,))['c']

    return render_template(
        'delete_employee_confirm.html',
        employee=emp,
        attendance_count=attendance_count,
        leave_count=leave_count
    )

@app.route('/employees/edit/<int:employee_id>', methods=['GET', 'POST'])
@admin_required
def edit_employee(employee_id):
    emp = query_one('SELECT * FROM employees WHERE id=?', (employee_id,))
    if not emp:
        flash('Employee not found', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        employee_code = request.form.get('employee_code', '').strip().upper()
        full_name = request.form.get('full_name', '').strip()
        department = request.form.get('department', '').strip()
        designation = request.form.get('designation', '').strip()
        gmail = request.form.get('gmail', '').strip()
        password = request.form.get('password', '').strip()
        active = 1 if request.form.get('active') == '1' else 0

        if not all([employee_code, full_name, department, designation]):
            flash('Please fill all required fields', 'danger')
            return redirect(url_for('edit_employee', employee_id=employee_id))

        existing = query_one(
            'SELECT id FROM employees WHERE employee_code=? AND id != ?',
            (employee_code, employee_id)
        )
        if existing:
            flash('Employee code already exists', 'danger')
            return redirect(url_for('edit_employee', employee_id=employee_id))

        if password:
            password_hash = generate_password_hash(password)
            execute(
                '''UPDATE employees
                   SET employee_code=?, full_name=?, department=?, designation=?, gmail=?, password_hash=?, active=?
                   WHERE id=?''',
                (employee_code, full_name, department, designation, gmail, password_hash, active, employee_id)
            )
        else:
            execute(
                '''UPDATE employees
                   SET employee_code=?, full_name=?, department=?, designation=?, gmail=?, active=?
                   WHERE id=?''',
                (employee_code, full_name, department, designation, gmail, active, employee_id)
            )

        write_audit('EDIT_EMPLOYEE', f'{employee_code} - {full_name}')
        flash('Employee updated successfully', 'success')
        return redirect(url_for('dashboard'))

    return render_template('edit_employee.html', emp=emp)
@app.route('/employees/delete/<int:employee_id>', methods=['POST'])
@admin_required
def delete_employee(employee_id):
    emp = query_one('SELECT employee_code, full_name FROM employees WHERE id=?', (employee_id,))
    if emp:
        execute('DELETE FROM attendance WHERE employee_id=?', (employee_id,))
        execute('DELETE FROM leave_requests WHERE employee_id=?', (employee_id,))
        execute('DELETE FROM employees WHERE id=?', (employee_id,))
        write_audit('DELETE_EMPLOYEE', f"{emp['employee_code']} - {emp['full_name']}")
        flash('Employee and all related data deleted successfully', 'success')
    return redirect(url_for('dashboard'))


@app.route('/qr/<int:employee_id>')
@admin_required
def employee_qr(employee_id):
    emp = query_one('SELECT * FROM employees WHERE id=?', (employee_id,))
    if not emp:
        flash('Employee not found', 'danger')
        return redirect(url_for('dashboard'))
    token = qr_token_for_employee(emp['employee_code'])
    return render_template('employee_qr.html', employee=emp, token=token, today=date.today().isoformat())


@app.route('/send_qr_codes', methods=['POST'])
@admin_required
def send_qr_codes():
    employees = query_all('SELECT * FROM employees WHERE active=1 AND gmail IS NOT NULL AND gmail != ""')
    today = date.today().isoformat()
    sent_count = 0

    current_time = time.time()
    current_window = int(current_time // 30)
    expires_in = 30 - int(current_time % 30)

    base_url = (APP_BASE_URL or '').rstrip('/')
    if not base_url or 'localhost' in base_url or '127.0.0.1' in base_url:
        local_ip = get_local_ip()
        base_url = f"http://{local_ip}:5000"

    for emp in employees:
        token = dynamic_qr_token_for_employee(emp['employee_code'], current_window)
        qr_url = f"{base_url}/employee_qr_public/{emp['id']}"

        try:
            subject = f"🔒 Anti-Screenshot Dynamic Attendance Link for {emp['full_name']} ({today})"

            body_text = f"""
Dear {emp['full_name']},

🔒 ANTI-SCREENSHOT SECURITY NOTICE:
To prevent unauthorized screenshots and static image sharing, QR code images are NOT attached to emails.

Your attendance QR code is securely accessible exclusively via your anti-screenshot protected live portal link below:

Employee: {emp['full_name']}
Employee Code: {emp['employee_code']}

----------------------------------------------------
📲 OPEN PROTECTED LIVE QR CODE:
{qr_url}
----------------------------------------------------

Note: The live portal auto-refreshes your QR code token every 30 seconds and blocks screenshot / screen capture attempts.

Best regards,
ProAttend Team
"""

            body_html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; color: #0f172a; margin: 0; padding: 20px; }}
        .card {{ max-width: 480px; margin: 0 auto; background: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; padding: 32px 24px; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
        .badge {{ display: inline-block; background: #eef2ff; color: #4f46e5; font-size: 12px; font-weight: 700; padding: 4px 12px; border-radius: 20px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }}
        .security-alert {{ background: #fef2f2; border: 1px solid #fecaca; border-radius: 12px; padding: 16px; margin: 20px 0; text-align: left; }}
        .security-alert h3 {{ color: #dc2626; font-size: 14px; margin: 0 0 6px 0; font-weight: 700; }}
        .security-alert p {{ color: #991b1b; font-size: 13px; margin: 0; line-height: 1.4; }}
        h2 {{ color: #0f172a; font-size: 22px; font-weight: 800; margin-bottom: 6px; }}
        p {{ color: #64748b; font-size: 14px; margin-bottom: 20px; }}
        .btn {{ display: inline-block; background: #4f46e5; color: #ffffff !important; font-weight: 700; text-decoration: none; padding: 14px 28px; border-radius: 10px; font-size: 15px; margin-top: 12px; box-shadow: 0 4px 12px rgba(79, 70, 229, 0.25); }}
        .emp-details {{ background: #f8fafc; border-radius: 8px; padding: 14px; text-align: left; font-size: 13px; color: #334155; margin-top: 20px; border: 1px solid #e2e8f0; }}
        .emp-details div {{ margin-bottom: 6px; }}
    </style>
</head>
<body>
    <div class="card">
        <span class="badge">🔒 Protected Attendance Link</span>
        <h2>Dynamic Attendance Portal</h2>
        <p>Scan your auto-refreshing QR code at the camera terminal to mark attendance.</p>
        
        <div class="security-alert">
            <h3>🔒 Anti-Screenshot Protection</h3>
            <p>To prevent unauthorized static screenshots, QR images are disabled in emails. Click the button below to access your dynamic anti-screenshot protected QR code live.</p>
        </div>

        <div class="emp-details">
            <div><strong>Employee:</strong> {emp['full_name']}</div>
            <div><strong>Employee Code:</strong> {emp['employee_code']}</div>
            <div><strong>Department:</strong> {emp['department']}</div>
        </div>

        <div style="margin-top: 24px;">
            <a href="{qr_url}" class="btn" target="_blank">📲 Open Protected Live QR Code</a>
        </div>
        <p style="font-size: 12px; color: #94a3b8; margin-top: 16px;">
            Features 30-second TOTP token auto-refresh and real-time screen capture protection.
        </p>
    </div>
</body>
</html>
"""

            status = send_email_sendgrid(emp['gmail'], subject, body_text, body_html)

            if status in (200, 201, 202):
                sent_count += 1
            else:
                flash(f"Failed to send email to {emp['gmail']}", "danger")
                return redirect(url_for('dashboard'))

        except Exception as e:
            flash(f"Failed to send email to {emp['gmail']}: {e}", "danger")
            return redirect(url_for('dashboard'))

    write_audit('SEND_QR_CODES', f'Sent 30s dynamic QR codes to {sent_count} employees')
    flash(f'Dynamic 30-second QR codes sent successfully to {sent_count} employees!', 'success')
    return redirect(url_for('dashboard'))


@app.route('/api/get_dynamic_qr/<int:employee_id>')
def api_get_dynamic_qr(employee_id):
    emp = query_one('SELECT * FROM employees WHERE id=? AND active=1', (employee_id,))
    if not emp:
        return jsonify({'ok': False, 'message': 'Employee not found'}), 404
        
    current_time = time.time()
    current_window = int(current_time // 30)
    expires_in = 30 - int(current_time % 30)
    token = dynamic_qr_token_for_employee(emp['employee_code'], current_window)
    
    return jsonify({
        'ok': True,
        'token': token,
        'expires_in': expires_in,
        'employee_code': emp['employee_code'],
        'full_name': emp['full_name']
    })


@app.route('/employee_qr_public/<int:employee_id>')
def employee_qr_public(employee_id):
    emp = query_one('SELECT * FROM employees WHERE id=?', (employee_id,))
    if not emp:
        return "Employee not found"

    current_time = time.time()
    current_window = int(current_time // 30)
    token = dynamic_qr_token_for_employee(emp['employee_code'], current_window)

    return render_template(
        'employee_qr_public.html',
        employee=emp,
        token=token,
        today=date.today().isoformat()
    )


@app.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    global OFFICE_LAT, OFFICE_LNG, OFFICE_RADIUS_METERS, SENDGRID_API_KEY, SENDGRID_FROM_EMAIL, EMAIL_CONFIG, APP_BASE_URL
    if request.method == 'POST':
        try:
            lat = float(request.form.get('lat', OFFICE_LAT))
            lng = float(request.form.get('lng', OFFICE_LNG))
            radius = float(request.form.get('radius', OFFICE_RADIUS_METERS))
            
            OFFICE_LAT = lat
            OFFICE_LNG = lng
            OFFICE_RADIUS_METERS = radius

            base_url_input = request.form.get('app_base_url', '').strip()
            if base_url_input:
                APP_BASE_URL = base_url_input.rstrip('/')

            sg_key = request.form.get('sendgrid_api_key', '').strip()
            if sg_key:
                SENDGRID_API_KEY = sg_key

            from_email = request.form.get('sendgrid_from_email', '').strip()
            if from_email:
                SENDGRID_FROM_EMAIL = from_email

            smtp_user = request.form.get('smtp_username', '').strip()
            smtp_pass = request.form.get('smtp_password', '').strip()
            if smtp_user:
                EMAIL_CONFIG['username'] = smtp_user
            if smtp_pass:
                EMAIL_CONFIG['password'] = smtp_pass

            write_audit('SETTINGS_UPDATED', f'Lat: {lat}, Lng: {lng}, Radius: {radius}m')
            flash('Settings updated successfully', 'success')
        except ValueError:
            flash('Invalid location coordinates or radius', 'danger')
        return redirect(url_for('settings'))

    return render_template(
        'settings.html',
        current_lat=OFFICE_LAT,
        current_lng=OFFICE_LNG,
        current_radius=OFFICE_RADIUS_METERS,
        app_base_url=APP_BASE_URL,
        sendgrid_api_key=SENDGRID_API_KEY,
        sendgrid_from_email=SENDGRID_FROM_EMAIL,
        smtp_username=EMAIL_CONFIG.get('username', ''),
        smtp_password=EMAIL_CONFIG.get('password', '')
    )


@app.route('/mark_absent', methods=['POST'])
@admin_required
def mark_absent():
    data = request.get_json(force=True) if request.is_json else request.form
    employee_id = data.get('employee_id')
    reason = data.get('reason', '').strip()
    if not employee_id:
        return jsonify({'ok': False, 'message': 'Employee ID required'}), 400

    emp = query_one('SELECT * FROM employees WHERE id=? OR employee_code=?', (employee_id, employee_id))
    if not emp:
        return jsonify({'ok': False, 'message': 'Employee not found'}), 404

    today = date.today().isoformat()
    existing = query_one('SELECT * FROM attendance WHERE employee_id=? AND attendance_date=?', (emp['id'], today))
    if existing:
        execute('UPDATE attendance SET status=? WHERE id=?', ('ABSENT', existing['id']))
    else:
        execute(
            'INSERT INTO attendance(employee_id, attendance_date, status) VALUES(?, ?, ?)',
            (emp['id'], today, 'ABSENT')
        )
    write_audit('MARK_ABSENT', f"{emp['employee_code']} marked ABSENT. Reason: {reason}")
    return jsonify({'ok': True, 'message': f"Marked {emp['full_name']} as ABSENT"})


@app.route('/scanner')
@admin_required
def scanner_page():
    return render_template(
        'scanner.html',
        office_lat=OFFICE_LAT,
        office_lng=OFFICE_LNG,
        office_radius=OFFICE_RADIUS_METERS
    )


@app.route('/api/scan', methods=['POST'])
def api_scan():
    data = request.get_json(force=True)
    token = (data.get('token') or '').strip()
    action = (data.get('action') or 'CHECK_IN').strip().upper()
    lat = data.get('lat')
    lng = data.get('lng')
    face_verified = bool(data.get('face_verified', False))

    if not token:
        return jsonify({'ok': False, 'message': 'QR token missing'}), 400
    if action not in ('CHECK_IN', 'CHECK_OUT'):
        return jsonify({'ok': False, 'message': 'Invalid action'}), 400
    if lat is None or lng is None:
        return jsonify({'ok': False, 'message': 'GPS location missing'}), 400

    inside, distance = verify_location(lat, lng)
    if not inside:
        return jsonify({'ok': False, 'message': f'Outside allowed location. Distance: {distance} meters'}), 403

    employee = verify_qr_token(token)
    if not employee:
        return jsonify({'ok': False, 'message': 'Invalid or expired QR token'}), 400

    today = date.today().isoformat()
    holiday = query_one('SELECT id, name FROM holidays WHERE holiday_date=?', (today,))
    if holiday:
        return jsonify({'ok': False, 'message': f"Today is holiday: {holiday['name']}"}), 400

    approved_leave = query_one(
        "SELECT id FROM leave_requests WHERE employee_id=? AND status='APPROVED' AND ? BETWEEN from_date AND to_date",
        (employee['id'], today)
    )
    if approved_leave:
        return jsonify({'ok': False, 'message': 'Employee is on approved leave today'}), 400

    existing = query_one(
        'SELECT * FROM attendance WHERE employee_id=? AND attendance_date=?',
        (employee['id'], today)
    )

    now = datetime.now(ZoneInfo("Asia/Kolkata")).strftime('%H:%M:%S')

    if action == 'CHECK_IN':
        if existing and existing['check_in_time']:
            return jsonify({'ok': False, 'message': 'Check-in already marked today'})
        if existing:
            execute(
                'UPDATE attendance SET check_in_time=?, status=?, gps_lat=?, gps_lng=?, face_verified=? WHERE id=?',
                (now, 'PRESENT', lat, lng, int(face_verified), existing['id'])
            )
        else:
            execute(
                '''INSERT INTO attendance(employee_id, attendance_date, check_in_time, status, gps_lat, gps_lng, face_verified)
                   VALUES(?, ?, ?, ?, ?, ?, ?)''',
                (employee['id'], today, now, 'PRESENT', lat, lng, int(face_verified))
            )
        return jsonify({'ok': True, 'message': f"Check-in marked for {employee['full_name']}", 'employee': employee})

    if not existing or not existing['check_in_time']:
        return jsonify({'ok': False, 'message': 'Check-in not marked yet'})
    if existing['check_out_time']:
        return jsonify({'ok': False, 'message': 'Check-out already marked today'})

    execute('UPDATE attendance SET check_out_time=? WHERE id=?', (now, existing['id']))
    return jsonify({'ok': True, 'message': f"Check-out marked for {employee['full_name']}", 'employee': employee})


@app.route('/reports', methods=['GET'])
@admin_required
def reports():
    from_date = request.args.get('from_date') or date.today().replace(day=1).isoformat()
    to_date = request.args.get('to_date') or date.today().isoformat()
    employee_id = request.args.get('employee_id', '')

    employees = query_all('SELECT id, employee_code, full_name FROM employees WHERE active=1 ORDER BY full_name')
    params = [from_date, to_date]
    where = 'WHERE a.attendance_date BETWEEN ? AND ?'
    if employee_id:
        where += ' AND e.id=?'
        params.append(employee_id)

    rows = query_all(f'''
        SELECT e.employee_code, e.full_name, e.department, a.attendance_date,
               a.check_in_time, a.check_out_time, a.status
        FROM attendance a
        JOIN employees e ON e.id = a.employee_id
        {where}
        ORDER BY a.attendance_date DESC, e.full_name
    ''', tuple(params))

    return render_template(
        'reports.html',
        rows=rows,
        employees=employees,
        from_date=from_date,
        to_date=to_date,
        employee_id=employee_id
    )


@app.route('/reports/export')
@admin_required
def export_attendance_report():
    employee_id = request.args.get('employee_id', '').strip()
    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()

    sql = """
        SELECT e.employee_code, e.full_name, e.department,
               a.attendance_date, a.check_in_time, a.check_out_time, a.status
        FROM attendance a
        JOIN employees e ON a.employee_id = e.id
        WHERE 1=1
    """
    params = []

    if employee_id:
        sql += " AND a.employee_id = ?"
        params.append(employee_id)
    if from_date:
        sql += " AND a.attendance_date >= ?"
        params.append(from_date)
    if to_date:
        sql += " AND a.attendance_date <= ?"
        params.append(to_date)

    sql += " ORDER BY a.attendance_date DESC, e.employee_code ASC"

    rows = query_all(sql, tuple(params))

    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance Report"

    ws.append([
        "Employee Code", "Full Name", "Department",
        "Attendance Date", "Check In", "Check Out", "Status"
    ])

    for row in rows:
        ws.append([
            row.get('employee_code', ''),
            row.get('full_name', ''),
            row.get('department', ''),
            str(row.get('attendance_date', '')),
            str(row.get('check_in_time') or '-'),
            str(row.get('check_out_time') or '-'),
            row.get('status', '')
        ])

    for col in ws.columns:
        max_length = 0
        col_letter = col[0].column_letter
        for cell in col:
            cell_value = str(cell.value) if cell.value is not None else ""
            if len(cell_value) > max_length:
                max_length = len(cell_value)
        ws.column_dimensions[col_letter].width = max_length + 2

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = "attendance_report.xlsx"
    write_audit('REPORT_EXPORTED', filename)

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@app.route('/attendance-calculator', methods=['GET'])
@admin_required
def attendance_calculator():
    employees = query_all('SELECT id, employee_code, full_name FROM employees WHERE active=1 ORDER BY full_name')
    employee_id = request.args.get('employee_id', '')
    from_date = request.args.get('from_date', '')
    to_date = request.args.get('to_date', '')
    result = None

    if employee_id and from_date and to_date:
        emp = query_one('SELECT * FROM employees WHERE id=?', (employee_id,))
        start = datetime.strptime(from_date, '%Y-%m-%d').date()
        end = datetime.strptime(to_date, '%Y-%m-%d').date()
        total_days = (end - start).days + 1
        holiday_count = query_one(
            'SELECT COUNT(*) AS c FROM holidays WHERE holiday_date BETWEEN ? AND ?',
            (from_date, to_date)
        )['c']
        leave_count = query_one(
            "SELECT COUNT(*) AS c FROM leave_requests WHERE employee_id=? AND status='APPROVED' AND (from_date <= ? AND to_date >= ?)",
            (employee_id, to_date, from_date)
        )['c']
        present = query_one(
            'SELECT COUNT(*) AS c FROM attendance WHERE employee_id=? AND attendance_date BETWEEN ? AND ?',
            (employee_id, from_date, to_date)
        )['c']
        working_days = max(total_days - holiday_count, 0)
        percentage = round((present / working_days) * 100, 2) if working_days else 0

        result = {
            'employee': emp,
            'total_days': total_days,
            'working_days': working_days,
            'present': present,
            'holidays': holiday_count,
            'leave_records': leave_count,
            'percentage': percentage,
        }

    return render_template(
        'calculator.html',
        employees=employees,
        result=result,
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date
    )


@app.route('/calculator/export_all')
@admin_required
def export_all_calculator_excel():
    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()

    if not from_date or not to_date:
        flash('Please select From Date and To Date', 'danger')
        return redirect(url_for('attendance_calculator'))

    employees = query_all("""
        SELECT id, employee_code, full_name, department
        FROM employees
        ORDER BY employee_code ASC
    """)

    holiday_rows = query_all("""
        SELECT holiday_date
        FROM holidays
        WHERE holiday_date BETWEEN ? AND ?
    """, (from_date, to_date))
    holiday_dates = {str(h['holiday_date']) for h in holiday_rows}

    leave_rows = query_all("""
        SELECT employee_id, from_date, to_date
        FROM leave_requests
        WHERE status='Approved'
          AND (
                from_date BETWEEN ? AND ?
                OR to_date BETWEEN ? AND ?
                OR (from_date <= ? AND to_date >= ?)
              )
    """, (from_date, to_date, from_date, to_date, from_date, to_date))

    attendance_rows = query_all("""
        SELECT employee_id, attendance_date, status
        FROM attendance
        WHERE attendance_date BETWEEN ? AND ?
    """, (from_date, to_date))

    attendance_map = {}
    for row in attendance_rows:
        attendance_map[(row['employee_id'], str(row['attendance_date']))] = row['status']

    leave_map = {}
    for row in leave_rows:
        emp_id = row['employee_id']
        start = row['from_date']
        end = row['to_date']

        if hasattr(start, 'strftime'):
            start = start.strftime('%Y-%m-%d')
        if hasattr(end, 'strftime'):
            end = end.strftime('%Y-%m-%d')

        start_dt = datetime.strptime(str(start), '%Y-%m-%d')
        end_dt = datetime.strptime(str(end), '%Y-%m-%d')

        current = start_dt
        while current <= end_dt:
            day_str = current.strftime('%Y-%m-%d')
            if from_date <= day_str <= to_date:
                leave_map[(emp_id, day_str)] = True
            current += timedelta(days=1)

    start_dt = datetime.strptime(from_date, '%Y-%m-%d')
    end_dt = datetime.strptime(to_date, '%Y-%m-%d')

    wb = Workbook()
    ws = wb.active
    ws.title = "All Attendance Summary"

    ws.append([
        "Employee Code", "Full Name", "Department", "From Date", "To Date",
        "Total Days", "Working Days", "Present", "Holidays",
        "Leave Records", "Attendance Percentage"
    ])

    for emp in employees:
        total_days = 0
        working_days = 0
        present = 0
        holidays = 0
        leave_records = 0

        current = start_dt
        while current <= end_dt:
            day_str = current.strftime('%Y-%m-%d')
            total_days += 1

            if day_str in holiday_dates:
                holidays += 1
            else:
                working_days += 1
                if leave_map.get((emp['id'], day_str)):
                    leave_records += 1
                if attendance_map.get((emp['id'], day_str)) in ['Present', 'PRESENT', 'present']:
                    present += 1

            current += timedelta(days=1)

        percentage = round((present / working_days) * 100, 2) if working_days > 0 else 0

        ws.append([
            emp.get('employee_code', ''),
            emp.get('full_name', ''),
            emp.get('department', ''),
            from_date,
            to_date,
            total_days,
            working_days,
            present,
            holidays,
            leave_records,
            f"{percentage}%"
        ])

    for col in ws.columns:
        max_length = 0
        col_letter = col[0].column_letter
        for cell in col:
            value = str(cell.value) if cell.value is not None else ""
            if len(value) > max_length:
                max_length = len(value)
        ws.column_dimensions[col_letter].width = max_length + 2

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"all_students_attendance_{from_date}_to_{to_date}.xlsx"
    write_audit('CALCULATOR_EXPORT_ALL', filename)

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@app.route('/leave', methods=['GET', 'POST'])
@admin_required
def leave_management():
    if request.method == 'POST':
        employee_id = request.form.get('employee_id')
        from_date = request.form.get('from_date')
        to_date = request.form.get('to_date')
        reason = request.form.get('reason', '').strip()
        status = request.form.get('status', 'APPROVED')

        execute(
            'INSERT INTO leave_requests(employee_id, from_date, to_date, reason, status) VALUES(?, ?, ?, ?, ?)',
            (employee_id, from_date, to_date, reason, status)
        )
        write_audit('LEAVE_ADDED', f'Employee {employee_id} leave {from_date} to {to_date}')
        flash('Leave saved', 'success')
        return redirect(url_for('leave_management'))

    employees = query_all('SELECT id, employee_code, full_name FROM employees WHERE active=1 ORDER BY full_name')
    leaves = query_all('''
        SELECT l.*, e.employee_code, e.full_name
        FROM leave_requests l
        JOIN employees e ON e.id = l.employee_id
        ORDER BY l.id DESC
    ''')

    return render_template('leave.html', employees=employees, leaves=leaves)


@app.route('/holidays', methods=['GET', 'POST'])
@admin_required
def holiday_management():
    if request.method == 'POST':
        holiday_date = request.form.get('holiday_date')
        name = request.form.get('name', '').strip()
        execute('INSERT INTO holidays(holiday_date, name) VALUES(?, ?)', (holiday_date, name))
        write_audit('HOLIDAY_ADDED', f'{holiday_date} - {name}')
        flash('Holiday added', 'success')
        return redirect(url_for('holiday_management'))

    holidays = query_all('SELECT * FROM holidays ORDER BY holiday_date ASC')
    return render_template('holidays.html', holidays=holidays)


@app.route('/edit_holiday/<int:holiday_id>', methods=['GET', 'POST'])
@admin_required
def edit_holiday(holiday_id):
    holiday = query_one('SELECT * FROM holidays WHERE id=?', (holiday_id,))
    if not holiday:
        flash('Holiday not found', 'danger')
        return redirect(url_for('holiday_management'))

    if request.method == 'POST':
        if request.form.get('delete'):
            execute('DELETE FROM holidays WHERE id=?', (holiday_id,))
            write_audit('HOLIDAY_DELETED', f'{holiday["holiday_date"]} - {holiday["name"]}')
            flash('Holiday deleted successfully', 'danger')
            return redirect(url_for('holiday_management'))

        holiday_date = request.form.get('holiday_date')
        name = request.form.get('name', '').strip()

        execute(
            'UPDATE holidays SET holiday_date=?, name=? WHERE id=?',
            (holiday_date, name, holiday_id)
        )
        write_audit('HOLIDAY_UPDATED', f'{holiday_date} - {name}')
        flash('Holiday updated successfully', 'success')
        return redirect(url_for('holiday_management'))

    return render_template('edit_holiday.html', holiday=holiday)


@app.route('/today')
@admin_required
def today_list():
    today = date.today().isoformat()
    rows = query_all('''
        SELECT e.employee_code, e.full_name, e.department,
               a.check_in_time, a.check_out_time, a.status
        FROM attendance a
        JOIN employees e ON e.id = a.employee_id
        WHERE a.attendance_date=?
        ORDER BY a.id DESC
    ''', (today,))
    return render_template('today.html', rows=rows, today=today)


@app.route('/today/export')
@admin_required
def export_today_excel():
    today = date.today()

    rows = query_all("""
        SELECT e.employee_code, e.full_name, e.department,
               a.check_in_time, a.check_out_time, a.status
        FROM attendance a
        JOIN employees e ON a.employee_id = e.id
        WHERE a.attendance_date = ?
        ORDER BY e.employee_code ASC
    """, (today,))

    wb = Workbook()
    ws = wb.active
    ws.title = "Today's Attendance"

    ws.append([
        "Employee Code",
        "Full Name",
        "Department",
        "Check In",
        "Check Out",
        "Status"
    ])

    for row in rows:
        ws.append([
            row.get('employee_code'),
            row.get('full_name'),
            row.get('department'),
            str(row.get('check_in_time') or '-'),
            str(row.get('check_out_time') or '-'),
            row.get('status')
        ])

    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            val = str(cell.value) if cell.value else ""
            if len(val) > max_len:
                max_len = len(val)
        ws.column_dimensions[col_letter].width = max_len + 2

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=f"today_attendance_{today}.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


if __name__ == '__main__':
    try:
        init_db()
        ensure_default_admin()
        seed_default_holidays()
    except Exception as e:
        print('Database error:', e)
        print('Make sure you created the SQLite database and imported schema.sql')
    app.run(host='0.0.0.0', port=5000, debug=True)