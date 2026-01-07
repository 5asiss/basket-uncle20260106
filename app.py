import os
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'basket-1234'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'basket_v12.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- [DB 모델 설정] ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    price_retail = db.Column(db.Integer)
    category = db.Column(db.String(50))
    image_url = db.Column(db.String(500))

# --- [로그인 엔진 설정] ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # 로그인 안 됐을 때 보낼 곳

@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

# --- [페이지 경로 설정] ---

@app.route('/')
def index():
    products = Product.query.all()
    # 상품이 있는 카테고리들만 추출
    cats = db.session.query(Product.category).distinct().all()
    categories = [c[0] for c in cats if c[0]]
    return render_template('index.html', products=products, categories=categories)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        flash("이메일 또는 비밀번호가 틀렸습니다.")
    return render_template('login.html')

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/admin/products')
@login_required
def admin_products():
    if not current_user.is_admin: return "권한 없음"
    products = Product.query.all()
    return render_template('admin_products.html', products=products)

@app.route('/admin/product/add', methods=['GET', 'POST'])
@login_required
def add_product():
    if not current_user.is_admin: return "권한 없음"
    if request.method == 'POST':
        new_p = Product(
            name=request.form.get('name'),
            price_retail=int(request.form.get('price')),
            category=request.form.get('category'),
            image_url=request.form.get('image_url')
        )
        db.session.add(new_p)
        db.session.commit()
        return redirect(url_for('admin_products'))
    
    # 등록 화면에서 선택할 카테고리 목록 (기본값 제공)
    cats = db.session.query(Product.category).distinct().all()
    main_cats = [c[0] for c in cats if c[0]]
    if not main_cats: main_cats = ['과일', '채소', '식자재']
    return render_template('admin_add_product.html', main_cats=main_cats)

# --- [DB 초기화 및 관리자 생성] ---
with app.app_context():
    db.create_all()
    if not User.query.filter_by(email='admin@test.com').first():
        admin = User(email='admin@test.com', password=generate_password_hash('1234'), is_admin=True)
        db.session.add(admin)
        db.session.commit()

if __name__ == '__main__':
    app.run(debug=True, port=5000)