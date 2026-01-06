import os
from flask import Flask, render_template, request, redirect, url_for, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
from io import BytesIO
from datetime import datetime
import pandas as pd
from flask import send_file
from io import BytesIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'basket-uncle-1234'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///basket.db'
db = SQLAlchemy(app)

# --- 데이터베이스 설계 ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(50))
    grade = db.Column(db.String(20), default='RETAIL') # RETAIL(소매) / WHOLESALE(도매)
    is_admin = db.Column(db.Boolean, default=False)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    price_retail = db.Column(db.Integer)
    price_wholesale = db.Column(db.Integer)
    category = db.Column(db.String(50)) # 농산물 / 공산품
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

# --- 화면 경로(Route) 설정 ---

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
            grade='RETAIL' # 기본은 소매회원
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
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/admin/excel')
@login_required
def download_excel():
    if not current_user.is_admin: return "권한 없음"
    orders = Order.query.all()
    df = pd.DataFrame([{
        "주문ID": o.id, "상품명": o.product_name, "금액": o.total_price, "날짜": o.created_at
    } for o in orders])
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, download_name="orders.xlsx", as_attachment=True)

# 처음 실행 시 DB와 관리자 계정 생성
def init_db():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(email='admin@test.com').first():
            admin = User(email='admin@test.com', password=generate_password_hash('1234'), name='관리자', is_admin=True)
            p1 = Product(name='감자 1kg', price_retail=5000, price_wholesale=4000, category='농산물')
            db.session.add(admin)
            db.session.add(p1)
            db.session.commit()

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
    @app.route('/admin/orders/excel')
@login_required
def download_orders_excel():
    # 1. 관리자인지 확인
    if not current_user.is_admin:
        return "관리자만 접근 가능합니다."

    # 2. DB에서 모든 주문 데이터 가져오기
    orders = Order.query.order_by(Order.created_at.desc()).all()

    # 3. 엑셀에 들어갈 데이터 정리
    order_data = []
    for o in orders:
        order_data.append({
            "주문번호": o.id,
            "상품명": o.product_name,
            "결제금액": o.total_price,
            "주문시간": o.created_at.strftime('%Y-%m-%d %H:%M'),
            "회원ID": o.user_id
        })

    # 4. 판다스(Pandas)를 이용해 엑셀 파일로 변환
    df = pd.DataFrame(order_data)
    
    # 메모리상에 파일 생성 (서버에 파일을 저장하지 않아 깔끔함)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='주문리스트')
    output.seek(0)

    # 5. 사용자에게 파일 전송
    return send_file(
        output,
        download_name=f"바구니삼촌_주문내역_{datetime.now().strftime('%Y%m%d')}.xlsx",
        as_attachment=True
    )