# Kitap.AI — Nginx HTTPS, екі web данасы және PostgreSQL primary/standby

№10–12 зертханалық жұмыстар. Kitap.AI — қазақ тіліндегі электрондық кітаптар мен аудиокітаптар платформасы.
Сайтта кітаптар каталогы, жазылым, сауалнама, байланыстар және тек `admin` рөліне арналған әкімші панелі бар.

```
Браузер → HTTPS Nginx (80→301, 443) → web немесе web_replica (round robin) → PostgreSQL primary ⇒ standby
```

| Сервис | Міндеті | Сыртқы порт |
|---|---|---|
| `nginx` | TLS аяқтау, reverse proxy, `/static`, жүктемені бөлу | 80, 443 |
| `web`, `web_replica` | Flask қосымшасының екі бірдей данасы | жоқ (`expose: 5000`) |
| `postgres_primary` | Барлық жазу, кіру, рөлді тексеру | жоқ |
| `postgres_standby` | Реплика, read only оқу | жоқ |

## Іске қосу

1. **`lab.local` доменін қосу.** PowerShell-ді *Run as administrator* арқылы ашып:
   ```powershell
   Add-Content -Path "$env:windir\System32\drivers\etc\hosts" -Value "127.0.0.1 lab.local"
   ping lab.local
   ```
   Linux/WSL: `echo "127.0.0.1 lab.local" | sudo tee -a /etc/hosts`

2. **Сертификат.** `nginx/certs/` ішінде дайын оқу сертификаты бар. Жаңасын жасау (Git Bash немесе WSL):
   ```bash
   mkdir -p nginx/certs
   MSYS_NO_PATHCONV=1 openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
     -keyout nginx/certs/lab.local.key -out nginx/certs/lab.local.crt \
     -subj "/CN=lab.local" -addext "subjectAltName=DNS:lab.local"
   openssl x509 -in nginx/certs/lab.local.crt -noout -subject -issuer -dates -ext subjectAltName
   ```
   `lab.local.key` — жеке кілт, `.gitignore` арқылы репозиторийге жіберілмейді.

3. **Іске қосу:**
   ```powershell
   copy .env.example .env          # бір рет; құпияларды өзгертіңіз
   docker compose config
   docker compose up -d --build
   docker compose ps               # 5 сервис healthy
   docker compose exec nginx nginx -t
   ```

Сайт: https://lab.local  ·  Web күйі: https://lab.local/health  ·  Nginx күйі: https://lab.local/nginx-health

Self signed сертификат болғандықтан браузер ескерту көрсетеді («Қосымша → lab.local сайтына өту»), `curl` үшін `-k`.
80/443 порттарын басқа бағдарлама (IIS, Skype) алып тұрса, оны тоқтатыңыз.

Әкімші `.env` файлындағы `ADMIN_EMAIL` / `ADMIN_PASSWORD` бойынша жасалады. Қосымша әкімші:
`docker compose exec web flask --app app create-admin`.
№10–11 жұмыстардың томдары сақталады: web қосымша бірінші сұрауда `app/schema.sql` миграциясын орындайды.

## Тексеру (№12)

```bash
curl -I  http://lab.local                     # 301 → https://lab.local/
curl -kI https://lab.local/static/style.css   # 200, Server: nginx, Expires, Cache-Control
curl -k  https://lab.local/nginx-health       # nginx ok
for i in 1 2 3 4 5 6; do curl -sk https://lab.local/health; echo; done   # instance: kitap-web-1 / kitap-web-2
docker compose exec nginx tail -n 20 /var/log/nginx/access.log           # upstream=... екі түрлі IP
curl http://localhost:5000                    # қосылу мүмкін емес (web порты жабық)
```

**Failover сынағы**

```bash
docker compose stop web
docker compose ps
curl -kI https://lab.local                    # сайт web_replica арқылы ашылады (X-Upstream тақырыбы)
curl -k  https://lab.local/health             # instance: kitap-web-2
docker compose logs --tail=20 nginx
docker compose start web
docker compose ps                             # web қайта healthy
```

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
* Құпиялар `.env` файлында, TLS жеке кілті `nginx/certs/*.key` — екеуі де `.gitignore` арқылы репозиторийге түспейді.
* Тек TLS 1.2 және 1.3; HTTP сұраныстары HTTPS-ке қайта бағытталады; cookie — `Secure`.
* Web және PostgreSQL порттары host-қа жарияланбайды; `X-Forwarded-For` / `X-Forwarded-Proto` қосымшаға беріледі (`ProxyFix`).

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
├── compose.yaml              5 сервис: nginx, web, web_replica, postgres_primary, postgres_standby
├── nginx/
│   ├── nginx.conf            upstream, HTTPS, redirect, /static, proxy headers
│   └── certs/                lab.local.crt (+ lab.local.key — тек жергілікті)
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
    └── static/               style.css, hero.svg, img/*.svg (Nginx тікелей береді)
```

## GitHub-қа жариялау

```powershell
git init
git add .
git commit -m "№10: Docker Compose, PostgreSQL primary/standby, Flask CRUD"
git branch -M main
git remote add origin https://github.com/Bakhaq/kitap-ai.git
git push -u origin main
```

Кейін `.env` ішіндегі `GITHUB_URL` мәнін өз репозиторийіңізге ауыстырып, `docker compose up -d` орындаңыз —
сілтеме Байланыстар бетінде және әр беттің төменгі жағында шығады.
