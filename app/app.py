# Kitap.AI — №11 зертханалық жұмыс
# Пайдаланушы бөлігі + әкімші панелі + PostgreSQL primary/standby
#
# Сұрауларды бағыттау:
#   primary (WRITE_DB_URL): тіркелу, кіру, рөлді тексеру, профиль, сауалнаманы жазу,
#                           өнімдер мен кеңселердің CRUD әрекеттері
#   standby (READ_DB_URL) : каталог, өнім беті, байланыстар, пайдаланушылар тізімі,
#                           сауалнама есебі

import csv
import io
import os
import re
import secrets
import time
from functools import wraps

import click
import psycopg2
import psycopg2.extras
from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only"),
    SESSION_COOKIE_HTTPONLY=True,      # JavaScript cookie-ді оқи алмайды
    SESSION_COOKIE_SAMESITE="Lax",     # басқа сайттан жіберілген POST-қа cookie қосылмайды
    # Nginx арқылы тек HTTPS жұмыс істегенде cookie тек шифрланған арнамен жіберіледі
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
)
# Nginx алдында тұрғанда X-Forwarded-For / X-Forwarded-Proto / Host тақырыптарын ескеру:
# request.remote_addr — клиенттің нақты IP-і, url_for(..., _external=True) — https://lab.local/...
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Осы сұрауға жауап берген web данасының атауы (Docker контейнерінің hostname-ы)
INSTANCE = os.environ.get("HOSTNAME", "unknown")

WRITE_DB_URL = os.environ.get("WRITE_DB_URL")
READ_DB_URL = os.environ.get("READ_DB_URL")
GITHUB_URL = os.environ.get("GITHUB_URL", "https://github.com/")

CATEGORIES = ["Оқырмандарға", "Бизнеске", "Білім беруге"]
INTENTS = {
    "buy": "Сатып алуға / жазылуға дайынмын",
    "try": "Алдымен тегін сынап көремін",
    "later": "Кейінірек қарастырамын",
    "no": "Қызықпаймын",
}
GENRES = ["Роман", "Тарихи", "Поэзия", "Әңгіме", "Ертегі", "Ғылыми-көпшілік", "Өзін-өзі дамыту"]
FORMATS = {"ebook": "Электрондық кітап", "audio": "Аудиокітап", "both": "Кітап + аудио"}
COVER_COLORS = {"Роман": "#0f5257", "Тарихи": "#8a4b2a", "Поэзия": "#6b3fa0", "Әңгіме": "#1f4e8c",
                "Ертегі": "#2e7d32", "Ғылыми-көпшілік": "#455a64", "Өзін-өзі дамыту": "#b5562f"}
# Кітап оқуға рұқсат беретін жазылым өнімдері
SUBSCRIPTION_SLUGS = ("kitap-ai-premium", "kitap-ai-otbasy")
SUBSCRIPTION_DAYS = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SLUG_RE = re.compile(r"^[a-z0-9-]{3,140}$")


# =====================================================================
# 1. Дерекқормен жұмыс
# =====================================================================

def _query(url, sql, params=None, fetch="all"):
    conn = psycopg2.connect(url, connect_timeout=3)
    try:
        with conn:  # сәтті болса COMMIT, қате болса ROLLBACK
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                if fetch == "one":
                    return cur.fetchone() if cur.description else None
                if fetch == "all":
                    return cur.fetchall() if cur.description else []
                return None
    finally:
        conn.close()


def primary(sql, params=None, fetch="all"):
    """Жазу және маңызды оқу (кіру, рөл) — тек primary."""
    return _query(WRITE_DB_URL, sql, params, fetch)


def standby(sql, params=None, fetch="all"):
    """Ашық каталог пен есептерді оқу — standby."""
    return _query(READ_DB_URL, sql, params, fetch)


def seen_on_standby(sql, params, tries=5, delay=0.3):
    """Қысқа қайталап оқу: primary-дегі өзгеріс standby-ге жеткенін тексереді."""
    for _ in range(tries):
        try:
            if standby(sql, params):
                return True
        except psycopg2.Error:
            return False
        time.sleep(delay)
    return False


def replication_status():
    """Standby күйі және репликация кідірісі (секунд)."""
    try:
        row = standby("""SELECT pg_is_in_recovery() AS in_recovery,
                                EXTRACT(EPOCH FROM now() - pg_last_xact_replay_timestamp()) AS lag""",
                      fetch="one")
        return {"ok": True, "in_recovery": row["in_recovery"],
                "lag": None if row["lag"] is None else round(float(row["lag"]), 2)}
    except psycopg2.Error:
        return {"ok": False, "in_recovery": None, "lag": None}


@app.errorhandler(psycopg2.Error)
def db_error(error):
    app.logger.error("Дерекқор қатесі: %s", error)
    return render_template("error.html", code=503, title="Дерекқор уақытша қолжетімсіз",
                           message="Сервер сұрауды орындай алмады. Бірнеше секундтан кейін бетті жаңартыңыз."), 503


@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", code=403, title="Рұқсат жоқ",
                           message="Бұл бет тек әкімшіге арналған."), 403


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404, title="Бет табылмады",
                           message="Сұралған бет немесе өнім жоқ."), 404


@app.errorhandler(400)
def bad_request(_):
    return render_template("error.html", code=400, title="Сұрау қабылданбады",
                           message="Форманың қауіпсіздік белгісі ескірген. Бетті жаңартып, қайталап көріңіз."), 400


# =====================================================================
# 2. Қауіпсіздік: CSRF, сессия, рөлдер
# =====================================================================

def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    """Өзгеріс енгізетін әр POST формасы жасырын csrf_token өрісін жіберуі тиіс."""
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400)


@app.before_request
def load_user():
    """Сессияда тек user_id сақталады; аты мен рөлі әр сұрауда primary-ден оқылады."""
    init_db_once()
    g.user = None
    uid = session.get("user_id")
    if uid:
        try:
            g.user = primary("SELECT id, email, full_name, role FROM users WHERE id = %s",
                             (uid,), fetch="one")
        except psycopg2.Error:
            g.user = None
        if g.user is None:
            session.pop("user_id", None)


@app.context_processor
def inject_globals():
    return {"current_user": g.get("user"), "github_url": GITHUB_URL, "intents": INTENTS, "instance": INSTANCE,
            "formats": FORMATS, "cover_colors": COVER_COLORS, "subscription_slugs": SUBSCRIPTION_SLUGS}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Алдымен жүйеге кіріңіз.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """Рөл серверде, primary-ден оқылған мән бойынша тексеріледі (батырманы жасыру жеткіліксіз)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Алдымен жүйеге кіріңіз.", "error")
            return redirect(url_for("login", next=request.path))
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# =====================================================================
# 3. Сұлба, әкімші аккаунты: init (.env) және қауіпсіз CLI команда
# =====================================================================

def create_admin(email, full_name, password):
    return primary(
        """INSERT INTO users (email, password_hash, full_name, role)
           VALUES (%s, %s, %s, 'admin')
           ON CONFLICT (email) DO UPDATE SET role = 'admin'
           RETURNING id""",
        (email.lower(), generate_password_hash(password), full_name), fetch="one")


SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")
_db_ready = False


def init_db_once():
    """Бірінші сұрау кезінде primary-де сұлбаны (idempotent) және .env әкімшісін дайындайды.
    №10 жұмыстың томдары сақталса да, жаңа кестелер осылай пайда болады; өзгерістер
    репликация арқылы standby-ге өтеді."""
    global _db_ready
    if _db_ready:
        return
    try:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            primary(f.read(), fetch=None)
        email, pwd = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD")
        if email and pwd:
            primary("""INSERT INTO users (email, password_hash, full_name, role)
                       VALUES (%s, %s, 'Kitap.AI әкімшісі', 'admin')
                       ON CONFLICT (email) DO NOTHING""",
                    (email.lower(), generate_password_hash(pwd)), fetch=None)
        _db_ready = True
    except psycopg2.Error as e:
        app.logger.warning("Дерекқорды дайындау мүмкін болмады: %s", e)


@app.cli.command("create-admin")
@click.option("--email", prompt="Email")
@click.option("--name", "full_name", prompt="Аты-жөні")
@click.password_option("--password", prompt="Пароль")
def create_admin_command(email, full_name, password):
    """docker compose exec web flask --app app create-admin"""
    init_db_once()
    row = create_admin(email, full_name, password)
    click.echo(f"Әкімші дайын (id = {row['id']}).")


# =====================================================================
# 4. Ашық беттер
# =====================================================================

@app.route("/")
def index():
    products, books_list = [], []
    try:
        products = standby("""SELECT slug, name, summary, image_url FROM products
                              WHERE is_active ORDER BY id LIMIT 3""")
        books_list = standby("""SELECT id, title, author, genre, book_format, is_free FROM books
                                WHERE is_active ORDER BY id DESC LIMIT 6""")
    except psycopg2.Error:
        pass  # басты бет дерекқорсыз да ашылады
    return render_template("index.html", products=products, books=books_list)


@app.route("/products")
def products():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "")
    sql = """SELECT slug, name, summary, price, price_model, image_url, category
             FROM products WHERE is_active"""
    params = []
    if q:
        sql += " AND (name ILIKE %s OR summary ILIKE %s)"
        params += [f"%{q}%", f"%{q}%"]
    if category in CATEGORIES:
        sql += " AND category = %s"
        params.append(category)
    sql += " ORDER BY price NULLS FIRST, id"
    return render_template("products.html", products=standby(sql, params),
                           q=q, category=category, categories=CATEGORIES)


@app.route("/products/<slug>")
def product_page(slug):
    product = standby("SELECT * FROM products WHERE slug = %s AND is_active", (slug,), fetch="one")
    if product is None:
        abort(404)
    paragraphs = [p.strip() for p in product["description"].split("\n\n") if p.strip()]
    features = [f.strip() for f in product["features"].splitlines() if f.strip()]
    stats = standby("""SELECT count(*) AS n, round(avg(rating), 1) AS avg
                       FROM survey_responses WHERE product_id = %s""", (product["id"],), fetch="one")
    office = standby("SELECT phone, email FROM offices ORDER BY id LIMIT 1", fetch="one")
    current_sub = active_subscription(g.user["id"]) if g.user else None
    return render_template("product.html", p=product, paragraphs=paragraphs,
                           features=features, stats=stats, office=office, current_sub=current_sub)


@app.route("/contacts")
def contacts():
    offices = standby("SELECT * FROM offices ORDER BY id")
    for o in offices:
        lat, lon = float(o["latitude"]), float(o["longitude"])
        o["map_src"] = ("https://www.openstreetmap.org/export/embed.html?bbox="
                        f"{lon - 0.01:.5f},{lat - 0.005:.5f},{lon + 0.01:.5f},{lat + 0.005:.5f}"
                        f"&layer=mapnik&marker={lat:.6f},{lon:.6f}")
        o["map_link"] = f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=16/{lat:.6f}/{lon:.6f}"
    return render_template("contacts.html", offices=offices)


@app.route("/health")
def health():
    """Web данасының және екі дерекқордың күйі (JSON). Құпия мәндер мен connection string шығарылмайды."""
    result = {"status": "ok", "instance": INSTANCE,
              "primary_recovery": None, "standby_recovery": None, "replication_lag_seconds": None}
    try:
        result["primary_recovery"] = primary("SELECT pg_is_in_recovery() AS recovery", fetch="one")["recovery"]
    except psycopg2.Error:
        result["status"] = "error"
        result["error"] = "primary қолжетімсіз"
    rep = replication_status()
    if rep["ok"]:
        result["standby_recovery"] = rep["in_recovery"]
        result["replication_lag_seconds"] = rep["lag"]
    elif result["status"] == "ok":
        result["status"] = "degraded"          # web жұмыс істейді, бірақ standby-ден оқу мүмкін емес
        result["error"] = "standby қолжетімсіз"
    # Docker healthcheck және Nginx үшін: primary жоқ болса 503, әйтпесе 200
    return jsonify(result), (503 if result["status"] == "error" else 200)


# =====================================================================
# 5. Тіркелу, кіру, шығу, профиль
# =====================================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    form = {"full_name": "", "email": ""}
    if request.method == "POST":
        form = {"full_name": request.form.get("full_name", "").strip(),
                "email": request.form.get("email", "").strip().lower()}
        password = request.form.get("password", "")
        errors = []
        if not 2 <= len(form["full_name"]) <= 120:
            errors.append("Аты-жөні 2–120 таңбадан тұруы керек.")
        if not EMAIL_RE.match(form["email"]) or len(form["email"]) > 160:
            errors.append("Email дұрыс емес.")
        if len(password) < 8:
            errors.append("Пароль кемінде 8 таңбадан тұруы керек.")
        elif password != request.form.get("password2", ""):
            errors.append("Парольдер сәйкес келмейді.")
        if not errors:
            # role формадан қабылданбайды: әрқашан 'user'
            row = primary("""INSERT INTO users (email, password_hash, full_name, role)
                             VALUES (%s, %s, %s, 'user')
                             ON CONFLICT (email) DO NOTHING RETURNING id""",
                          (form["email"], generate_password_hash(password), form["full_name"]),
                          fetch="one")
            if row is None:
                errors.append("Бұл email бұрын тіркелген.")
            else:
                session.clear()
                session["user_id"] = row["id"]
                flash("Тіркелу сәтті аяқталды. Kitap.AI-ға қош келдіңіз!", "ok")
                return redirect(url_for("books"))
        for e in errors:
            flash(e, "error")
        return render_template("register.html", form=form), 400
    return render_template("register.html", form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    email = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        # Кіру primary-де тексеріледі: жаңа тіркелген пайдаланушы репликацияны күтпейді
        user = primary("SELECT id, password_hash, role FROM users WHERE email = %s",
                       (email,), fetch="one")
        if user is None or not check_password_hash(user["password_hash"], request.form.get("password", "")):
            flash("Email немесе пароль қате.", "error")
            return render_template("login.html", email=email), 401
        session.clear()                 # сессияны жаңарту (session fixation-нан қорғау)
        session["user_id"] = user["id"]
        flash("Жүйеге сәтті кірдіңіз.", "ok")
        nxt = request.args.get("next", "")
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = url_for("admin_books") if user["role"] == "admin" else url_for("books")
        return redirect(nxt)
    return render_template("login.html", email=email)


@app.post("/logout")
def logout():
    session.clear()
    flash("Сіз жүйеден шықтыңыз.", "ok")
    return redirect(url_for("index"))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        new_pwd = request.form.get("new_password", "")
        errors = []
        if not 2 <= len(full_name) <= 120:
            errors.append("Аты-жөні 2–120 таңбадан тұруы керек.")
        if new_pwd:
            row = primary("SELECT password_hash FROM users WHERE id = %s", (g.user["id"],), fetch="one")
            if not check_password_hash(row["password_hash"], request.form.get("current_password", "")):
                errors.append("Ағымдағы пароль қате.")
            elif len(new_pwd) < 8:
                errors.append("Жаңа пароль кемінде 8 таңбадан тұруы керек.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("profile.html", responses=my_responses(),
                                   sub=active_subscription(g.user["id"])), 400
        primary("UPDATE users SET full_name = %s WHERE id = %s", (full_name, g.user["id"]), fetch=None)
        if new_pwd:
            primary("UPDATE users SET password_hash = %s WHERE id = %s",
                    (generate_password_hash(new_pwd), g.user["id"]), fetch=None)
        flash("Профиль primary серверде жаңартылды.", "ok")
        return redirect(url_for("profile"))
    return render_template("profile.html", responses=my_responses(),
                           sub=active_subscription(g.user["id"]))


def my_responses():
    return primary("""SELECT s.rating, s.intent, s.comment, s.created_at, p.name
                      FROM survey_responses s JOIN products p ON p.id = s.product_id
                      WHERE s.user_id = %s ORDER BY s.id DESC""", (g.user["id"],))


# =====================================================================
# 6. Сауалнама (тек тіркелген пайдаланушылар — топ шешімі)
# =====================================================================

@app.route("/survey", methods=["GET", "POST"])
@login_required
def survey():
    products_list = standby("SELECT id, name FROM products WHERE is_active ORDER BY id")
    form = {"product_id": request.args.get("product", ""), "rating": "", "intent": "", "comment": ""}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in ("product_id", "rating", "intent", "comment")}
        errors = []
        if not form["product_id"].isdigit():
            errors.append("Өнімді таңдаңыз.")
        if form["rating"] not in {"1", "2", "3", "4", "5"}:
            errors.append("1-ден 5-ке дейін баға қойыңыз.")
        if form["intent"] not in INTENTS:
            errors.append("Ниетіңізді тізімнен таңдаңыз.")
        if len(form["comment"]) > 1000:
            errors.append("Пікір 1000 таңбадан аспауы керек.")
        if not errors:
            row = primary("""INSERT INTO survey_responses (user_id, product_id, rating, intent, comment)
                             SELECT %s, id, %s, %s, %s FROM products
                             WHERE id = %s AND is_active
                             RETURNING id""",
                          (g.user["id"], int(form["rating"]), form["intent"],
                           form["comment"] or None, int(form["product_id"])), fetch="one")
            if row is None:
                errors.append("Таңдалған өнім табылмады.")
            else:
                flash(f"Рахмет! Жауабыңыз сақталды (№ {row['id']}).", "ok")
                return redirect(url_for("survey"))
        for e in errors:
            flash(e, "error")
        return render_template("survey.html", products=products_list, form=form), 400
    return render_template("survey.html", products=products_list, form=form)


# =====================================================================
# 7. Әкімші панелі: өнімдер
# =====================================================================

def product_form_data(form):
    d = {k: form.get(k, "").strip() for k in
         ("slug", "name", "summary", "description", "price", "image_url",
          "category", "features", "audience", "price_model")}
    d["description"] = d["description"].replace("\r\n", "\n")
    d["features"] = d["features"].replace("\r\n", "\n")
    d["is_active"] = form.get("is_active") == "on"
    errors = []
    if not SLUG_RE.match(d["slug"]):
        errors.append("Slug: 3–140 таңба, тек латын кіші әріптері, сан және дефис.")
    if not 2 <= len(d["name"]) <= 140:
        errors.append("Атауы 2–140 таңба болуы керек.")
    if not 20 <= len(d["summary"]) <= 300:
        errors.append("Қысқаша сипаттама 20–300 таңба болуы керек.")
    paragraphs = [p for p in d["description"].split("\n\n") if p.strip()]
    if len(paragraphs) < 2 or len(d["description"]) < 150:
        errors.append("Толық сипаттамада бос жолмен бөлінген кемінде 2 абзац болуы керек (≥150 таңба).")
    if "lorem ipsum" in (d["description"] + d["summary"]).lower():
        errors.append("Lorem ipsum үлгі мәтіні қабылданбайды.")
    if d["price"]:
        try:
            d["price"] = round(float(d["price"].replace(" ", "").replace(",", ".")), 2)
            if d["price"] < 0:
                raise ValueError
        except ValueError:
            errors.append("Баға 0-ден кем емес сан болуы керек.")
    else:
        d["price"] = None
    if d["image_url"] and not (d["image_url"].startswith("/static/") or d["image_url"].startswith("https://")):
        errors.append("Сурет сілтемесі /static/... немесе https://... болуы керек.")
    if d["category"] not in CATEGORIES:
        errors.append("Санатты тізімнен таңдаңыз.")
    if not d["features"]:
        errors.append("Кемінде бір ерекшелік енгізіңіз (әр жолға біреуден).")
    if not d["audience"]:
        errors.append("Мақсатты аудиторияны көрсетіңіз.")
    if not d["price_model"]:
        errors.append("Баға немесе ұсыну моделін көрсетіңіз.")
    return d, errors


PRODUCT_COLS = ("slug", "name", "summary", "description", "price", "image_url",
                "category", "features", "audience", "price_model", "is_active")


@app.route("/admin")
@admin_required
def admin_home():
    return redirect(url_for("admin_books"))


@app.route("/admin/products", methods=["GET", "POST"])
@admin_required
def admin_products():
    form = {"is_active": True, "category": CATEGORIES[0]}
    if request.method == "POST":
        form, errors = product_form_data(request.form)
        if not errors:
            row = primary(f"""INSERT INTO products ({", ".join(PRODUCT_COLS)})
                              VALUES ({", ".join(["%s"] * len(PRODUCT_COLS))})
                              ON CONFLICT (slug) DO NOTHING RETURNING id""",
                          tuple(form[c] for c in PRODUCT_COLS), fetch="one")
            if row is None:
                errors.append("Мұндай slug бұрын бар.")
            else:
                ok = seen_on_standby("SELECT 1 FROM products WHERE id = %s", (row["id"],))
                flash(f"Өнім primary серверде сақталды (id = {row['id']}). " +
                      ("Standby-де де көрінді." if ok else
                       "Standby-ге әлі жетпеді — ашық бетте сәл кейін көрінеді."), "ok")
                return redirect(url_for("admin_products"))
        for e in errors:
            flash(e, "error")
    # Әкімшінің жұмыс тізімі primary-ден оқылады: өзгеріс бірден көрінеді
    rows = primary("""SELECT p.id, p.slug, p.name, p.price, p.category, p.is_active,
                             (SELECT count(*) FROM survey_responses s WHERE s.product_id = p.id) AS answers
                      FROM products p ORDER BY p.id""")
    status = 400 if request.method == "POST" else 200
    return render_template("admin/products.html", products=rows, form=form,
                           categories=CATEGORIES, rep=replication_status()), status


@app.route("/admin/products/<int:pid>/edit", methods=["GET", "POST"])
@admin_required
def admin_product_edit(pid):
    if request.method == "POST":
        form, errors = product_form_data(request.form)
        if not errors:
            try:
                row = primary(f"""UPDATE products SET {", ".join(c + " = %s" for c in PRODUCT_COLS)}
                                  WHERE id = %s RETURNING id""",
                              tuple(form[c] for c in PRODUCT_COLS) + (pid,), fetch="one")
            except psycopg2.IntegrityError:
                row, errors = None, ["Мұндай slug бұрын бар."]
            if row is None and not errors:
                abort(404)
            if not errors:
                ok = seen_on_standby("SELECT 1 FROM products WHERE id = %s AND name = %s", (pid, form["name"]))
                flash(f"Өнім (id = {pid}) primary серверде жаңартылды. " +
                      ("Standby-де де көрінді." if ok else "Standby-ге әлі жетпеді."), "ok")
                return redirect(url_for("admin_products"))
        for e in errors:
            flash(e, "error")
        form["id"] = pid
        return render_template("admin/product_form.html", form=form, categories=CATEGORIES), 400
    form = primary("SELECT * FROM products WHERE id = %s", (pid,), fetch="one")
    if form is None:
        abort(404)
    return render_template("admin/product_form.html", form=form, categories=CATEGORIES)


@app.post("/admin/products/<int:pid>/toggle")
@admin_required
def admin_product_toggle(pid):
    row = primary("UPDATE products SET is_active = NOT is_active WHERE id = %s RETURNING is_active, name",
                  (pid,), fetch="one")
    if row is None:
        abort(404)
    flash(f"«{row['name']}» " + ("жарияланды." if row["is_active"] else "жасырылды (каталогта көрінбейді)."), "ok")
    return redirect(url_for("admin_products"))


# =====================================================================
# 8. Әкімші панелі: кеңселер
# =====================================================================

def office_form_data(form):
    d = {k: form.get(k, "").strip() for k in ("name", "address", "latitude", "longitude", "phone", "email")}
    errors = []
    if not 2 <= len(d["name"]) <= 120:
        errors.append("Кеңсе атауы 2–120 таңба болуы керек.")
    if not 5 <= len(d["address"]) <= 240:
        errors.append("Мекенжай 5–240 таңба болуы керек.")
    for key, lim, label in (("latitude", 90, "Ендік"), ("longitude", 180, "Бойлық")):
        try:
            d[key] = round(float(d[key].replace(",", ".")), 6)
            if not -lim <= d[key] <= lim:
                raise ValueError
        except ValueError:
            errors.append(f"{label} −{lim}…{lim} аралығындағы сан болуы керек.")
    if d["email"] and not EMAIL_RE.match(d["email"]):
        errors.append("Кеңсе email-і дұрыс емес.")
    d["phone"] = d["phone"] or None
    d["email"] = d["email"] or None
    return d, errors


OFFICE_COLS = ("name", "address", "latitude", "longitude", "phone", "email")


@app.route("/admin/offices", methods=["GET", "POST"])
@admin_required
def admin_offices():
    form = {}
    if request.method == "POST":
        form, errors = office_form_data(request.form)
        if not errors:
            row = primary(f"""INSERT INTO offices ({", ".join(OFFICE_COLS)})
                              VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                          tuple(form[c] for c in OFFICE_COLS), fetch="one")
            flash(f"Кеңсе primary серверде сақталды (id = {row['id']}).", "ok")
            return redirect(url_for("admin_offices"))
        for e in errors:
            flash(e, "error")
    offices = primary("SELECT * FROM offices ORDER BY id")
    return render_template("admin/offices.html", offices=offices, form=form,
                           rep=replication_status()), (400 if request.method == "POST" else 200)


@app.route("/admin/offices/<int:oid>/edit", methods=["GET", "POST"])
@admin_required
def admin_office_edit(oid):
    if request.method == "POST":
        form, errors = office_form_data(request.form)
        if not errors:
            row = primary(f"""UPDATE offices SET {", ".join(c + " = %s" for c in OFFICE_COLS)}
                              WHERE id = %s RETURNING id""",
                          tuple(form[c] for c in OFFICE_COLS) + (oid,), fetch="one")
            if row is None:
                abort(404)
            flash(f"Кеңсе (id = {oid}) primary серверде жаңартылды.", "ok")
            return redirect(url_for("admin_offices"))
        for e in errors:
            flash(e, "error")
        form["id"] = oid
        return render_template("admin/office_form.html", form=form), 400
    form = primary("SELECT * FROM offices WHERE id = %s", (oid,), fetch="one")
    if form is None:
        abort(404)
    return render_template("admin/office_form.html", form=form)


@app.post("/admin/offices/<int:oid>/delete")
@admin_required
def admin_office_delete(oid):
    primary("DELETE FROM offices WHERE id = %s", (oid,), fetch=None)
    flash(f"Кеңсе (id = {oid}) жойылды.", "ok")
    return redirect(url_for("admin_offices"))


# =====================================================================
# 9. Әкімші панелі: пайдаланушылар және сауалнамалар (standby-ден оқу)
# =====================================================================

@app.route("/admin/users")
@admin_required
def admin_users():
    # password_hash бағаны таңдалмайды
    users = standby("""SELECT u.id, u.email, u.full_name, u.role, u.created_at,
                              (SELECT count(*) FROM survey_responses s WHERE s.user_id = u.id) AS answers,
                              (SELECT p.name FROM subscriptions sb JOIN products p ON p.id = sb.product_id
                               WHERE sb.user_id = u.id AND sb.status = 'active' AND sb.expires_at > now()
                               ORDER BY sb.expires_at DESC LIMIT 1) AS subscription
                       FROM users u ORDER BY u.id""")
    return render_template("admin/users.html", users=users, rep=replication_status())


def survey_filter():
    pid = request.args.get("product", "")
    rating = request.args.get("rating", "")
    sql = """SELECT s.id, s.rating, s.intent, s.comment, s.created_at,
                    p.name AS product, u.email, u.full_name
             FROM survey_responses s
             JOIN products p ON p.id = s.product_id
             LEFT JOIN users u ON u.id = s.user_id
             WHERE 1=1"""
    params = []
    if pid.isdigit():
        sql += " AND s.product_id = %s"
        params.append(int(pid))
    if rating in {"1", "2", "3", "4", "5"}:
        sql += " AND s.rating = %s"
        params.append(int(rating))
    sql += " ORDER BY s.id DESC"
    return sql, params, pid, rating


@app.route("/admin/surveys")
@admin_required
def admin_surveys():
    sql, params, pid, rating = survey_filter()
    rows = standby(sql, params)
    products_list = standby("SELECT id, name FROM products ORDER BY id")
    avg = round(sum(r["rating"] for r in rows) / len(rows), 2) if rows else None
    return render_template("admin/surveys.html", rows=rows, products=products_list,
                           pid=pid, rating=rating, avg=avg, rep=replication_status())


@app.route("/admin/surveys.csv")
@admin_required
def admin_surveys_csv():
    """Бонус: сүзгіленген жауаптарды CSV форматында экспорттау."""
    sql, params, _, _ = survey_filter()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "created_at", "product", "rating", "intent", "user_email", "comment"])
    for r in standby(sql, params):
        w.writerow([r["id"], r["created_at"], r["product"], r["rating"],
                    INTENTS.get(r["intent"], r["intent"]), r["email"] or "", r["comment"] or ""])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=survey_responses.csv"})


@app.post("/admin/surveys/<int:sid>/delete")
@admin_required
def admin_survey_delete(sid):
    primary("DELETE FROM survey_responses WHERE id = %s", (sid,), fetch=None)
    flash(f"Жауап № {sid} primary серверден жойылды.", "ok")
    return redirect(url_for("admin_surveys", **{k: v for k, v in request.args.items()}))


# =====================================================================
# 10. Кітаптар каталогы, оқу/тыңдау және жазылым
# =====================================================================

def active_subscription(user_id):
    """Жазылымды тексеру — қолжетімділік құқығы, сондықтан primary-ден оқылады."""
    return primary("""SELECT s.id, s.expires_at, p.name, p.slug
                      FROM subscriptions s JOIN products p ON p.id = s.product_id
                      WHERE s.user_id = %s AND s.status = 'active' AND s.expires_at > now()
                      ORDER BY s.expires_at DESC LIMIT 1""", (user_id,), fetch="one")


@app.route("/books")
def books():
    q = request.args.get("q", "").strip()
    genre = request.args.get("genre", "")
    fmt = request.args.get("format", "")
    access = request.args.get("access", "")
    sql = """SELECT id, title, author, genre, book_format, description, is_free
             FROM books WHERE is_active"""
    params = []
    if q:
        sql += " AND (title ILIKE %s OR author ILIKE %s)"
        params += [f"%{q}%", f"%{q}%"]
    if genre in GENRES:
        sql += " AND genre = %s"
        params.append(genre)
    if fmt in ("ebook", "audio"):
        sql += " AND (book_format = %s OR book_format = 'both')"
        params.append(fmt)
    if access == "free":
        sql += " AND is_free"
    sql += " ORDER BY title"
    return render_template("books.html", books=standby(sql, params), q=q, genre=genre,
                           fmt=fmt, access=access, genres=GENRES)


@app.route("/books/<int:bid>")
def book_page(bid):
    book = standby("SELECT * FROM books WHERE id = %s AND is_active", (bid,), fetch="one")
    if book is None:
        abort(404)
    # Қарапайым ұсыныс: алдымен сол автор, кейін сол жанр
    similar = standby("""SELECT id, title, author, genre, book_format, is_free FROM books
                         WHERE is_active AND id <> %s AND (author = %s OR genre = %s)
                         ORDER BY (author = %s) DESC, id DESC LIMIT 4""",
                      (bid, book["author"], book["genre"], book["author"]))
    sub = active_subscription(g.user["id"]) if g.user else None
    can_read = bool(g.user) and (book["is_free"] or sub is not None)
    return render_template("book.html", book=book, similar=similar, can_read=can_read, sub=sub)


@app.route("/books/<int:bid>/read")
@login_required
def read_book(bid):
    book = standby("SELECT * FROM books WHERE id = %s AND is_active", (bid,), fetch="one")
    if book is None:
        abort(404)
    mode = request.args.get("mode", "read")
    if mode not in ("read", "listen") or (mode == "listen" and book["book_format"] == "ebook") \
            or (mode == "read" and book["book_format"] == "audio"):
        mode = "listen" if book["book_format"] == "audio" else "read"
    sub = None if book["is_free"] else active_subscription(g.user["id"])
    if not book["is_free"] and sub is None:
        flash("Бұл кітапты оқу және тыңдау үшін жазылым қажет.", "error")
        return redirect(url_for("subscribe_offer", book=bid))
    return render_template("read.html", book=book, mode=mode, sub=sub)


@app.route("/subscribe")
def subscribe_offer():
    plans = standby("""SELECT id, slug, name, summary, price, price_model, features, image_url
                       FROM products WHERE is_active AND slug IN (%s, %s) ORDER BY price""",
                    SUBSCRIPTION_SLUGS)
    for p in plans:
        p["feature_list"] = [f.strip() for f in p["features"].splitlines() if f.strip()]
    book = None
    if request.args.get("book", "").isdigit():
        book = standby("SELECT id, title, author FROM books WHERE id = %s", (int(request.args["book"]),), fetch="one")
    sub = active_subscription(g.user["id"]) if g.user else None
    return render_template("subscribe.html", plans=plans, book=book, sub=sub, days=SUBSCRIPTION_DAYS)


@app.post("/subscribe/<slug>")
@login_required
def subscribe(slug):
    if slug not in SUBSCRIPTION_SLUGS:
        abort(404)
    book_id = request.form.get("book", "")
    target = url_for("read_book", bid=int(book_id)) if book_id.isdigit() else url_for("books")
    if active_subscription(g.user["id"]):
        flash("Сізде белсенді жазылым бар.", "ok")
        return redirect(target)
    # Демо режим: төлем қабылданбайды, жазылым бірден primary-де жасалады
    row = primary(f"""INSERT INTO subscriptions (user_id, product_id, expires_at)
                      SELECT %s, id, now() + interval '{SUBSCRIPTION_DAYS} days'
                      FROM products WHERE slug = %s AND is_active
                      RETURNING id, expires_at""", (g.user["id"], slug), fetch="one")
    if row is None:
        abort(404)
    flash(f"Жазылым іске қосылды ({row['expires_at'].strftime('%d.%m.%Y')} дейін). Оқуға сәттілік!", "ok")
    return redirect(target)


@app.post("/subscription/cancel")
@login_required
def cancel_subscription():
    primary("""UPDATE subscriptions SET status = 'cancelled'
               WHERE user_id = %s AND status = 'active'""", (g.user["id"],), fetch=None)
    flash("Жазылым тоқтатылды.", "ok")
    return redirect(url_for("profile"))


# ---------- Әкімші: кітаптар ----------

BOOK_COLS = ("title", "author", "genre", "book_format", "description", "is_free", "is_active")


def book_form_data(form):
    d = {k: form.get(k, "").strip() for k in ("title", "author", "genre", "book_format", "description")}
    d["description"] = d["description"].replace("\r\n", "\n")
    d["is_free"] = form.get("is_free") == "on"
    d["is_active"] = form.get("is_active") == "on"
    errors = []
    if not 1 <= len(d["title"]) <= 150:
        errors.append("Кітап атауы 1–150 таңба болуы керек.")
    if not 2 <= len(d["author"]) <= 120:
        errors.append("Автор 2–120 таңба болуы керек.")
    if d["genre"] not in GENRES:
        errors.append("Жанрды тізімнен таңдаңыз.")
    if d["book_format"] not in FORMATS:
        errors.append("Форматты тізімнен таңдаңыз.")
    if not 10 <= len(d["description"]) <= 1000:
        errors.append("Сипаттама 10–1000 таңба болуы керек.")
    return d, errors


@app.route("/admin/books", methods=["GET", "POST"])
@admin_required
def admin_books():
    form = {"is_active": True, "genre": GENRES[0], "book_format": "both"}
    if request.method == "POST":
        form, errors = book_form_data(request.form)
        if not errors:
            row = primary(f"""INSERT INTO books ({", ".join(BOOK_COLS)})
                              VALUES ({", ".join(["%s"] * len(BOOK_COLS))}) RETURNING id""",
                          tuple(form[c] for c in BOOK_COLS), fetch="one")
            ok = seen_on_standby("SELECT 1 FROM books WHERE id = %s", (row["id"],))
            flash(f"Кітап primary серверде сақталды (id = {row['id']}). " +
                  ("Standby-де де көрінді." if ok else "Standby-ге әлі жетпеді."), "ok")
            return redirect(url_for("admin_books"))
        for e in errors:
            flash(e, "error")
    rows = primary("""SELECT id, title, author, genre, book_format, is_free, is_active
                      FROM books ORDER BY id""")
    return render_template("admin/books.html", books=rows, form=form, genres=GENRES,
                           rep=replication_status()), (400 if request.method == "POST" else 200)


@app.route("/admin/books/<int:bid>/edit", methods=["GET", "POST"])
@admin_required
def admin_book_edit(bid):
    if request.method == "POST":
        form, errors = book_form_data(request.form)
        if not errors:
            row = primary(f"""UPDATE books SET {", ".join(c + " = %s" for c in BOOK_COLS)}
                              WHERE id = %s RETURNING id""",
                          tuple(form[c] for c in BOOK_COLS) + (bid,), fetch="one")
            if row is None:
                abort(404)
            flash(f"Кітап (id = {bid}) primary серверде жаңартылды.", "ok")
            return redirect(url_for("admin_books"))
        for e in errors:
            flash(e, "error")
        form["id"] = bid
        return render_template("admin/book_form.html", form=form, genres=GENRES), 400
    form = primary("SELECT * FROM books WHERE id = %s", (bid,), fetch="one")
    if form is None:
        abort(404)
    return render_template("admin/book_form.html", form=form, genres=GENRES)


@app.post("/admin/books/<int:bid>/toggle")
@admin_required
def admin_book_toggle(bid):
    row = primary("UPDATE books SET is_active = NOT is_active WHERE id = %s RETURNING is_active, title",
                  (bid,), fetch="one")
    if row is None:
        abort(404)
    flash(f"«{row['title']}» " + ("жарияланды." if row["is_active"] else "жасырылды."), "ok")
    return redirect(url_for("admin_books"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
