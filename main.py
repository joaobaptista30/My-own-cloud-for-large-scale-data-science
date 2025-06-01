import os
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
from db_tables import db, User, Team, TeamMember, Role, Service
from sqlalchemy import or_, text
import bcrypt
import jwt
import datetime
import openstack
import time

from config import Config


APP = Flask(__name__)
APP.config.from_object(Config)

conn = openstack.connect(cloud='microstack')

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
    
    user = User.query.filter_by(UserName=session.get("username")).first()
    
    teams = (db.session.query(Team, Role.RoleName)
            .join(TeamMember, Team.TeamId == TeamMember.TeamId)
            .join(Role, TeamMember.RoleId == Role.RoleId)
            .filter(TeamMember.UserId == user.UserId)
            .all())
    
    team_data = [{'TeamId': team.TeamId,
                'TeamName': team.TeamName,
                'TeamDescription': team.TeamDescription,
                'RoleName': role_name}
                for team, role_name in teams]
            
    return render_template('account.html',page=page, teams=team_data)

@APP.route('/virtualmachine')
def virtualmachine():
    if not session.get("token") or not verify_token(session.get("token")):
        return redirect(url_for("login"))
    
    user = User.query.filter_by(UserName=session.get("username")).first()
    teams = (Team.query.join(TeamMember).filter(TeamMember.UserId == user.UserId).all())

    if session.get('teamid_selected'): 
        services = Service.query.filter_by(TeamId=session.get('teamid_selected'), ServiceType='VM').all()
        return render_template('virtualmachine.html', teams=teams, services=services)

    return render_template('virtualmachine.html', teams=teams)

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


@APP.route('/api/leaveteam', methods=["POST"])
def api_leave_team():
    teamid = request.form.get("teamid")
    user = User.query.filter(User.UserName == session.get("username")).first()
    teammember = TeamMember.query.filter_by(UserId=user.UserId, TeamId=teamid).first()
    
    if teammember.role.RoleName == "owner":
        members = TeamMember.query.filter_by(TeamId=teamid).count()
        if members == 1: # apenas 1 user na equipa
            team = Team.query.get(teamid)
            if team:
                db.session.delete(team)
        else: # leave e promover novo user a owner
            next_owner = TeamMember.query.filter_by(TeamId=teamid).order_by(TeamMember.RoleId)[1]
            next_owner.RoleId = 1

    db.session.delete(teammember)
    db.session.commit()
        
    return redirect(url_for("account", page="teams"))


@APP.route('/api/addteammember', methods=["POST"])
def api_add_teammember():
    teamid = request.form.get("teamid")
    username = request.form.get("username")
    role = request.form.get("role")

    if not teamid or not username or not role:
        flash("Missing fields.", "error")
        return redirect(url_for("account", page="teams"))

    team = Team.query.filter_by(TeamId=teamid).first()
    if not team:
        flash("Team not found.", "error")
        return redirect(url_for("account", page="teams"))

    current_user = User.query.filter_by(UserName=session.get("username")).first()
    userrole = TeamMember.query.filter_by(UserId=current_user.UserId, TeamId=teamid).first()
    if userrole.RoleId == 3:
        flash("You don't have permission to add members.", "error")
        return redirect(url_for("account", page="teams"))

    user = User.query.filter_by(UserName=username).first()
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("account", page="teams"))

    existing_member = TeamMember.query.filter_by(UserId=user.UserId, TeamId=teamid).first()
    if existing_member:
        existing_member.RoleId = role
        db.session.commit()
        return redirect(url_for("account", page="teams"))

    new_member = TeamMember(UserId=user.UserId, TeamId=teamid, RoleId=role)
    db.session.add(new_member)
    db.session.commit()

    flash("User added successfully.", "success")
    return redirect(url_for("account", page="teams"))
    

@APP.route('/api/selectteam', methods=["POST"])
def api_select_team():
    data = request.get_json()
    team_id = data.get('team_id')

    if team_id:
        team = Team.query.get(team_id)
        session['teamid_selected'] = team.TeamId
        user = User.query.filter_by(UserName=session["username"]).first()
        team_member_data = TeamMember.query.filter_by(UserId=user.UserId, TeamId=team_id).first()
        session["user_role"] = Role.query.filter_by(RoleId=team_member_data.RoleId).first().RoleName

    return {"redirect": url_for("virtualmachine")}, 200


@APP.route('/api/createvirtualmachine', methods=["POST"])
def api_create_virtualmachine():
    vm_name = request.form.get("vmname")
    vm_type = request.form.get("vmtype")
    vm_description = request.form.get("description")

    if Service.query.filter_by(ServiceName=vm_name).all():
        flash("Service name already exists", "error")
        return redirect(url_for("virtualmachine"))

    network = conn.network.find_network('test')

    server = conn.compute.create_server(
        name=vm_name,
        image_id=conn.compute.find_image('cirros').id,
        flavor_id=conn.compute.find_flavor(f"m1.{vm_type}").id,
        networks=[{"uuid": network.id}],
        key_name="mykey"
    )

    # Wait until it's active
    server = conn.compute.wait_for_server(server, status='ACTIVE', failures=['ERROR'], interval=2, wait=300)
    
    # Allocate and associate a floating IP
    external_network = conn.network.find_network('external')
    floating_ip = conn.network.create_ip(floating_network_id=external_network.id)

    ports = list(conn.network.ports(device_id=server.id))
    server_port = ports[0]
    conn.network.update_ip(floating_ip, port_id=server_port.id)



    server_config = {"server_description": vm_description,
                     "server_id": server.id,
                     "server_status": server.status,
                     "server_size": vm_type,
                     "server_ip": floating_ip.floating_ip_address}

    service_created = Service(ServiceName=vm_name, ServiceType="VM", ServiceConfig=server_config, TeamId=session['teamid_selected'])
    db.session.add(service_created)
    db.session.commit()

    flash("Virtual Machine created successfully", "success")
    return redirect(url_for("virtualmachine", vm_created="true"))


@APP.route('/api/vmaction', methods=["POST"])
def api_vm_action():
    action = request.form.get("action")
    vm_id = request.form.get("serverid")
    service_name = request.form.get("servicename")

    service = Service.query.filter_by(ServiceName=service_name).first()

    if not service:
        flash("Unexpected error", "error")
        return redirect(url_for("virtualmachine"))

    server = conn.compute.get_server(vm_id)
    if action == "start":
        if session["user_role"] == "member":
            flash("Not authorized", "error")
            return redirect(url_for("virtualmachine", vm_updated="true"))
        
        service.ServiceConfig["server_status"] = "ACTIVE"
        db.session.commit()
        conn.compute.start_server(server)
        flash("Starting Virtual Machine successfully\nMay take a while", "success")

    elif action == "stop":
        if session["user_role"] == "member":
            flash("Not authorized", "error")
            return redirect(url_for("virtualmachine", vm_updated="true"))
        
        service.ServiceConfig["server_status"] = "SHUTOFF"
        db.session.commit()
        conn.compute.stop_server(server)
        flash("Stopping Virtual Machine successfully\nMay take a while", "success")

    elif action == "delete":
        if session["user_role"] != "owner":
            flash("Not authorized", "error")
            return redirect(url_for("virtualmachine", vm_updated="true"))
        
        conn.compute.delete_server(server)
        db.session.delete(service)
        db.session.commit()

    return redirect(url_for("virtualmachine", vm_updated="true"))

'''
para ver todas as equipas de 1 user
for membership in user.teams:
    print(membership.team.TeamName)  # Follows `teams = db.relationship('TeamMember')`

'''