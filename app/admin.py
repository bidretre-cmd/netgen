from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, Response, current_app
from flask_login import login_required, current_user
from functools import wraps
from .models import User, CookieResult
from . import db
from .checker import (
    check_single_cookie,
    parse_proxy_text,
    CHECKER_AVAILABLE,
    cancel_checking,
    reset_checker_state,
    is_cancelled
)
import threading
import json
import queue
import traceback
from datetime import datetime
from .proxy_checker import check_proxy, unique_proxy_entries

admin_bp = Blueprint('admin', __name__)

# SSE progress queue (global)
progress_queue = queue.Queue()

# Global job tracker to prevent old threads from interfering with new ones
current_job_id = 0
job_lock = threading.Lock()


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Access denied. Admins only.', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


# ─── HELPER: Kumpulkan file contents ─────────────────────────────────────────

def _collect_from_files(files):
    contents = []
    for f in files:
        if f and f.filename:
            try:
                content = f.read().decode('utf-8', errors='ignore')
                if content.strip():
                    contents.append((f.filename, content))
            except Exception:
                pass
    return contents


def _collect_from_zip(zip_file):
    import zipfile, io
    contents = []
    try:
        data = zip_file.read()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if name.lower().endswith(('.txt', '.json')) and '__MACOSX' not in name:
                    try:
                        text = zf.read(name).decode('utf-8', errors='ignore')
                        if text.strip():
                            contents.append((name.split('/')[-1], text))
                    except Exception:
                        pass
    except Exception:
        pass
    return contents


def _collect_from_folder(folder_path):
    import os
    contents = []
    if not os.path.isdir(folder_path):
        return contents
    for fname in os.listdir(folder_path):
        if fname.lower().endswith(('.txt', '.json')):
            fpath = os.path.join(folder_path, fname)
            try:
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
                if text.strip():
                    contents.append((fname, text))
            except Exception:
                pass
    return contents


# ─── BACKGROUND WORKER ───────────────────────────────────────────────────────

def _run_check_background(flask_app, my_job_id, service_type, file_contents, proxies, threads_count):
    """
    Jalankan pengecekan cookies di background thread.
    WAJIB pakai flask_app.app_context() agar bisa akses database.
    """
    with flask_app.app_context():
        results = {
            'done': 0, 'total': len(file_contents),
            'success': 0, 'failed': 0, 'error': 0,
            'success_plans': {}
        }
        task_q = queue.Queue()
        for item in file_contents:
            task_q.put(item)

        db_results = []
        lock = threading.Lock()

        def sanitize(s):
            if not isinstance(s, str):
                return s
            return s.encode('utf-8', 'surrogatepass').decode('utf-8', 'ignore').replace('\x00', '')

        def worker():
            with flask_app.app_context():
                while True:
                    if current_job_id != my_job_id or is_cancelled():
                        while not task_q.empty():
                            try:
                                task_q.get_nowait()
                                task_q.task_done()
                            except queue.Empty:
                                break
                        break

                    try:
                        fname, content = task_q.get_nowait()
                    except queue.Empty:
                        break

                    sanitized_content = sanitize(content)
                    existing = CookieResult.query.filter_by(cookie_text=sanitized_content).first()
                    if existing:
                        with lock:
                            results['done'] += 1
                            results['success'] += 1
                            plan_lbl = existing.plan_name or existing.plan_key or 'Unknown'
                            cnt_lbl = existing.country or 'Unknown'
                            is_oh = "On Hold" if existing.is_on_hold else ""
                            status_detail = f"Skipped ({plan_lbl} - {cnt_lbl})"
                            if is_oh:
                                status_detail = f"Skipped ({plan_lbl} - {is_oh} - {cnt_lbl})"
                            
                            # Increment category count
                            results['success_plans'][plan_lbl] = results['success_plans'].get(plan_lbl, 0) + 1

                            progress_queue.put(json.dumps({
                                'done': results['done'],
                                'total': results['total'],
                                'success': results['success'],
                                'failed': results['failed'],
                                'error': results['error'],
                                'current': f"{fname} (Skipped)",
                                'status': 'success',
                                'status_detail': status_detail,
                            }))
                        task_q.task_done()
                        continue

                    try:
                        if service_type == 'netflix':
                            result = check_single_cookie(content, proxies)
                        else:
                            result = check_single_cookie(content, proxies)
                    except Exception as e:
                        result = {'status': 'error', 'error_reason': str(e)}

                    with lock:
                        if result.get('status') == 'cancelled':
                            task_q.task_done()
                            continue
                            
                        results['done'] += 1
                        status = result.get('status', 'error')
                        status_detail = status
                        if status in ('success', 'free'):
                            results['success'] += 1
                            plan_lbl = result.get('plan_name') or result.get('plan_key') or 'Unknown'
                            cnt_lbl = result.get('country') or 'Unknown'
                            is_oh = "On Hold" if result.get('is_on_hold') else ""
                            if status == 'free':
                                status_detail = f"Success (Free - {cnt_lbl})"
                            else:
                                if is_oh:
                                    status_detail = f"Success ({plan_lbl} - {is_oh} - {cnt_lbl})"
                                else:
                                    status_detail = f"Success ({plan_lbl} - {cnt_lbl})"
                            
                            # Increment category count
                            results['success_plans'][plan_lbl] = results['success_plans'].get(plan_lbl, 0) + 1

                            try:
                                entry = CookieResult(
                                    service_type=sanitize(service_type),
                                    filename=sanitize(fname),
                                    cookie_text=sanitize(result.get('cookie_text', '')),
                                    plan_key=sanitize(result.get('plan_key', 'unknown')),
                                    plan_name=sanitize(result.get('plan_name', 'Unknown')),
                                    country=sanitize(result.get('country', 'Unknown')),
                                    is_on_hold=bool(result.get('is_on_hold', False)),
                                    email=sanitize(result.get('email')),
                                    account_name=sanitize(result.get('account_name')),
                                    quality=sanitize(result.get('quality')),
                                    max_streams=sanitize(result.get('max_streams')),
                                    plan_price=sanitize(result.get('plan_price')),
                                    next_billing=sanitize(result.get('next_billing')),
                                    payment_method=sanitize(result.get('payment_method')),
                                    member_since=sanitize(result.get('member_since')),
                                    extra_members=sanitize(result.get('extra_members')),
                                    profiles=sanitize(result.get('profiles')),
                                    hold_status=sanitize(result.get('hold_status')),
                                    membership_status=sanitize(result.get('membership_status')),
                                    source_file=sanitize(fname),
                                    checked_at=datetime.utcnow(),
                                )
                                db.session.add(entry)
                                db.session.commit()
                            except Exception as e:
                                db.session.rollback()
                                print(f"[DB ERROR] Gagal tambah entry {fname}: {e}")
                        elif status == 'failed':
                            results['failed'] += 1
                            status_detail = f"Failed: {result.get('error_reason', 'Cookie Invalid')}"
                        else:
                            results['error'] += 1
                            status_detail = f"Error: {result.get('error_reason', 'Unknown Error')}"

                        progress_queue.put(json.dumps({
                            'done': results['done'],
                            'total': results['total'],
                            'success': results['success'],
                            'failed': results['failed'],
                            'error': results['error'],
                            'current': fname,
                            'status': status,
                            'status_detail': status_detail,
                        }))
                        print(f"[CHECK] {fname} -> {status} ({status_detail}) | Error: {result.get('error_reason', '')}")
                    task_q.task_done()

        n_threads = min(threads_count, max(1, len(file_contents)))
        thread_list = [threading.Thread(target=worker, daemon=True) for _ in range(n_threads)]
        for t in thread_list:
            t.start()
        for t in thread_list:
            t.join()

        if current_job_id == my_job_id and not is_cancelled():
            progress_queue.put(json.dumps({
                'done': results['total'],
                'total': results['total'],
                'success': results['success'],
                'failed': results['failed'],
                'error': results['error'],
                'saved': results['success'],
                'current': 'DONE',
                'status': 'done',
                'plans_summary': results.get('success_plans', {})
            }))

def _run_recheck_background(flask_app, my_job_id, service_filter=None, proxies=None, plan_filter='all'):
    if proxies is None: proxies = []
    with flask_app.app_context():
        query = CookieResult.query
        if service_filter:
            query = query.filter(CookieResult.service_type == service_filter)
        if plan_filter == 'free':
            query = query.filter(CookieResult.plan_key == 'free')
        elif plan_filter == 'non_free':
            query = query.filter(CookieResult.plan_key != 'free')
        cookies = query.all()
        results = {'done': 0, 'total': len(cookies), 'success': 0, 'failed': 0, 'error': 0, 'deleted': 0, 'success_plans': {}}

        # ── Reset is_verified = False untuk semua cookie yang akan dicek ──
        # Cookie yang error/tidak selesai tidak akan ikut terhitung di dashboard
        try:
            for c in cookies:
                c.is_verified = False
            db.session.commit()
        except Exception:
            db.session.rollback()
        # ─────────────────────────────────────────────────────────────────

        task_q = queue.Queue()
        for c in cookies:
            task_q.put((c.id, c.service_type, c.cookie_text))
            
        lock = threading.Lock()
        
        def sanitize(s):
            if not isinstance(s, str): return s
            return s.encode('utf-8', 'surrogatepass').decode('utf-8', 'ignore').replace('\x00', '')
            
        def worker():
            with flask_app.app_context():
                while True:
                    if current_job_id != my_job_id or is_cancelled():
                        while not task_q.empty():
                            try:
                                task_q.get_nowait()
                                task_q.task_done()
                            except queue.Empty:
                                break
                        break
                        
                    try:
                        c_id, s_type, c_text = task_q.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        if s_type == 'netflix':
                            res = check_single_cookie(c_text, proxies)
                        else:
                            res = check_single_cookie(c_text, proxies)
                    except Exception as e:
                        res = {'status': 'error', 'error_reason': str(e)}

                    with lock:
                        if res.get('status') == 'cancelled':
                            task_q.task_done()
                            continue
                            
                        results['done'] += 1
                        status = res.get('status', 'error')
                        status_detail = status
                        
                        try:
                            cookie = CookieResult.query.get(c_id)
                            if cookie:
                                if status in ('success', 'free'):
                                    results['success'] += 1
                                    cookie.plan_key = sanitize(res.get('plan_key', cookie.plan_key))
                                    cookie.plan_name = sanitize(res.get('plan_name', cookie.plan_name))
                                    cookie.country = sanitize(res.get('country', cookie.country))
                                    cookie.is_on_hold = bool(res.get('is_on_hold', cookie.is_on_hold))
                                    cookie.email = sanitize(res.get('email') or cookie.email)
                                    cookie.checked_at = datetime.utcnow()
                                    cookie.is_verified = True   # ← tandai verified
                                    
                                    plan_lbl = cookie.plan_name or cookie.plan_key or 'Unknown'
                                    cnt_lbl = cookie.country or 'Unknown'
                                    is_oh = "On Hold" if cookie.is_on_hold else ""
                                    if status == 'free':
                                        status_detail = f"Success (Free - {cnt_lbl})"
                                    else:
                                        if is_oh:
                                            status_detail = f"Success ({plan_lbl} - {is_oh} - {cnt_lbl})"
                                        else:
                                            status_detail = f"Success ({plan_lbl} - {cnt_lbl})"
                                    
                                    # Increment category count
                                    results['success_plans'][plan_lbl] = results['success_plans'].get(plan_lbl, 0) + 1
                                elif status == 'failed':
                                    results['failed'] += 1
                                    results['deleted'] += 1
                                    status_detail = f"Failed (Deleted): {res.get('error_reason', 'Cookie Invalid')}"
                                    db.session.delete(cookie)   # ← cookie invalid, hapus
                                else:
                                    # error: tetap di DB tapi is_verified=False (sudah di-reset di atas)
                                    results['error'] += 1
                                    status_detail = f"Error: {res.get('error_reason', 'Unknown Error')}"
                                db.session.commit()
                        except Exception as e:
                            db.session.rollback()
                            print(f"Error updating cookie {c_id}: {e}")
                            results['error'] += 1
                            status_detail = f"Error: {str(e)}"
                            
                        progress_queue.put(json.dumps({
                            'done': results['done'],
                            'total': results['total'],
                            'success': results['success'],
                            'failed': results['failed'],
                            'error': results['error'],
                            'current': f"ID {c_id}",
                            'status': status,
                            'status_detail': status_detail,
                        }))
                    task_q.task_done()
                
        n_threads = min(10, max(1, len(cookies)))
        thread_list = [threading.Thread(target=worker, daemon=True) for _ in range(n_threads)]
        for t in thread_list: t.start()
        for t in thread_list: t.join()
        
        if current_job_id == my_job_id and not is_cancelled():
            progress_queue.put(json.dumps({
                'done': results['total'],
                'total': results['total'],
                'success': results['success'],
                'failed': results['failed'],
                'error': results['error'],
                'saved': results['success'],
                'current': 'DONE',
                'status': 'done',
                'plans_summary': results.get('success_plans', {})
            }))




# ─── ROUTES ──────────────────────────────────────────────────────────────────

@admin_bp.route('/')
@login_required
@admin_required
def dashboard():
    total = CookieResult.query.count()
    from sqlalchemy import func
    plan_stats = db.session.query(
        CookieResult.plan_key,
        CookieResult.plan_name,
        func.count(CookieResult.id).label('count')
    ).group_by(CookieResult.plan_key).all()

    country_stats = db.session.query(
        CookieResult.country,
        func.count(CookieResult.id).label('count')
    ).group_by(CookieResult.country).order_by(
        func.count(CookieResult.id).desc()
    ).limit(15).all()

    service_stats = db.session.query(
        CookieResult.service_type,
        func.count(CookieResult.id).label('count')
    ).group_by(CookieResult.service_type).order_by(
        func.count(CookieResult.id).desc()
    ).all()

    pending_users = User.query.filter_by(is_approved=False, is_admin=False).count()
    total_users = User.query.filter_by(is_admin=False).count()

    return render_template('admin/dashboard.html',
                           total=total,
                           plan_stats=plan_stats,
                           country_stats=country_stats,
                           service_stats=service_stats,
                           pending_users=pending_users,
                           total_users=total_users)


@admin_bp.route('/check', methods=['GET', 'POST'])
@login_required
@admin_required
def check():
    if request.method == 'POST':
        proxy_text = request.form.get('proxies', '')
        threads_count = max(1, min(int(request.form.get('threads', 5) or 5), 30))
        service_type = request.form.get('service_type', 'netflix')
        proxies = parse_proxy_text(proxy_text) if proxy_text.strip() else []
        
        if not proxies:
            proxies = []
            
        upload_mode = request.form.get('upload_mode', 'files')
        file_contents = []

        if upload_mode == 'zip':
            zip_file = request.files.get('zip_file')
            if not zip_file or zip_file.filename == '':
                flash('Please select a ZIP file first.', 'danger')
                return redirect(url_for('admin.check'))
            file_contents = _collect_from_zip(zip_file)
            if not file_contents:
                flash('ZIP does not contain valid cookie files (.txt/.json).', 'danger')
                return redirect(url_for('admin.check'))

        elif upload_mode == 'folder':
            folder_path = request.form.get('folder_path', '').strip()
            if not folder_path:
                flash('Please enter a folder path.', 'danger')
                return redirect(url_for('admin.check'))
            file_contents = _collect_from_folder(folder_path)
            if not file_contents:
                flash(f'Folder not found or is empty: {folder_path}', 'danger')
                return redirect(url_for('admin.check'))

        else:  # files
            files = request.files.getlist('cookies')
            if not files or all(f.filename == '' for f in files):
                flash('Please select at least 1 cookie file.', 'danger')
                return redirect(url_for('admin.check'))
            file_contents = _collect_from_files(files)

        if not file_contents:
            flash('No valid cookie files found.', 'danger')
            return redirect(url_for('admin.check'))

        # Kosongkan queue lama
        while not progress_queue.empty():
            try:
                progress_queue.get_nowait()
            except queue.Empty:
                break

        # Ambil app instance SEBELUM spawn thread
        flask_app = current_app._get_current_object()

        # Reset cancel flag sebelum jalan
        global current_job_id
        with job_lock:
            current_job_id += 1
            my_job_id = current_job_id

        reset_checker_state()

        t = threading.Thread(
            target=_run_check_background,
            args=(flask_app, my_job_id, service_type, file_contents, proxies, threads_count),
            daemon=True
        )
        t.start()
        return render_template('admin/checking.html',
                               total=len(file_contents),
                               checker_ok=CHECKER_AVAILABLE)

    return render_template('admin/check.html',
                           checker_ok=CHECKER_AVAILABLE)

@admin_bp.route('/api/cancel', methods=['POST'])
@login_required
@admin_required
def api_cancel():
    global current_job_id
    with job_lock:
        current_job_id += 1
    cancel_checking()
    return jsonify({"status": "cancelled", "message": "Checking process stopped."})


@admin_bp.route('/progress-stream')
@login_required
@admin_required
def progress_stream():
    def generate():
        while True:
            try:
                msg = progress_queue.get(timeout=30)
                yield f"data: {msg}\n\n"
                data = json.loads(msg)
                if data.get('status') == 'done' or data.get('current') == 'DONE':
                    break
            except queue.Empty:
                yield 'data: {"heartbeat": true}\n\n'
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@admin_bp.route('/debug-checker')
@login_required
@admin_required
def debug_checker():
    """Cek apakah modul checker berhasil di-import."""
    from .checker import CHECKER_AVAILABLE, check_single_cookie
    import sys, os, traceback
    
    test_res = "Not tested"
    try:
        test_res = check_single_cookie("test_cookie", [{'http': 'http://127.0.0.1'}])
    except Exception as e:
        test_res = traceback.format_exc()

    checker_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'Netflix-Cookie-Checker-main'
    )
    main_exists = os.path.exists(os.path.join(checker_dir, 'main.py'))
    info = {
        'checker_available': CHECKER_AVAILABLE,
        'checker_dir': checker_dir,
        'main_py_exists': main_exists,
        'db_count': CookieResult.query.count(),
        'test_res': test_res,
    }
    return jsonify(info)


@admin_bp.route('/results')
@login_required
@admin_required
def results():
    page = request.args.get('page', 1, type=int)
    service_filter = request.args.get('service', '')
    plan_filter = request.args.get('plan', '')
    country_filter = request.args.get('country', '')
    search = request.args.get('search', '')

    query = CookieResult.query
    if service_filter:
        query = query.filter(CookieResult.service_type == service_filter)
    if plan_filter:
        query = query.filter(CookieResult.plan_key == plan_filter)
    if country_filter:
        query = query.filter(CookieResult.country == country_filter)
    if search:
        query = query.filter(
            (CookieResult.email.ilike(f'%{search}%')) |
            (CookieResult.account_name.ilike(f'%{search}%')) |
            (CookieResult.country.ilike(f'%{search}%'))
        )

    pagination = query.order_by(CookieResult.checked_at.desc()).paginate(page=page, per_page=20)
    from sqlalchemy import func
    plans = db.session.query(CookieResult.plan_key, CookieResult.plan_name).distinct().all()
    countries = db.session.query(CookieResult.country).distinct().order_by(CookieResult.country).all()

    return render_template('admin/results.html',
                           pagination=pagination,
                           plans=plans,
                           countries=countries,
                           service_filter=service_filter,
                           plan_filter=plan_filter,
                           country_filter=country_filter,
                           search=search)


@admin_bp.route('/delete/<int:cookie_id>', methods=['POST'])
@login_required
@admin_required
def delete_cookie(cookie_id):
    cookie = CookieResult.query.get_or_404(cookie_id)
    db.session.delete(cookie)
    db.session.commit()
    flash('Cookie deleted successfully.', 'success')
    return redirect(request.referrer or url_for('admin.results'))


@admin_bp.route('/delete-expired', methods=['POST'])
@login_required
@admin_required
def delete_expired():
    from datetime import date
    today = date.today().isoformat()
    deleted = 0
    for c in CookieResult.query.all():
        if c.next_billing:
            try:
                if c.next_billing[:10] < today:
                    db.session.delete(c)
                    deleted += 1
            except Exception:
                pass
    db.session.commit()
    flash(f'{deleted} expired cookies deleted from database.', 'success')
    return redirect(url_for('admin.results'))


@admin_bp.route('/delete-all', methods=['POST'])
@login_required
@admin_required
def delete_all():
    try:
        CookieResult.query.delete()
        db.session.commit()
        flash('All cookie data has been cleared.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to delete data: {e}', 'danger')
    return redirect(url_for('admin.results'))


@admin_bp.route('/delete-duplicates', methods=['POST'])
@login_required
@admin_required
def delete_duplicates():
    """
    Hapus duplikasi cookies berdasarkan isi dari cookie_text secara persis.
    Menyimpan entri terbaru (ID terbesar) untuk setiap grup duplikat.
    """
    from sqlalchemy import func

    deleted_total = 0

    try:
        # Cari grup yang memiliki cookie_text dan service_type yang sama persis
        dup_groups = db.session.query(
            CookieResult.cookie_text,
            CookieResult.service_type,
            func.max(CookieResult.id).label('keep_id'),
            func.count(CookieResult.id).label('cnt')
        ).filter(
            CookieResult.cookie_text.isnot(None),
            CookieResult.cookie_text != ''
        ).group_by(
            CookieResult.cookie_text,
            CookieResult.service_type
        ).having(
            func.count(CookieResult.id) > 1
        ).all()

        ids_to_delete = []
        for group in dup_groups:
            # Ambil semua ID dalam grup ini, selain yang ingin kita pertahankan (keep_id)
            dupes = db.session.query(CookieResult.id).filter(
                CookieResult.cookie_text == group.cookie_text,
                CookieResult.service_type == group.service_type,
                CookieResult.id != group.keep_id
            ).all()
            ids_to_delete.extend([d.id for d in dupes])

        if ids_to_delete:
            # SQLite default limits expression depth/parameters, so delete in chunks if needed
            # but usually it's fine for small/medium sizes. Let's do it in one query first.
            deleted_total = CookieResult.query.filter(
                CookieResult.id.in_(ids_to_delete)
            ).delete(synchronize_session='fetch')
            db.session.commit()

        flash(
            f'✅ Successfully deleted {deleted_total} duplicate cookies.',
            'success'
        )

    except Exception as e:
        db.session.rollback()
        flash(f'❌ Failed to delete duplicates: {e}', 'danger')

    return redirect(url_for('admin.results'))


@admin_bp.route('/api/duplicate-stats')
@login_required
@admin_required
def api_duplicate_stats():
    """Preview statistik cookies duplikat berdasarkan isi."""
    from sqlalchemy import func

    try:
        dup_groups = db.session.query(
            func.count(CookieResult.id).label('cnt')
        ).filter(
            CookieResult.cookie_text.isnot(None),
            CookieResult.cookie_text != ''
        ).group_by(
            CookieResult.cookie_text,
            CookieResult.service_type
        ).having(
            func.count(CookieResult.id) > 1
        ).all()

        dup_by_text = sum(row.cnt - 1 for row in dup_groups)
        dup_groups_count = len(dup_groups)

        total_cookies = CookieResult.query.count()

        return jsonify({
            'total': total_cookies,
            'dup_text': dup_by_text,
            'dup_text_groups': dup_groups_count,
            'will_delete': dup_by_text,
            'after_clean': max(0, total_cookies - dup_by_text),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─── PROXY CHECKER ROUTES ────────────────────────────────────────────────────

proxy_progress_queue = queue.Queue()
current_proxy_job_id = 0
proxy_job_lock = threading.Lock()
proxy_cancel_flag = False

@admin_bp.route('/proxy-check')
@login_required
@admin_required
def proxy_check():
    return render_template('admin/proxy_check.html')

@admin_bp.route('/proxy-check/start', methods=['POST'])
@login_required
@admin_required
def proxy_check_start():
    global current_proxy_job_id, proxy_cancel_flag
    data = request.json
    raw_proxies = data.get('proxies', '')
    threads_count = max(1, min(int(data.get('threads', 50)), 200))
    test_url = data.get('test_url', 'https://httpbin.org/ip')
    timeout = float(data.get('timeout', 10.0))
    
    schemes = []
    if data.get('http'): schemes.append('http')
    if data.get('https'): schemes.append('https')
    if data.get('socks4'): schemes.append('socks4')
    if data.get('socks5'): schemes.append('socks5')
    if not schemes:
        schemes = ['http', 'https']

    proxy_list = unique_proxy_entries(raw_proxies)
    if not proxy_list:
        return jsonify({'success': False, 'message': 'No valid proxies found'})

    with proxy_job_lock:
        current_proxy_job_id += 1
        my_job_id = current_proxy_job_id
        proxy_cancel_flag = False
        while not proxy_progress_queue.empty():
            try: proxy_progress_queue.get_nowait()
            except queue.Empty: break

    def worker(flask_app, job_id, p_list):
        with flask_app.app_context():
            results = {'total': len(p_list), 'done': 0, 'live': 0, 'dead': 0}
            task_q = queue.Queue()
            for p in p_list:
                task_q.put(p)
            
            def check_thread():
                while True:
                    if proxy_cancel_flag or current_proxy_job_id != job_id:
                        break
                    try:
                        p = task_q.get_nowait()
                    except queue.Empty:
                        break
                    
                    res = check_proxy(p, schemes, test_url, timeout)
                    
                    with proxy_job_lock:
                        if proxy_cancel_flag or current_proxy_job_id != job_id:
                            task_q.task_done()
                            break
                        results['done'] += 1
                        if res.status == 'LIVE':
                            results['live'] += 1
                        else:
                            results['dead'] += 1
                            
                        proxy_progress_queue.put(json.dumps({
                            'done': results['done'],
                            'total': results['total'],
                            'live': results['live'],
                            'dead': results['dead'],
                            'current': res.raw_proxy,
                            'status': res.status,
                            'latency': res.latency_ms,
                            'error': res.error
                        }))
                    task_q.task_done()

            n_threads = min(threads_count, len(p_list))
            threads = [threading.Thread(target=check_thread, daemon=True) for _ in range(n_threads)]
            for t in threads: t.start()
            for t in threads: t.join()

            if current_proxy_job_id == job_id and not proxy_cancel_flag:
                proxy_progress_queue.put(json.dumps({'status': 'done'}))

    threading.Thread(target=worker, args=(current_app._get_current_object(), my_job_id, proxy_list), daemon=True).start()
    return jsonify({'success': True, 'total': len(proxy_list)})

@admin_bp.route('/proxy-check/stream')
@login_required
@admin_required
def proxy_check_stream():
    def event_stream():
        while True:
            try:
                msg = proxy_progress_queue.get(timeout=10)
                yield f"data: {msg}\n\n"
                if '"status": "done"' in msg:
                    break
            except queue.Empty:
                yield "data: {\"ping\": 1}\n\n"
    return Response(event_stream(), mimetype="text/event-stream")

@admin_bp.route('/proxy-check/cancel', methods=['POST'])
@login_required
@admin_required
def proxy_check_cancel():
    global proxy_cancel_flag
    proxy_cancel_flag = True
    return jsonify({'success': True})


@admin_bp.route('/recheck-all', methods=['POST'])
@login_required
@admin_required
def recheck_all():
    service_filter = request.args.get('service', '')
    plan_filter = request.form.get('plan_filter', 'all')
    proxy_text = request.form.get('proxies', '')
    proxies = parse_proxy_text(proxy_text) if proxy_text.strip() else []

    if not proxies:
        proxies = []

    # Kosongkan queue lama
    while not progress_queue.empty():
        try:
            progress_queue.get_nowait()
        except queue.Empty:
            break

    global current_job_id
    with job_lock:
        current_job_id += 1
        my_job_id = current_job_id

    flask_app = current_app._get_current_object()
    reset_checker_state()

    query = CookieResult.query
    if service_filter:
        query = query.filter(CookieResult.service_type == service_filter)
    if plan_filter == 'free':
        query = query.filter(CookieResult.plan_key == 'free')
    elif plan_filter == 'non_free':
        query = query.filter(CookieResult.plan_key != 'free')
        
    total = query.count()
    if total == 0:
        flash('No cookies to check.', 'warning')
        return redirect(url_for('admin.results'))

    t = threading.Thread(
        target=_run_recheck_background,
        args=(flask_app, my_job_id, service_filter, proxies, plan_filter),
        daemon=True
    )
    t.start()
    return render_template('admin/checking.html', total=total)


@admin_bp.route('/token-proxies', methods=['GET', 'POST'])
@login_required
@admin_required
def token_proxies():
    import os, json
    proxy_path = os.path.join(os.path.dirname(__file__), 'token_proxies.txt')
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')

    if request.method == 'POST':
        # Save proxy list
        new_proxies = request.form.get('proxies', '')
        try:
            with open(proxy_path, 'w', encoding='utf-8') as f:
                f.write(new_proxies)
        except Exception as e:
            flash(f'Error saving proxies: {e}', 'danger')
            return redirect(url_for('admin.token_proxies'))

        # Save global proxy toggle
        # Radio button selalu mengirim tepat satu nilai: '1' (aktif) atau '0' (nonaktif)
        use_token_proxy = (request.form.get('use_token_proxy', '1') == '1')
        config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
            except Exception:
                pass
        config['use_token_proxy'] = use_token_proxy
        try:
            with open(config_path, 'w') as f:
                json.dump(config, f)
        except Exception as e:
            flash(f'Error saving config: {e}', 'danger')
            return redirect(url_for('admin.token_proxies'))

        flash('Token proxy settings updated successfully.', 'success')
        return redirect(url_for('admin.token_proxies'))

    proxies_text = ''
    if os.path.exists(proxy_path):
        try:
            with open(proxy_path, 'r', encoding='utf-8') as f:
                proxies_text = f.read()
        except Exception:
            pass

    use_token_proxy = True  # default on
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                use_token_proxy = config.get('use_token_proxy', True)
        except Exception:
            pass

    return render_template('admin/token_proxies.html',
                           proxies_text=proxies_text,
                           use_token_proxy=use_token_proxy)



@admin_bp.route('/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def settings():
    import os
    import json
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    
    if request.method == 'POST':
        ads_percentage = request.form.get('ads_percentage', type=int)
        prevent_duplicate_claims = (request.form.get('prevent_duplicate_claims', '1') == '1')
        
        if ads_percentage is None or ads_percentage < 0 or ads_percentage > 100:
            flash('Invalid percentage. Must be between 0 and 100.', 'danger')
        else:
            config = {}
            if os.path.exists(config_path):
                try:
                    with open(config_path, 'r') as f:
                        config = json.load(f)
                except Exception:
                    pass
            config['ads_percentage'] = ads_percentage
            config['prevent_duplicate_claims'] = prevent_duplicate_claims
            with open(config_path, 'w') as f:
                json.dump(config, f)
            flash('Settings saved successfully.', 'success')
            
    current_pct = 0
    prevent_duplicate_claims = True
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                current_pct = int(config.get('ads_percentage', 0))
                prevent_duplicate_claims = config.get('prevent_duplicate_claims', True)
        except Exception:
            pass
            
    return render_template(
        'admin/settings.html',
        ads_percentage=current_pct,
        prevent_duplicate_claims=prevent_duplicate_claims
    )



@admin_bp.route('/users')
@login_required
@admin_required
def users():
    from .models import UserCookieClaim
    from datetime import datetime, timedelta

    all_users = User.query.filter_by(is_admin=False).order_by(User.created_at.desc()).all()

    # Hitung klaim 24 jam terakhir untuk semua user biasa
    twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
    claims_data = db.session.query(
        UserCookieClaim.user_id, db.func.count(UserCookieClaim.id).label('count')
    ).filter(
        UserCookieClaim.claimed_at >= twenty_four_hours_ago
    ).group_by(UserCookieClaim.user_id).all()
    
    claims_dict = {row.user_id: row.count for row in claims_data}

    return render_template('admin/users.html', users=all_users, claims_dict=claims_dict)


@admin_bp.route('/users/approve/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def approve_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_approved = True
    db.session.commit()
    flash(f'User "{user.username}" has been approved.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/reject/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def reject_user(user_id):
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    flash('User has been deleted.', 'info')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/update-limit/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def update_user_limit(user_id):
    user = User.query.get_or_404(user_id)
    try:
        # Update daily claim limit
        new_limit = request.form.get('max_daily_claims')
        if new_limit is not None:
            new_limit = int(new_limit)
            if new_limit < 0:
                flash('Daily claim limit cannot be negative.', 'danger')
            else:
                user.max_daily_claims = new_limit

        # Update ads percentage
        new_ads = request.form.get('ads_percentage')
        if new_ads is not None:
            new_ads = int(new_ads)
            if 0 <= new_ads <= 100:
                user.ads_percentage = new_ads
            else:
                flash('Ads percentage must be between 0 and 100.', 'danger')

        db.session.commit()
        flash(f'Settings for "{user.username}" successfully updated.', 'success')
    except ValueError:
        flash('Limits and ads percentage must be valid numbers.', 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to update settings: {e}', 'danger')
    return redirect(url_for('admin.users'))



@admin_bp.route('/api/stats')
@login_required
@admin_required
def api_stats():
    from sqlalchemy import func
    plan_stats = db.session.query(
        CookieResult.plan_key, func.count(CookieResult.id)
    ).group_by(CookieResult.plan_key).all()
    return jsonify({p: c for p, c in plan_stats})
