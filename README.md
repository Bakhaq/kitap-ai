# Kitap.AI — пайдаланушы бөлігі, әкімші панелі және PostgreSQL primary/standby

№10–11 зертханалық жұмыстар. Kitap.AI — қазақ тіліндегі электрондық кітаптар мен аудиокітаптар платформасы.
Сайтта ашық пайдаланушы бөлігі (каталог, өнім беті, сауалнама, байланыстар мен карта) және тек `admin`
рөліне арналған әкімші панелі бар. Деректер PostgreSQL 17-де primary → standby ағындық репликациясымен сақталады.

```
Браузер → web (Flask) → postgres_primary ⇒ postgres_standby
           тіркелу, кіру, CRUD → primary      каталог, есептер → standby      WAL ⇒ standby
```

## Іске қосу

```powershell
copy .env.example .env          # мәндерді өзгертіңіз (GITHUB_URL — өз репозиторийіңіз)
docker compose config
docker compose up -d --build
docker compose ps
```

Сайт: http://localhost:8083  ·  Күй: http://localhost:8083/health

Әкімші `.env` файлындағы `ADMIN_EMAIL` / `ADMIN_PASSWORD` бойынша алғашқы сұрауда жасалады.
Қосымша әкімшіні қауіпсіз команда арқылы жасауға болады (тіркелу формасы `admin` рөлін бермейді):

```powershell
docker compose exec web flask --app app create-admin
```

№10 жұмыстың томдары сақталған болса, оларды өшірудің қажеті жоқ: web қосымша бірінші сұрауда
`app/schema.sql` миграциясын орындайды (ескі `users` кестесі `users_lab10` болып сақталады).
Таза бастау үшін: `docker compose down -v`.

## Беттер мен маршруттар

| Маршрут | Әдіс | Міндеті | Сервер |
|---|---|---|---|
| `/` | GET | Басты бет: мақсат, артықшылықтар, өнімдер | standby |
| `/register`, `/login`, `/logout` | GET POST | Тіркелу (role әрқашан `user`), кіру, шығу | primary |
| `/profile` | GET POST | Өз профилі және өз жауаптары | primary |
| `/books` | GET | Кітаптар каталогы: іздеу, жанр, формат, «тек тегін» | standby |
| `/books/<id>` | GET | Кітап беті және ұқсас кітаптар | standby |
| `/books/<id>/read` | GET | Оқу / тыңдау: тегін кітап — кірген кез келген пайдаланушыға, қалғаны — жазылыммен, әйтпесе жазылым ұсынылады | жазылымды тексеру — primary |
| `/subscribe` | GET POST | Жазылым ұсынысы (Premium, Отбасылық), демо режимде 30 күнге іске қосу | жазу — primary |
| `/products` | GET | Тарифтер мен өнімдер: іздеу және санат сүзгісі | standby |
| `/products/<slug>` | GET | Өнімнің толық беті | standby |
| `/survey` | GET POST | Сауалнама (тек тіркелген пайдаланушы) | жазу — primary |
| `/contacts` | GET | Кеңселер, OpenStreetMap картасы, GitHub сілтемесі | standby |
| `/admin/books` | GET POST | Кітаптар: қосу, өңдеу, тегін/жазылым, жариялау/жасыру | primary |
| `/admin/products` | GET POST | Өнімдер: жасау, өңдеу, жариялау/жасыру | primary |
| `/admin/offices` | GET POST | Кеңселер CRUD | primary |
| `/admin/users` | GET | Пайдаланушылар (password_hash жоқ) | standby |
| `/admin/surveys` | GET | Жауаптар: өнім/баға сүзгісі, жою (растаумен), CSV | standby |
| `/health` | GET | JSON: primary/standby күйі, репликация кідірісі | екеуі |

## Қауіпсіздік
* Пароль тек `werkzeug.security` hash түрінде сақталады; барлық SQL сұраулары параметрленген.
* Әр POST формасында CSRF белгісі; сессиялық cookie — `HttpOnly`, `SameSite=Lax`; сессияда тек `user_id`.
* Рөл әр сұрауда серверде primary-ден оқылып тексеріледі (`admin_required`), `user` рөлі `/admin` бетінде 403 алады.
* Құпиялар `.env` файлында, ол `.gitignore` арқылы репозиторийге түспейді.

## Тексеру командалары

```powershell
docker compose exec postgres_primary psql -U appuser -d projectdb -c "SELECT client_addr,state,sync_state FROM pg_stat_replication;"
docker compose exec postgres_standby psql -U appuser -d projectdb -c "SELECT pg_is_in_recovery();"
docker compose exec postgres_primary psql -U appuser -d projectdb -c "SELECT email, left(password_hash, 20), role FROM users;"
docker compose exec postgres_standby psql -U appuser -d projectdb -c "INSERT INTO offices(name,address,latitude,longitude) VALUES ('x','x',0,0);"   # read-only қатесі
# Standby ақауы
docker compose stop postgres_standby
#   ... әкімші панелінде өнім қосу ...
docker compose start postgres_standby
docker compose exec postgres_standby psql -U appuser -d projectdb -c "SELECT id, slug FROM products ORDER BY id DESC LIMIT 3;"
# Қайта іске қосу: деректер томдарда сақталады
docker compose restart
```

## Файл құрылымы

```
├── compose.yaml              3 сервис, бөлек томдар (primary_data, standby_data)
├── .env.example              құпиялар үлгісі (.env репозиторийге жүктелмейді)
├── primary-init/
│   ├── 01-replication.sh     replicator рөлі және pg_hba
│   ├── 02-schema.sql         books (№10 жұмыс)
│   └── 03-users.sql          users, products, survey_responses, offices + бастапқы деректер
├── standby/setup-standby.sh  pg_basebackup + hot standby
└── app/
    ├── app.py                Flask қосымшасы
    ├── schema.sql            03-users.sql көшірмесі (бар томдарға миграция)
    ├── templates/            ашық беттер және admin/ панелі
    └── static/               суреттер (hero.svg, img/*.svg)
```

## GitHub-қа жариялау

```powershell
git init
git add .
git commit -m "№10: Docker Compose, PostgreSQL primary/standby, Flask CRUD"
git branch -M main
git remote add origin https://github.com/<github-логин>/kitap-ai-lab11.git
git push -u origin main
```

Кейін `.env` ішіндегі `GITHUB_URL` мәнін өз репозиторийіңізге ауыстырып, `docker compose up -d` орындаңыз —
сілтеме Байланыстар бетінде және әр беттің төменгі жағында шығады.
