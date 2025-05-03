from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class User(db.Model):
    UserId = db.Column(db.Integer, primary_key=True)
    UserName = db.Column(db.String(80), unique=True, nullable=False)
    UserEmail = db.Column(db.String(120), unique=True, nullable=False)
    UserPasswordHash = db.Column(db.String(60), nullable=False)
    # relationships
    teams = db.relationship('TeamMember', back_populates='user')

class Team(db.Model):
    TeamId = db.Column(db.Integer, primary_key=True)
    TeamName = db.Column(db.String(80), unique=True, nullable=False)
    TeamDescription = db.Column(db.Text)
    # relationships
    members = db.relationship('TeamMember', back_populates='team')
    services = db.relationship('Service', back_populates='team')

class TeamMember(db.Model):
    UserId = db.Column(db.Integer, db.ForeignKey('user.UserId'), primary_key=True)
    TeamId = db.Column(db.Integer, db.ForeignKey('team.TeamId'), primary_key=True)
    RoleId = db.Column(db.Integer, db.ForeignKey('role.RoleId'), nullable=False)
    
    __table_args__ = (
        db.UniqueConstraint('UserId', 'TeamId', name='uix_user_team'),
    )
    # relationships
    user = db.relationship('User', back_populates='teams')
    team = db.relationship('Team', back_populates='members')
    role = db.relationship('Role', back_populates='members')

class Role(db.Model):
    RoleId = db.Column(db.Integer, primary_key=True)
    RoleName = db.Column(db.String(80), unique=True, nullable=False)
    # relationships
    members = db.relationship('TeamMember', back_populates='role')

class Service(db.Model):
    ServiceId = db.Column(db.Integer, primary_key=True)
    ServiceName = db.Column(db.String, unique=True, nullable=False)
    ServiceType = db.Column(db.String, nullable=False)
    ServiceConfig = db.Column(db.JSON, nullable=False)
    TeamId = db.Column(db.Integer, db.ForeignKey('team.TeamId'), nullable=False)
    
    # relationships
    team = db.relationship('Team', back_populates='services')