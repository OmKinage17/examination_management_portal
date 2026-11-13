"""
Flask Extensions module.
Defines SQLAlchemy and Flask-Session instances to avoid circular imports.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_session import Session

# SQLAlchemy database instance
db = SQLAlchemy()

# Flask-Session instance
server_session = Session()

