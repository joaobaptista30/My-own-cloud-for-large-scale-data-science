import os
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
from db_tables import db, User, Team, TeamMember, Role, Service
from sqlalchemy import or_, text
import bcrypt
import jwt
import datetime
from config import Config


APP = Flask(__name__)
APP.config.from_object(Config)



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
    return render_template('index.html')


@APP.route('/login')
def login():
    return render_template("login.html")

@APP.route('/account')
def account():
    if not session.get("token") or not verify_token(session.get("token")):
        return redirect(url_for("login"))
    
    page = request.args.get('page','acc')
    
    teams = db.session.execute(text('''
        SELECT t.TeamName, t.TeamDescription
        FROM team AS t
        WHERE t.TeamId IN (
            SELECT tm.TeamId
            FROM team_member AS tm
            WHERE tm.UserId IN (
                SELECT u.UserId
                FROM user AS u
                WHERE u.UserName like :username ))
    '''), {"username": session.get("username")}).fetchall()
        
    return render_template('account.html',page=page, teams=teams)

@APP.route('/virtualmachine')
def vm():
    if not session.get("token") or not verify_token(session.get("token")):
        return redirect(url_for("login"))
    
    return render_template('underdev.html')

@APP.route('/diskstorage')
def diskstorage():
    if not session.get("token") or not verify_token(session.get("token")):
        return redirect(url_for("login"))
    
    return render_template('underdev.html')

@APP.route('/container')
def containers():
    if not session.get("token") or not verify_token(session.get("token")):
        return redirect(url_for("login"))
    
    return render_template('underdev.html')

@APP.route('/database')
def databased():
    if not session.get("token") or not verify_token(session.get("token")):
        return redirect(url_for("login"))
    return render_template('underdev.html')
    



### API endpoints ###

@APP.route('/api/register', methods=["POST"])
def api_register():
    username = request.form.get("username")
    email = request.form.get("email")
    password = request.form.get("password")

    if not username or not email or not password:
        flash("Missing required fields")
        return redirect(url_for("login"))
    if User.query.filter_by(UserName=username).first() or User.query.filter_by(UserEmail=email).first():
        flash("Username or email already exists",category="register_error")
        return redirect(url_for("login"))

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    new_user = User(UserName=username, UserEmail=email, UserPasswordHash=password_hash)
    db.session.add(new_user)
    db.session.commit()
    
    token = generate_token(new_user.UserId)
    session.permanent = True
    session["token"] = token
    session["username"] = username
    
    return redirect(url_for("account", page="acc"))


@APP.route('/api/login', methods=["POST"])
def api_login():
    identifier = request.form.get("username")
    password = request.form.get("password")

    user = User.query.filter(or_(User.UserName == identifier, User.UserEmail == identifier)).first()
    if not user or not bcrypt.checkpw(password.encode("utf-8"), user.UserPasswordHash):
        flash("Invalid username or password",category="login_error")
        return redirect(url_for("login"))

    token = generate_token(user.UserId)
    session.permanent = True
    session["token"] = token
    session["username"] = user.UserName

    return redirect(url_for("account", page="acc"))


@APP.route('/api/logout')
def api_logout():
    session.clear()
    return redirect(url_for("index"))


@APP.route('/api/createteam', methods=["POST"])
def api_create_team():
    teamname = request.form.get("teamname")
    teamdescription = request.form.get("description")
    
    if Team.query.filter_by(TeamName=teamname).first():
        flash("Team already exists",category="team_error")
        return redirect(url_for("account", page="teams"))
    
    user = User.query.filter(User.UserName == session.get("username")).first()
    if not user:
        flash("User not found", category="team_error")
        return redirect(url_for("account", page="teams"))
    
    try:
        # create team
        newteam = Team(TeamName=teamname, TeamDescription=teamdescription)
        db.session.add(newteam)
        db.session.flush()
        # create user role in team
        newteammember = TeamMember(UserId=user.UserId, TeamId=newteam.TeamId, RoleId=1)
        db.session.add(newteammember)
        db.session.commit()
        
    except Exception as e:
        db.session.rollback()
        flash("Error creating team: " + str(e), category="team_error")

    
    return redirect(url_for("account", page="teams"))


'''
para ver todas as equipas de 1 user
for membership in user.teams:
    print(membership.team.TeamName)  # Follows `teams = db.relationship('TeamMember')`

'''