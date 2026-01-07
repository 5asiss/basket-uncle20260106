import os
import pandas as pd
from io import BytesIO
from flask import Flask, render_template, request, redirect, url_for, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'uncle-samchon-secret-key'
basedir = os.path.abspath(os.path.dirname(__file__))
# DB 파일 이름을 관리하기 쉽게 설정합니다.
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'uncle_basket.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- [DB 모델: 장부 설계] ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(50))
    is_admin = db.Column(db.Boolean, default=False)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50)) # 농산물, 공산품 등
    name = db.Column(db.String(100))
    price = db.Column(db.Integer)
    spec = db.Column(db.String(100))
    image_url = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True)

class Cart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    product_name = db.Column(db.String(100))
    price = db.Column(db.Integer)
    quantity = db.Column(db.Integer, default=1)

# --- [로그인 설정] ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

# --- [주요 경로(Route)] ---
@app.route('/')
def index():
    products = Product.query.filter_by(is_active=True).all()
    return render_template('index.html', products=products)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        hashed_pw = generate_password_hash(request.form['password'])
        new_user = User(email=request.form['email'], password=hashed_pw, name=request.form['name'])
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

@app.route('/add_to_cart/<int:p_id>')
@login_required
def add_to_cart(p_id):
    p = Product.query.get(p_id)
    item = Cart(user_id=current_user.id, product_name=p.name, price=p.price)
    db.session.add(item)
    db.session.commit()
    return redirect(url_for('view_cart'))

@app.route('/cart')
@login_required
def view_cart():
    items = Cart.query.filter_by(user_id=current_user.id).all()
    total = sum(i.price * i.quantity for i in items)
    return render_template('cart.html', items=items, total=total)

# --- [관리자: 엑셀 다운로드] ---
@app.route('/admin/excel')
@login_required
def download_excel():
    if not current_user.is_admin: return "권한 없음"
    orders = Cart.query.all()
    data = [{"주문자ID": o.user_id, "상품명": o.product_name, "가격": o.price} for o in orders]
    df = pd.DataFrame(data)
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, download_name="uncle_orders.xlsx", as_attachment=True)

with app.app_context():
    db.create_all()
    # 초기 관리자 생성 (비번: 1234)
    if not User.query.filter_by(email='admin@test.com').first():
        admin = User(email='admin@test.com', password=generate_password_hash('1234'), is_admin=True)
        db.session.add(admin)
        db.session.commit()

if __name__ == '__main__':
    app.run(debug=True)