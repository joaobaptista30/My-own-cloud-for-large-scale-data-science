import os
import logging
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
from db_tables import db, User, Team, TeamMember, Role, Service
from sqlalchemy import or_, text
import docker
import bcrypt
import jwt
import datetime
import openstack
from apscheduler.schedulers.background import BackgroundScheduler

from config import Config


APP = Flask(__name__)
APP.config.from_object(Config)

logger = logging.getLogger(__name__)

# connection to microstack server for resources alocation
conn = openstack.connect(cloud='microstack')

# database connection 
db.init_app(APP)
with APP.app_context():
    db.create_all()

# Initialize scheduler for backgroud checks, VM usage, etc
scheduler = BackgroundScheduler()
scheduler.start()    
    
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

# Track last activity for each VM
vm_last_activity = {}

# Auto-scaling thresholds
CPU_SCALE_UP_THRESHOLD = 80  # % CPU usage
CPU_SCALE_DOWN_THRESHOLD = 20  # % CPU usage
INACTIVITY_TIMEOUT = 300  # 5 minutes in seconds

def get_vm_metrics(server_id):
    """
    Fetch VM metrics (CPU/memory usage) from OpenStack.
    """
    try:
        cpu_usage = conn.telemetry.get_sample('cpu_util', server_id)
        memory_usage = conn.telemetry.get_sample('memory.usage', server_id) 
        return {'cpu': cpu_usage, 'memory': memory_usage}
    except Exception as e:
        logger.error(f"Error fetching metrics for server {server_id}: {e}")
        return {'cpu': 0, 'memory': 0}

def scale_vm(server_id, current_flavor, scale_direction):
    """
    Scale VM up or down by resizing to a different flavor.
    """

    FLAVOR_MAP = {'small': {'id': 'm1.small', 'ram': 1024, 'cpu': 2},
                  'medium': {'id': 'm1.medium', 'ram': 4096, 'cpu': 4},
                  'large': {'id': 'm1.large', 'ram': 8192, 'cpu': 8}}
    FLAVOR_ORDER = ['small', 'medium', 'large']  # Order for scaling

    current_flavor_idx = FLAVOR_ORDER.index(current_flavor)
    if scale_direction == 'up' and current_flavor_idx < len(FLAVOR_ORDER) - 1:
        new_flavor = FLAVOR_ORDER[current_flavor_idx + 1]
    elif scale_direction == 'down' and current_flavor_idx > 0:
        new_flavor = FLAVOR_ORDER[current_flavor_idx - 1]
    else:
        return False  # No scaling possible

    try:
        new_flavor_id = FLAVOR_MAP[new_flavor]['id']
        server = conn.compute.get_server(server_id)
        conn.compute.resize_server(server, new_flavor_id)
        conn.compute.confirm_resize(server)
        logger.info(f"Scaled VM {server_id} to {new_flavor}")
        
        # Update service config in database
        service = Service.query.filter_by(ServiceName=server.name).first()
        if service:
            service.ServiceConfig['server_size'] = new_flavor
            db.session.commit()
        return True
    except Exception as e:
        logger.error(f"Error scaling VM {server_id}: {e}")
        return False

def check_vm_activity():
    """
    Check all VMs for activity and scaling needs.
    Shutdown after 5 minutes of inactivity.
    """

    current_time = datetime.datetime.now(datetime.timezone.utc)
    for server_summary in conn.compute.servers():
        server = conn.compute.get_server(server_summary.id)

        if server.status != 'ACTIVE': # VM not running
            continue

        server_id = server.id
        metrics = get_vm_metrics(server_id)
        cpu_usage = metrics['cpu']

        if cpu_usage > 10: # Update last activity time if VM is active
            vm_last_activity[server_id] = current_time
        else: # if server inactive for +300sec (5min) shutdown
            last_activity = vm_last_activity.get(server_id, current_time)
            if (current_time - last_activity).total_seconds() > INACTIVITY_TIMEOUT and "container" not in server.name :
                try:
                    conn.compute.stop_server(server)
                    service = Service.query.filter_by(ServiceName=server.name).first()
                    service.ServiceConfig['server_status'] = 'SHUTOFF'
                    db.session.commit()
                    logger.info(f"Server: {service.ServiceName} shutdown")
                except Exception as e:
                    logger.error(f"Error shutting down VM {server_id}: {e}")

        # Auto-scaling logic
        current_flavor = service.ServiceConfig['server_size']
        if cpu_usage > CPU_SCALE_UP_THRESHOLD:
            scale_vm(server_id, current_flavor, 'up')
        elif cpu_usage < CPU_SCALE_DOWN_THRESHOLD:
            scale_vm(server_id, current_flavor, 'down')


# Schedule for routine checks 
scheduler.add_job(check_vm_activity, 'interval', minutes=1)


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
    
    user = User.query.filter_by(UserName=session.get("username")).first()
    teams = (Team.query.join(TeamMember).filter(TeamMember.UserId == user.UserId).all())

    if session.get('teamid_selected'): 
        services = Service.query.filter_by(TeamId=session.get('teamid_selected'), ServiceType='DISK').all()
        vms = Service.query.filter_by(TeamId=session.get('teamid_selected'), ServiceType='VM').all()
        return render_template('diskstorage.html', teams=teams, services=services, vms=vms)
    
    return render_template('diskstorage.html', teams=teams)

@APP.route('/container')
def containers():
    if not session.get("token") or not verify_token(session.get("token")):
        return redirect(url_for("login"))
    
    user = User.query.filter_by(UserName=session.get("username")).first()
    teams = Team.query.join(TeamMember).filter(TeamMember.UserId == user.UserId).all()
    
    if session.get('teamid_selected'):
        services = Service.query.filter_by(TeamId=session.get('teamid_selected'), ServiceType='CONTAINER').all()
        vm = Service.query.filter_by(TeamId=session.get('teamid_selected'), ServiceType='VM_CONTAINER').first()
        return render_template('containers.html', teams=teams, services=services, vm=vm)
    
    return render_template('containers.html', teams=teams)

@APP.route('/database')
def database():
    if not session.get("token") or not verify_token(session.get("token")):
        return redirect(url_for("login"))

    user = User.query.filter_by(UserName=session.get("username")).first()
    teams = Team.query.join(TeamMember).filter(TeamMember.UserId == user.UserId).all()

    if session.get('teamid_selected'):
        services = Service.query.filter_by(TeamId=session.get('teamid_selected'), ServiceType='DATABASE').all()
        return render_template('database.html', teams=teams, services=services)

    return render_template('database.html', teams=teams)
    



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
    logger.info(f"{username} register")
    
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
    logger.info(f"{user.UserName} loggin")

    return redirect(url_for("account", page="acc"))


@APP.route('/api/logout')
def api_logout():
    logger.info(f"{session["username"]} logout")
    session.clear()
    return redirect(url_for("index"))


@APP.route('/api/createteam', methods=["POST"])
def api_create_team():
    teamname = request.form.get("teamname")
    teamdescription = request.form.get("description")

    if not teamname:
        flash("Missing required fields")
        return redirect(url_for("account", page="teams"))
    
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
        logger.info(f"{session["username"]} created team {newteam.TeamId}")
        
    except Exception as e:
        db.session.rollback()
        flash("Error creating team: " + str(e), category="team_error")

    
    return redirect(url_for("account", page="teams"))


@APP.route('/api/leaveteam', methods=["POST"])
def api_leave_team():
    teamid = request.form.get("teamid")
    user = User.query.filter(User.UserName == session.get("username")).first()
    teammember = TeamMember.query.filter_by(UserId=user.UserId, TeamId=teamid).first()

    if not teamid:
        flash("Missing required fields")
        return redirect(url_for("account", page="teams"))
    
    if teammember.role.RoleName == "owner":
        members = TeamMember.query.filter_by(TeamId=teamid).count()
        if members == 1: # apenas 1 user na equipa
            team = Team.query.get(teamid)
            if team:
                logger.info(f"{user.UserName} left and team: {team.TeamID} deleted for 0 users")
                db.session.delete(team)
        else: # leave e promover novo user a owner
            next_owner = TeamMember.query.filter_by(TeamId=teamid).order_by(TeamMember.RoleId)[1]
            next_owner.RoleId = 1
            logger.info(f"{user.UserName} left team {team.TeamId} and user: {next_owner.UserId}(Id) promoted to new owner")

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
        logger.warning(f"user: {current_user.UserName} tried to edit teammember for team {team.TeamID} but does not have permission")
        return redirect(url_for("account", page="teams"))

    user = User.query.filter_by(UserName=username).first()
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("account", page="teams"))

    existing_member = TeamMember.query.filter_by(UserId=user.UserId, TeamId=teamid).first()
    if existing_member:
        existing_member.RoleId = role
        db.session.commit()
        logger.info(f"user: {current_user.UserName} edited {user.UserName} role in team {team.TeamID} to {role}")
        return redirect(url_for("account", page="teams"))

    new_member = TeamMember(UserId=user.UserId, TeamId=teamid, RoleId=role)
    db.session.add(new_member)
    db.session.commit()
    logger.info(f"user: {current_user.UserName} added {new_member.UserName} in team {team.TeamID} with role {role}")

    flash("User added successfully.", "success")
    return redirect(url_for("account", page="teams"))
    

@APP.route('/api/selectteam', methods=["POST"])
def api_select_team():
    data = request.get_json()
    team_id = data.get('team_id')
    url_req = data.get('url_req')

    if team_id:
        team = Team.query.get(team_id)
        session['teamid_selected'] = team.TeamId
        user = User.query.filter_by(UserName=session["username"]).first()
        team_member_data = TeamMember.query.filter_by(UserId=user.UserId, TeamId=team_id).first()
        session["user_role"] = Role.query.filter_by(RoleId=team_member_data.RoleId).first().RoleName
        logger.info(f"{session["username"]} is working in team {team_id}")

    return {"redirect": url_for(url_req)}, 200


@APP.route('/api/createvirtualmachine', methods=["POST"])
def api_create_virtualmachine():
    vm_name = request.form.get("vmname")
    vm_type = request.form.get("vmtype")
    vm_description = request.form.get("description")

    if not vm_name or not vm_type:
        flash("Missing fields.", "error")
        return redirect(url_for("virtualmachine"))

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
    logger.info(f"user {session["username"]} created VM: {server.name} for team {session['teamid_selected']}")

    flash("Virtual Machine created successfully", "success")
    return redirect(url_for("virtualmachine", vm_created="true"))


@APP.route('/api/vmaction', methods=["POST"])
def api_vm_action():
    action = request.form.get("action")
    vm_id = request.form.get("serverid")
    service_name = request.form.get("servicename")

    if not action or not vm_id or not service_name:
        flash("Missing fields.", "error")
        return redirect(url_for("virtualmachine"))

    service = Service.query.filter_by(ServiceName=service_name).first()

    if not service:
        flash("Unexpected error", "error")
        return redirect(url_for("virtualmachine"))

    server = conn.compute.get_server(vm_id)
    if action == "start":
        if session["user_role"] == "member":
            flash("Not authorized", "error")
            logger.error(f"user {session["username"]} not authorized to start VM: {server.name}")
            return redirect(url_for("virtualmachine", vm_updated="true"))
        
        service.ServiceConfig["server_status"] = "ACTIVE"
        db.session.commit()
        conn.compute.start_server(server)
        flash("Starting Virtual Machine successfully\nMay take a while", "success")
        logger.info(f"user {session["username"]} | started VM {server.name}")

    elif action == "stop":
        if session["user_role"] == "member":
            flash("Not authorized", "error")
            logger.error(f"user {session["username"]} not authorized to stop VM: {server.name}")
            return redirect(url_for("virtualmachine", vm_updated="true"))
        
        service.ServiceConfig["server_status"] = "SHUTOFF"
        db.session.commit()
        conn.compute.stop_server(server)
        flash("Stopping Virtual Machine successfully\nMay take a while", "success")
        logger.info(f"user {session["username"]} | stoped VM {server.name}")

    elif action == "delete":
        if session["user_role"] != "owner":
            flash("Not authorized", "error")
            logger.error(f"user {session["username"]} not authorized to delete VM: {server.name}")
            return redirect(url_for("virtualmachine", vm_updated="true"))
        
        conn.compute.delete_server(server)
        db.session.delete(service)
        db.session.commit()
        logger.info(f"user {session["username"]} | deleted VM {server.name}")

    return redirect(url_for("virtualmachine", vm_updated="true"))


@APP.route('/api/createdisk', methods=["POST"])
def api_create_disk():

    disk_name = request.form.get("diskname")
    disk_size = request.form.get("disksize")
    disk_description = request.form.get("description")
    team_id = session.get("teamid_selected")

    if not disk_name or not disk_size or not team_id:
        flash("Missing fields.", "error")
        return redirect(url_for("diskstorage"))

    volume = conn.block_storage.create_volume(
        name=disk_name,
        size=disk_size,  # Size in GB
        description=disk_description
    )
    conn.block_storage.wait_for_status(volume, status='available', interval=2, wait=300)
    
    volume_config = {
        "volume_id": volume.id,
        "volume_size": disk_size,
        "volume_description": disk_description,
        "volume_status": volume.status,
        "attached_to": None,  # VM ID to fill when user attach
        "vm_name": None # VM name that volume is attach
    }
    
    service = Service(ServiceName=disk_name, ServiceType="DISK", ServiceConfig=volume_config, TeamId=team_id)
    db.session.add(service)
    db.session.commit()

    return redirect(url_for("diskstorage", disk_created="true"))


@APP.route('/api/diskaction', methods=["POST"])
def api_disk_action():

    action = request.form.get("action")
    volume_id = request.form.get("volumeid")
    service_name = request.form.get("servicename")
    server_id = request.form.get("serverid")

    if not action or not volume_id or not service_name:
        flash("Missing fields.", "error")
        return redirect(url_for("diskstorage"))

    service = Service.query.filter_by(ServiceName=service_name).first()
    if not service:
        flash("Volume register not found in DB", "error")
        return redirect(url_for("diskstorage"))

    user = User.query.filter_by(UserName=session.get("username")).first()
    team_member = TeamMember.query.filter_by(UserId=user.UserId, TeamId=session.get("teamid_selected")).first()

    if action in ["attach", "detach"] and not server_id:
        flash("Server ID required for this action", "error")
        return redirect(url_for("diskstorage"))

    if action == "attach":
        if team_member.RoleId == 3:
            flash("Not authorized", "error")
            return redirect(url_for("diskstorage", disk_updated="false"))

        volume = conn.block_storage.get_volume(volume_id)
        server = conn.compute.get_server(server_id)
        
        # Attach volume to server
        conn.compute.create_volume_attachment(server=server, volume_id=volume_id)
        conn.block_storage.wait_for_status(volume, status='in-use', interval=2, wait=300)
        
        service = Service.query.filter_by(ServiceConfig={"volume_id": volume_id}).first()
        if service:
            service.ServiceConfig["volume_status"] = "in-use"
            service.ServiceConfig["attached_to"] = server_id
            service.ServiceConfig["vm_name"] = server.name
            db.session.commit()
            logger.info(f"user: {user.UserName} attached Disk: {service.ServiceName} to VM: {server.name}")
        else:
            flash("Volume not fund", "error")
            return redirect(url_for("diskstorage", disk_updated="false"))

    elif action == "detach":
        if team_member.RoleId == 3:
            flash("Not authorized", "error")
            return redirect(url_for("diskstorage", disk_updated="false"))

        volume = conn.block_storage.get_volume(volume_id)
        server = conn.compute.get_server(server_id)
        
        # Detach volume
        conn.compute.delete_volume_attachment(volume_id=volume_id, server=server)
        conn.block_storage.wait_for_status(volume, status='available', interval=2, wait=300)
        
        service = Service.query.filter_by(ServiceConfig={"volume_id": volume_id}).first()
        if service:
            service.ServiceConfig["volume_status"] = "available"
            service.ServiceConfig["attached_to"] = None
            service.ServiceConfig["vm_name"] = None
            db.session.commit()
            logger.info(f"user: {user.UserName} detached Disk: {service.ServiceName} from VM: {server.name}")
        else:
            flash("Volume not fund", "error")
            return redirect(url_for("diskstorage", disk_updated="false"))

    elif action == "delete":
        if team_member.RoleId != 1:
            flash("Not authorized", "error")
            return redirect(url_for("diskstorage", disk_updated="false"))

        volume = conn.block_storage.get_volume(volume_id)
        if volume.status == 'in-use':
            flash("Volume in use, can't delete", "error")
            return redirect(url_for("diskstorage", disk_updated="false"))
        
        # Delete from OpenStack
        conn.block_storage.delete_volume(volume)
        
        service = Service.query.filter_by(ServiceConfig={"volume_id": volume_id}).first()
        if service:
            logger.info(f"user: {user.UserName} deleted Disk: {service.ServiceName}")
            db.session.delete(service)
            db.session.commit()
        else:
            flash("Volume not fund", "error")
            return redirect(url_for("diskstorage", disk_updated="false"))
        
    return redirect(url_for("diskstorage", disk_updated="true"))


@APP.route('/api/createcontainer', methods=["POST"])
def api_create_container():
    container_name = request.form.get("containername")
    image = "nginx:latest"
    ports = "80:80".split(",")
    team_id = session.get("teamid_selected")

    if not container_name or not team_id:
        flash("Missing fields.", "error")
        return redirect(url_for("containers"))
    
    if Service.query.filter_by(ServiceName=container_name).first():
        flash("Container name already exists", "error")
        return redirect(url_for("containers"))

    vm_container_service = Service.query.filter_by(ServiceType="VM_CONTAINER", TeamId=team_id).first()

    if not vm_container_service: # create VM to host containers if it doesn't exist
        network = conn.network.find_network('test')

        server = conn.compute.create_server(
            name=f"container_vm_{team_id}",
            image_id=conn.compute.find_image('cirros').id,
            flavor_id=conn.compute.find_flavor(f"m1.medium").id,
            networks=[{"uuid": network.id}],
            key_name="mykey"
        )
        server = conn.compute.wait_for_server(server, status='ACTIVE', failures=['ERROR'], interval=2, wait=300)
        
        # Allocate and associate a floating IP
        external_network = conn.network.find_network('external')
        floating_ip = conn.network.create_ip(floating_network_id=external_network.id)

        ports = list(conn.network.ports(device_id=server.id))
        server_port = ports[0]
        conn.network.update_ip(floating_ip, port_id=server_port.id)

        server_config = {"server_description": f"vm for container of team {team_id}",
                        "server_id": server.id,
                        "server_status": server.status,
                        "server_size": "medium",
                        "server_ip": floating_ip.floating_ip_address}

        vm_container_service = Service(ServiceName=f"container_vm_{team_id}", ServiceType="VM_CONTAINER", ServiceConfig=server_config, TeamId=session['teamid_selected'])
        db.session.add(vm_container_service)
        db.session.commit()

    
    # Get VM's floating IP
    vm_ip = vm_container_service.ServiceConfig["server_ip"]
    vm_id = vm_container_service.ServiceConfig["server_id"]

    client = docker.DockerClient(base_url=f"tcp://{vm_ip}:2375")
    if not client:
        flash("error creating container","error")
        return redirect(url_for("containers"))


    # Map ports (e.g., "80:80" means host_port:container_port)
    port_mappings = {f"{port.split(':')[1]}/tcp": int(port.split(':')[0]) for port in ports}
    container = client.containers.run(
        image=image,
        name=container_name,
        ports=port_mappings,
        detach=True
    )

    container_config = {
        "container_id": container.id,
        "container_image": image,
        "container_status": container.status,
        "container_ports": ports,
        "vm_id": vm_id
    }
    service = Service(ServiceName=container_name, ServiceType="CONTAINER", ServiceConfig=container_config, TeamId=team_id)
    db.session.add(service)
    db.session.commit()
    logger.info(f"Created container {container_name} for team {team_id}")
    client.close()
    
    return redirect(url_for("containers", container_created="true"))


@APP.route('/api/containeraction', methods=["POST"])
def api_container_action():
    action = request.form.get("action")
    container_id = request.form.get("containerid")
    service_name = request.form.get("servicename")
    vm_id = request.form.get("vmid")
    team_id = session.get("teamid_selected")

    if not action or not container_id or not service_name or not vm_id:
        flash("Missing fields.", "error")
        return redirect(url_for("containers"))

    service = Service.query.filter_by(ServiceName=service_name, TeamId=team_id).first()
    if not service:
        flash("Container not found", "error")
        return redirect(url_for("containers"))

    vm_service = Service.query.filter_by(ServiceType="VM_CONTAINER", TeamId=team_id).first()
    if not vm_service:
        flash("VM not found", "error")
        return redirect(url_for("containers"))
    
    vm_ip = vm_service.ServiceConfig["server_ip"]
    user_role = session.get("user_role")

    client = docker.DockerClient(base_url=f"tcp://{vm_ip}:2375")
    if not client:
        flash("Docker client not fund, contact admin", "error")
        return redirect(url_for("containers"))
    
    if action == "start":
        if user_role == "member":
            client.close()
            flash("Not authorized", "error")
            return redirect(url_for("containers"))
        
        container = client.containers.get(container_id)
        container.start()
        service = Service.query.filter_by(ServiceConfig={"container_id": container_id}).first()
        if service:
            service.ServiceConfig["container_status"] = "running"
            db.session.commit()
        logger.info(f"Started container {container_id}")

    elif action == "stop":
        if user_role == "member":
            client.close()
            flash("Not authorized", "error")
            return redirect(url_for("containers"))
        
        container = client.containers.get(container_id)
        container.stop()
        service = Service.query.filter_by(ServiceConfig={"container_id": container_id}).first()
        if service:
            service.ServiceConfig["container_status"] = "stopped"
            db.session.commit()
        logger.info(f"Stopped container {container_id}")

    elif action == "delete":
        if user_role != "owner":
            client.close()
            flash("Not authorized", "error")
            return redirect(url_for("containers"))
        
        container = client.containers.get(container_id)
        container.remove(force=True)  # Stop before removing
        service = Service.query.filter_by(ServiceConfig={"container_id": container_id}, TeamId=team_id).first()
        if service:
            db.session.delete(service)
            db.session.commit()
        logger.info(f"Deleted container {container_id} for team {team_id}")

    client.close()
    return redirect(url_for("containers", container_updated="true"))


@APP.route('/api/createdatabase', methods=["POST"])
def api_create_database():

    db_name = request.form.get("dbname")
    db_description = request.form.get("description")
    team_id = session.get("teamid_selected")

    db_type = "mysql" 
    db_flavor = "medium"

    if not db_name or not team_id:
        flash("Missing fields.", "error")
        return redirect(url_for("database"))

    if Service.query.filter_by(ServiceName=db_name).first():
        flash("Database name already exists", "error")
        return redirect(url_for("database"))


    flavor = conn.compute.find_flavor(f"m1.{db_flavor}")
    datastore = {'type': db_type}

    db_instance = conn.database.create_instance(
        name=db_name,
        flavor_id=flavor.id,
        datastore=datastore,
        description=db_description
    )
    conn.database.wait_for_instance(db_instance, status='ACTIVE', interval=2, wait=600)

    db_config = {
        "db_id": db_instance.id,
        "db_description": db_description,
        "db_status": db_instance.status,
        "connection_string": db_instance.connection_string
    }

    service = Service(ServiceName=db_name, ServiceType="DATABASE", ServiceConfig=db_config, TeamId=team_id)
    db.session.add(service)
    db.session.commit()
    logger.info(f"user {session['username']} created Database: {db_name} for team {team_id}")

    flash("Database created successfully", "success")
    return redirect(url_for("database", db_created="true"))


@APP.route('/api/dbaction', methods=["POST"])
def api_database_action():

    db_id = request.form.get("db_id")
    service_name = request.form.get("servicename")
    action = request.form.get("action")

    if not action or not db_id or not service_name:
        flash("Missing fields.", "error")
        return redirect(url_for("database"))

    service = Service.query.filter_by(ServiceName=service_name).first()
    if not service:
        flash("Database not found", "error")
        return redirect(url_for("database"))

    user = User.query.filter_by(UserName=session.get("username")).first()
    team_member = TeamMember.query.filter_by(UserId=user.UserId, TeamId=session.get("teamid_selected")).first()

    if action == "delete":
        if team_member.RoleId != 1:
            flash("Not authorized", "error")
            return redirect(url_for("database", db_updated="false"))

        try:
            conn.database.delete_instance(db_id)
            db.session.delete(service)
            db.session.commit()
            logger.info(f"user: {user.UserName} deleted Database: {service.ServiceName}")
            flash("Database deleted successfully", "success")
        except Exception as e:
            logger.error(f"Error deleting database {service_name}: {e}")
            flash(f"Error deleting database: {str(e)}", "error")

    return redirect(url_for("database", db_updated="true"))
