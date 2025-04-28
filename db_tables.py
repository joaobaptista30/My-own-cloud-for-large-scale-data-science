from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(60), nullable=False)
    # relationships
    teams = db.relationship('TeamMember', back_populates='user')
    owned_teams = db.relationship('Teams', back_populates='owner')

class Teams(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.Text)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    # relationships
    owner = db.relationship('User', back_populates='owned_teams')
    members = db.relationship('TeamMember', back_populates='team')
    services = db.relationship('Service', back_populates='team')

class TeamMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'))
    role = db.Column(db.String(20))  # 'admin', 'member', etc.
    # relationships
    user = db.relationship('User', back_populates='teams')
    team = db.relationship('Teams', back_populates='members')

class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    service_type = db.Column(db.String(20))  # 'compute', 'storage', 'container', 'database'
    config = db.Column(db.JSON)  # Service configuration
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    # relationships
    team = db.relationship('Teams', back_populates='services')
    user = db.relationship('User')