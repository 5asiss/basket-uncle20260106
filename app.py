import os
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, send_file, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'basket-uncle-1234'
basedir = os.path.abspath(os.path.dirname(__file__))
# DB 파일명을 v6로 유지하여 기존 데이터와 충돌을 피합니다.
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'basket_final_v6.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- [데이터 모델] ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(50))
    is_admin = db.Column(db.Boolean, default=False)
    grade = db.Column(db.String(20), default='RETAIL') # RETAIL / WHOLESALE

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    spec = db.Column(db.String(100))
    price_retail = db.Column(db.Integer)
    price_wholesale = db.Column(db.Integer)
    category = db.Column(db.String(50))     # 대분류명
    sub_category = db.Column(db.String(50)) # 소분류명
    image_url = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    type = db.Column(db.String(20)) # 'MAIN' 또는 'SUB'

# --- [로그인 설정] ---
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- [사용자 화면] ---
@app.route('/')
def index():
    cat = request.args.get('cat')
    sub = request.args.get('sub')
    query = Product.query.filter_by(is_active=True)
    if cat: query = query.filter_by(category=cat)
    if sub: query = query.filter_by(sub_category=sub)
    products = query.all()
    
    main_cats = Category.query.filter_by(type='MAIN').all()
    sub_cats = Category.query.filter_by(type='SUB').all()
    
    return render_template('index.html', products=products, 
                           current_cat=cat, current_sub=sub, 
                           main_cats=main_cats, sub_cats=sub_cats)

# --- [관리자: 카테고리 관리] ---
@app.route('/admin/categories', methods=['GET', 'POST'])
@login_required
def admin_categories():
    if not current_user.is_admin: return "권한 없음"
    if request.method == 'POST':
        name = request.form['name']
        cat_type = request.form['type']
        if name and not Category.query.filter_by(name=name).first():
            new_cat = Category(name=name, type=cat_type)
            db.session.add(new_cat)
            db.session.commit()
        return redirect(url_for('admin_categories'))
    
    main_cats = Category.query.filter_by(type='MAIN').all()
    sub_cats = Category.query.filter_by(type='SUB').all()
    return render_template('admin_categories.html', main_cats=main_cats, sub_cats=sub_cats)

@app.route('/admin/category/delete/<int:id>')
@login_required
def delete_category(id):
    if not current_user.is_admin: return "권한 없음"
    cat = Category.query.get(id)
    if cat:
        db.session.delete(cat)
        db.session.commit()
    return redirect(url_for('admin_categories'))

# --- [로그인 / 회원가입] ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        hashed_pw = generate_password_hash(request.form['password'])
        new_user = User(email=request.form['email'], password=hashed_pw, name=request.form['name'])
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

# --- [서버 시작] ---
with app.app_context():
    db.create_all()
    if not User.query.filter_by(email='admin@test.com').first():
        admin = User(email='admin@test.com', password=generate_password_hash('1234'), name='관리자', is_admin=True)
        db.session.add(admin)
        if not Category.query.first():
            db.session.add(Category(name='농산물직거래', type='MAIN'))
            db.session.add(Category(name='식자재마트', type='MAIN'))
            db.session.add(Category(name='다이소', type='MAIN'))
        db.session.commit()

if __name__ == '__main__':
    app.run(debug=True, port=5000)