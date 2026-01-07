import os
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'basket-1234'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'basket_v12.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- [모델] ---
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

class Cart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    quantity = db.Column(db.Integer, default=1)
    product = db.relationship('Product')

# --- [로그인 설정] ---
login_manager = LoginManager()
login_manager.init_app(app)
@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

# --- [주요 경로: 404 방지용] ---
@app.route('/')
def index():
    products = Product.query.all()
    return render_template('index.html', products=products)

@app.route('/add/<int:p_id>')
def add_to_cart(p_id):
    # 테스트를 위해 로그인 체크를 잠시 끕니다. 그냥 눌러도 담기게 합니다.
    new_item = Cart(user_id=1, product_id=p_id) 
    db.session.add(new_item)
    db.session.commit()
    return redirect('/cart') # 직접 주소로 이동

@app.route('/cart')
def view_cart():
    items = Cart.query.all()
    # 대분류별 묶기
    grouped = {}
    for i in items:
        cat = i.product.category if i.product.category else "미분류"
        if cat not in grouped: grouped[cat] = []
        grouped[cat].append(i)
    return render_template('cart.html', grouped_cart=grouped, min_price=10000, delivery_fee=1900)

with app.app_context():
    db.create_all()
    # 테스트용 관리자 계정 생성
    if not User.query.filter_by(email='admin@test.com').first():
        admin = User(email='admin@test.com', password=generate_password_hash('1234'), is_admin=True)
        db.session.add(admin)
        db.session.commit()

if __name__ == '__main__':
    app.run(debug=True)