"""Hugo — agent IA de devis (Standia), servi sur /standia/AgentIA_Devis.

    from devis_agent import bp as hugo_bp
    app.register_blueprint(hugo_bp)
"""

from flask import Blueprint

bp = Blueprint("hugo", __name__, url_prefix="/standia/AgentIA_Devis",
               template_folder="templates", static_folder="static", static_url_path="/static")

from . import routes  # noqa: E402,F401
