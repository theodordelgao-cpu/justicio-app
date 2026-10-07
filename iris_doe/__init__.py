"""Iris — agent IA de montage de DOE (Standia).

Intégration dans Justicio :

    from iris_doe import bp as iris_bp
    app.register_blueprint(iris_bp)      # servi sur /standia/AgentIA_DEO
"""

from flask import Blueprint

URL_PREFIX = "/standia/AgentIA_DEO"

bp = Blueprint(
    "iris",
    __name__,
    url_prefix=URL_PREFIX,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)

from . import routes  # noqa: E402,F401
