import os
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

from flask import Flask, request, jsonify, render_template, redirect, url_for
from db_tables import db, User
from sqlalchemy import or_
import bcrypt
import jwt
import datetime
from dotenv import load_dotenv
load_dotenv()


APP = Flask(__name__)
APP.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
APP.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("SQLALCHEMY_DATABASE_URI")
APP.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = os.getenv("SQLALCHEMY_TRACK_MODIFICATIONS")


db.init_app(APP)
with APP.app_context():
    db.create_all()
    
    
# generate 24h valid token 
def generate_token(user_id):
    payload = {
        "user_id": user_id,
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
    }
    return jwt.encode(payload, APP.config["SECRET_KEY"], algorithm="HS256")

# verify if token is still valid
def verify_token(token):
    try:
        payload = jwt.decode(token, APP.config["SECRET_KEY"], algorithms=["HS256"])
        return payload["user_id"]
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None



### pages endpoint ###
@APP.route('/')
def index():
    token = request.cookies.get("token")
    user_logged_in = False
    if token:
        user_logged_in = True
        
    return render_template('index.html',user_logged_in=user_logged_in)


@APP.route('/login')
def login():
    return render_template("login.html")

@APP.route('/account')
def account():
    username = request.cookies.get("username")
    if not username:
        return redirect(url_for("login"))
    
    return render_template('account.html',user_logged_in=username)

@APP.route('/virtualmachine')
def vm():
    username = request.cookies.get("username")
    if not username:
        return redirect(url_for("login"))
    
    return render_template('underdev.html',user_logged_in=username)

@APP.route('/diskstorage')
def diskstorage():
    username = request.cookies.get("username")
    if not username:
        return redirect(url_for("login"))
    
    return render_template('underdev.html',user_logged_in=username)

@APP.route('/container')
def containers():
    username = request.cookies.get("username")
    if not username:
        return redirect(url_for("login"))
    
    return render_template('underdev.html',user_logged_in=username)

@APP.route('/database')
def databased():
    username = request.cookies.get("username")
    if not username:
        return redirect(url_for("login"))
    
    return render_template('underdev.html',user_logged_in=username)



### API endpoints ###

@APP.route('/api/register', methods=["POST"])
def api_register():
    username = request.form.get("username")
    email = request.form.get("email")
    password = request.form.get("password")

    if not username or not email or not password:
        return jsonify({"error": "Missing required fields"}), 400

    if User.query.filter_by(username=username).first() or User.query.filter_by(email=email).first():
        #TODO now html indicar que user ja existe
        return jsonify({"error": "Username or email already exists"}), 409

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    new_user = User(username=username, email=email, password_hash=password_hash)
    db.session.add(new_user)
    db.session.commit()
    
    token = generate_token(new_user.id)
    response = redirect(url_for("account"))
    response.set_cookie("token", token, max_age=86400)
    response.set_cookie("username", username, max_age=86400)
    return response


@APP.route('/api/login', methods=["POST"])
def api_login():
    identifier = request.form.get("username")
    password = request.form.get("password")

    user = User.query.filter(or_(User.username == identifier, User.email == identifier)).first()
    if not user or not bcrypt.checkpw(password.encode("utf-8"), user.password_hash):
        #TODO no html indicar que algum esta errado
        return jsonify({"error": "Invalid username or password"}), 401

    token = generate_token(user.id)
    response = redirect(url_for("account"))
    response.set_cookie("token", token, max_age=86400)
    response.set_cookie("username", user.username, max_age=86400)
    return response
