"""
wsgi.py — ponto de entrada para hospedagem (PythonAnywhere, Render, gunicorn...).
O servidor importa a variável "application" daqui. Localmente use: python app.py
"""
import os

os.environ.setdefault("JUSTPLAN_PRODUCAO", "1")   # online: cookie só por HTTPS e cabeçalhos extras

from app import app as application  # noqa: E402
