import os
from flask import Flask, render_template, request, redirect, url_for, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
from io import BytesIO
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'basket-uncle-1234'
# 파일 상단에 import os 가 있는지 확인하세요
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'basket.db')
db = SQLAlchemy(app)

# --- 데이터베이스 설계 ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(50))
    grade = db.Column(db.String(20), default='RETAIL')
    is_admin = db.Column(db.Boolean, default=False)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    price_retail = db.Column(db.Integer)
    price_wholesale = db.Column(db.Integer)
    category = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    product_name = db.Column(db.String(100))
    total_price = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.now)

# --- 로그인 관리 설정 ---
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- 화면 경로 설정 ---
@app.route('/')
def index():
    products = Product.query.filter_by(is_active=True).all()
    return render_template('index.html', products=products)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        hashed_pw = generate_password_hash(request.form['password'])
        new_user = User(
            email=request.form['email'],
            password=hashed_pw,
            name=request.form['name'],
            grade='RETAIL'
        )
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/admin/excel')
@login_required
def download_excel():
    if not current_user.is_admin:
        return "권한 없음"
    orders = Order.query.all()
    order_list = []
    for o in orders:
        order_list.append({
            "주문ID": o.id, 
            "상품명": o.product_name, 
            "금액": o.total_price, 
            "날짜": o.created_at
        })
    df = pd.DataFrame(order_list)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    return send_file(output, download_name="orders.xlsx", as_attachment=True)

# 초기화 함수
def init_db():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(email='admin@test.com').first():
            admin = User(
                email='admin@test.com', 
                password=generate_password_hash('1234'), 
                name='관리자', 
                is_admin=True
            )
            p1 = Product(name='감자 1kg', price_retail=5000, price_wholesale=4000, category='농산물')
            db.session.add(admin)
            db.session.add(p1)
            db.session.commit()

# 기존 맨 아래 부분을 지우고 이걸로 교체하세요
with app.app_context():
    db.create_all()  # 테이블이 없으면 새로 생성
    # 관리자 계정이 없으면 생성
    if not User.query.filter_by(email='admin@test.com').first():
        admin = User(
            email='admin@test.com', 
            password=generate_password_hash('1234'), 
            name='관리자', 
            is_admin=True
        )
        # 테스트용 상품 하나 추가 (테이블이 비어있으면 에러 날 수 있음)
        p1 = Product(name='감자 1kg', price_retail=5000, price_wholesale=4000, category='농산물')
        db.session.add(admin)
        db.session.add(p1)
        db.session.commit()
        print("DB 초기화 완료!")

if __name__ == '__main__':
    app.run(debug=True)