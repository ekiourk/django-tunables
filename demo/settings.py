"""Settings of the demo project. Run it with scripts/demo.sh; see demo/README.md."""

from pathlib import Path

HERE = Path(__file__).resolve().parent

SECRET_KEY = "demo-only-not-a-secret"
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "tunables",
    "demo",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [HERE / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": HERE / "db.sqlite3"}}

ROOT_URLCONF = "demo.urls"
STATIC_URL = "static/"
USE_TZ = True
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/shop/settings/"

TUNABLES = {
    "CATALOGUE": "demo.catalogue.catalogue",
    "EDITABLE_GROUPS": "demo.access.editable_groups",
}
