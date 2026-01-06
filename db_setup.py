from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///basket_uncle.db'
app.config['SECRET_KEY'] = 'your_secret_key_1234' # 보안키
db = SQLAlchemy(app)

# 1. 회원 정보
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(50))
    grade = db.Column(db.String(20), default='RETAIL') # 일반(RETAIL)/도매(WHOLESALE)
    is_admin = db.Column(db.Boolean, default=False)

# 2. 상품 정보
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    price_retail = db.Column(db.Integer)    # 일반가
    price_wholesale = db.Column(db.Integer) # 도매가
    category = db.Column(db.String(50))     # 농산물/공산품
    is_active = db.Column(db.Boolean, default=True)

# 3. 주문 정보
class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    product_name = db.Column(db.String(100))
    total_price = db.Column(db.Integer)
    status = db.Column(db.String(20), default='PENDING') # 대기/완료
    created_at = db.Column(db.DateTime, default=datetime.now)

with app.app_context():
    db.create_all() # 실제 파일로 만들기