from app import create_app, db
from app.models import User
from werkzeug.security import generate_password_hash

app = create_app()

with app.app_context():
    db.create_all()
    # Create default admin if not exists
    if not User.query.filter_by(username='admin').first():
        admin = User(
            username='admin',
            email='admin@netflix.local',
            password_hash=generate_password_hash('admin123'),
            is_admin=True,
            is_approved=True
        )
        db.session.add(admin)
        db.session.commit()
        print("[+] Default admin created: username=admin password=admin123")

if __name__ == '__main__':
    print("[*] Starting Netflix Cookie Manager...")
    print("[*] Admin Panel: http://localhost:5000/admin/")
    print("[*] User Panel:  http://localhost:5000/user/")
    app.run(debug=True, host='0.0.0.0', port=5000)
