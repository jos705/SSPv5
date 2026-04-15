from flask import Blueprint

bp = Blueprint("databases", __name__, url_prefix="/databases")

from . import routes  # noqa: E402,F401
