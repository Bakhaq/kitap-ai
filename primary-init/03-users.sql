-- №11 зертханалық жұмыс: №10 жұмыстың сұлбасын (books) кеңейту.
-- Міндетті 4 кесте: users, products, survey_responses, offices.
--
-- Файл қайта орындалса да қауіпсіз (IF NOT EXISTS, ON CONFLICT):
--   * жаңа том болса — primary алғаш іске қосылғанда docker-entrypoint орындайды;
--   * №10 жұмыстың томдары бұрыннан бар болса — web қосымша бірінші сұрауда
--     дәл осы мәтінді (app/schema.sql) primary серверде орындайды.

-- №10 жұмыстағы ескі users кестесі (телефон + псевдоним) болса, оны users_lab10
-- деп сақтап қоямыз, ал жаңа users кестесі email негізінде құрылады.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'phone')
       AND NOT EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_schema = 'public' AND table_name = 'users_lab10') THEN
        ALTER TABLE users RENAME TO users_lab10;
        ALTER INDEX IF EXISTS users_pkey RENAME TO users_lab10_pkey;
        ALTER INDEX IF EXISTS users_nickname_key RENAME TO users_lab10_nickname_key;
        ALTER INDEX IF EXISTS users_phone_key RENAME TO users_lab10_phone_key;
        ALTER SEQUENCE IF EXISTS users_id_seq RENAME TO users_lab10_id_seq;
    END IF;
END $$;


CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         VARCHAR(160) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,                       -- тек hash, ашық пароль сақталмайды
    full_name     VARCHAR(120) NOT NULL,
    role          VARCHAR(16) NOT NULL DEFAULT 'user'
                  CHECK (role IN ('user', 'admin')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS products (
    id          BIGSERIAL PRIMARY KEY,
    slug        VARCHAR(140) UNIQUE NOT NULL CHECK (slug ~ '^[a-z0-9-]+$'),
    name        VARCHAR(140) NOT NULL,
    summary     VARCHAR(300) NOT NULL,
    description TEXT NOT NULL,
    price       NUMERIC(12,2) CHECK (price >= 0),
    image_url   TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Kitap.AI қосымша өрістері (міндетті өрістер сақталған)
    category    VARCHAR(40)  NOT NULL DEFAULT 'Оқырмандарға',
    features    TEXT         NOT NULL DEFAULT '',      -- әр жол — бір ерекшелік
    audience    VARCHAR(300) NOT NULL DEFAULT '',      -- мақсатты аудитория
    price_model VARCHAR(160) NOT NULL DEFAULT ''       -- ұсыну моделі, мысалы «айына»
);

CREATE TABLE IF NOT EXISTS survey_responses (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    rating     SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    intent     VARCHAR(40) NOT NULL,
    comment    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS offices (
    id        BIGSERIAL PRIMARY KEY,
    name      VARCHAR(120) NOT NULL,
    address   VARCHAR(240) NOT NULL,
    latitude  NUMERIC(9,6) NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude NUMERIC(9,6) NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    phone     VARCHAR(40),
    email     VARCHAR(160)
);

-- Жиі сүзгілерге арналған индекстер
CREATE INDEX IF NOT EXISTS idx_products_active   ON products (is_active, category);
CREATE INDEX IF NOT EXISTS idx_survey_product    ON survey_responses (product_id, rating);
CREATE INDEX IF NOT EXISTS idx_survey_user       ON survey_responses (user_id);

-- Kitap.AI өнімдері мен кеңселерінің бастапқы деректері.
-- Әкімші аккаунты осында жасалмайды: оны web қосымша .env арқылы немесе
-- `flask --app app create-admin` командасымен жасайды (пароль hash-ын Python есептейді).

INSERT INTO products (slug, name, summary, description, price, image_url, category, features, audience, price_model) VALUES
('kitap-ai-oqyrman',
 'Kitap.AI Оқырман',
 'Қазақ тіліндегі кітаптарды бір каталогтан тауып, оқуды тегін бастауға арналған базалық нұсқа.',
 'Kitap.AI Оқырман — платформамен танысудың ең қарапайым жолы. Пайдаланушы қазақ тіліндегі электрондық кітаптар мен аудиокітаптардың ортақ каталогын ашып, атауы, авторы және жанры бойынша іздейді. Әр кітаптың форматы бірден көрінеді, сондықтан оқитынын немесе тыңдайтынын алдын ала таңдай алады.

Базалық нұсқада сол автор және сол жанр ережесіне негізделген ұсыныстар жұмыс істейді. Бұл нұсқа студенттерге, мектеп оқушыларына және қазақша оқуды енді бастаған оқырмандарға арналған; кейін олар қосымша мүмкіндіктер үшін Premium жазылымына ауыса алады.',
 0, '/static/img/oqyrman.svg', 'Оқырмандарға',
 'Ортақ каталог және іздеу
Жанр мен формат бойынша сүзгі
Сол автордың және сол жанрдың ұсыныстары
Таңдаулы кітаптар тізімі',
 'Қазақ тілді оқырмандар, студенттер, мектеп оқушылары',
 'Тегін (freemium)'),

('kitap-ai-premium',
 'Kitap.AI Premium жазылымы',
 'Талғамыңызды үйренетін AI ұсыныстары, офлайн тыңдау және оқу статистикасы бар жеке жазылым.',
 'Premium жазылымы оқырманның оқыған және тыңдаған кітаптарын, қойған бағаларын ескеріп, жеке ұсыныстар жасайды. Әр ұсыныстың жанында себебі көрсетіледі: мысалы, «Сізге ұнаған тарихи романға ұқсас» немесе «Сіз жиі тыңдайтын автордың жаңа кітабы». Осылайша пайдаланушы алгоритмнің шешімін түсініп, оған сенеді.

Жазылымда аудиокітаптарды смартфонға жүктеп, интернетсіз тыңдауға, тыңдау жылдамдығын өзгертуге және апталық оқу статистикасын көруге болады. Жазылым жол жүріп немесе жұмыс арасында кітап тыңдайтын белсенді оқырмандарға арналған.',
 1990, '/static/img/premium.svg', 'Оқырмандарға',
 'Түсіндірілетін AI ұсыныстары
Аудиокітаптарды офлайн тыңдау
Тыңдау жылдамдығы және ұйқы таймері
Апталық оқу статистикасы мен мақсаттар',
 'Белсенді оқырмандар, аудиокітап тыңдаушылар, 18–35 жас',
 'Айына 1 990 ₸, 7 күн тегін сынақ'),

('kitap-ai-otbasy',
 'Kitap.AI Отбасылық жазылымы',
 'Бір жазылымда 4 профиль: ата-аналар мен балаларға бөлек ұсыныстар және балалар бөлімі.',
 'Отбасылық жазылым бір шотқа төрт жеке профильді біріктіреді. Әр профильдің өз тарихы мен ұсыныстары болады, сондықтан ата-ананың тарихи романдары баланың ертегілеріне араласпайды. Балалар профилінде тек жасына сай кітаптар мен аудиоертегілер көрсетіледі.

Ата-ана баланың оқу уақытын бақылап, апталық мақсат қоя алады. Бұл тариф балаларын қазақша кітап оқуға баулығысы келетін отбасыларға арналған және жеке Premium жазылымдарына қарағанда үнемді.',
 3490, '/static/img/otbasy.svg', 'Оқырмандарға',
 '4 жеке профиль
Жасқа сай балалар бөлімі
Ата-ана бақылауы және оқу мақсаттары
Premium мүмкіндіктерінің барлығы',
 'Балалы отбасылар, ата-аналар',
 'Айына 3 490 ₸'),

('kitap-ai-api',
 'Баспаларға арналған ұсыныс API',
 'Баспа немесе кітап дүкені өз сайтына Kitap.AI ұсыныс модулін API арқылы қосады.',
 'Ұсыныс API қазақ тіліндегі кітаптардың мағыналық ұқсастығын бағалайтын Kitap.AI модулін сыртқы сайттар мен қосымшаларға береді. Баспа өз каталогын жүктейді, ал API әр кітап беті үшін «осыған ұқсас кітаптар» тізімін және себебін қайтарады.

Модуль сатылымды арттыруға және оқырманның сайтта ұзағырақ қалуына көмектеседі. Қосылу кезінде біз каталогты бірге дайындаймыз және ұсыныс сапасын тестілеу есебін береміз.',
 150000, '/static/img/api.svg', 'Бизнеске',
 'REST API және құжаттама
Каталогты жүктеу және жаңарту
Ұсыныстың себебін қайтару
Ай сайынғы сапа есебі (CTR, nDCG@10)',
 'Баспалар, кітап дүкендері, цифрлық кітап сервистері',
 'Айына 150 000 ₸ бастап (B2B-лицензия)'),

('kitap-ai-mektep',
 'Мектеп пен кітапханаға арналған лицензия',
 'Оқу марафондарын өткізуге және оқушылардың оқу белсенділігін бақылауға арналған жылдық лицензия.',
 'Лицензия мектепке немесе кітапханаға оқушыларға ортақ кітап қорын ашуға және оқу марафондарын ұйымдастыруға мүмкіндік береді. Мұғалім сыныпқа кітап тізімін тағайындайды, ал жүйе әр оқушының қай кітапты бастағанын және аяқтағанын көрсетеді.

Kitap.AI оқушының деңгейі мен қызығушылығына қарай қосымша кітаптар ұсынады. Есептер мұғалімге қай кітаптар қызықты болғанын түсінуге көмектеседі; оқушылардың дербес деректері тек оқу мекемесінің ішінде қолданылады.',
 300000, '/static/img/mektep.svg', 'Білім беруге',
 'Сыныптар мен оқушылар тізімі
Оқу марафондары және рейтинг
Мұғалімге арналған есептер
300 оқушыға дейін бір лицензия',
 'Мектептер, колледждер, қалалық және ауылдық кітапханалар',
 'Жылына 300 000 ₸ (300 оқушыға дейін)')
ON CONFLICT (slug) DO NOTHING;

-- Кеңселер: координаттар ашық қоғамдық орындарға шамамен сәйкес келеді
-- (қатысушылардың үй мекенжайлары қолданылмайды). Телефон мен email — үлгі мәндер.
INSERT INTO offices (name, address, latitude, longitude, phone, email)
SELECT * FROM (VALUES
('Kitap.AI — Астана кеңсесі',  'Астана қ., Сәтпаев к-сі, 2 (Л.Н. Гумилев атындағы ЕҰУ маңы, шамамен)', 51.159800, 71.471400, '+7 (700) 000-00-01', 'astana@kitap-ai.example'),
('Kitap.AI — Алматы кеңсесі',  'Алматы қ., Абай даңғылы маңы (шамамен)',                               43.240400, 76.945600, '+7 (700) 000-00-02', 'almaty@kitap-ai.example')
) AS v(name, address, latitude, longitude, phone, email)
WHERE NOT EXISTS (SELECT 1 FROM offices);

-- =====================================================================
-- Кітаптар каталогы (№10 жұмыстағы books кестесін кеңейту) және жазылымдар
-- =====================================================================

-- is_free: тіркелген кез келген пайдаланушы оқи алады (қоғамдық игілік, халық ауыз әдебиеті)
-- is_active: әкімші кітапты каталогтан жасыра алады
ALTER TABLE books ADD COLUMN IF NOT EXISTS is_free   BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE books ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;

-- Пайдаланушының жазылымы: Premium немесе Отбасылық өнімі (products)
CREATE TABLE IF NOT EXISTS subscriptions (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    status     VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions (user_id, status, expires_at);

-- Қазақ әдебиетінің кітаптары (атауы бойынша қайталанбайды)
INSERT INTO books (title, author, genre, book_format, description, is_free)
SELECT v.title, v.author, v.genre, v.book_format, v.description, v.is_free
FROM (VALUES
 ('Абай жолы',                 'Мұхтар Әуезов',        'Роман',   'both',  'Абай Құнанбайұлының өмірі мен дәуірі туралы төрт томдық роман-эпопея.', false),
 ('Көшпенділер',               'Ілияс Есенберлин',     'Тарихи',  'both',  'Қазақ хандығының құрылуы мен азаттық күресі туралы тарихи трилогия.', false),
 ('Қан мен тер',               'Әбдіжәміл Нұрпейісов', 'Роман',   'audio', 'Арал өңірі балықшыларының ХХ ғасыр басындағы тағдыры туралы трилогия.', false),
 ('Ақбілек',                   'Жүсіпбек Аймауытов',   'Роман',   'both',  'Қиын тағдырға мойымаған қазақ қызы туралы роман.', false),
 ('Мен қазақпын',              'Жұбан Молдағалиев',    'Поэзия',  'ebook', 'Халықтың тарихы мен рухын жырлаған атақты поэма.', false),
 ('Ботагөз',                   'Сәбит Мұқанов',        'Роман',   'ebook', 'ХХ ғасыр басындағы қазақ ауылы мен жастардың махаббаты туралы роман.', false),
 ('Қилы заман',                'Мұхтар Әуезов',        'Тарихи',  'ebook', '1916 жылғы ұлт-азаттық көтерілісі туралы тарихи повесть.', false),
 ('Менің атым Қожа',           'Бердібек Соқпақбаев',  'Әңгіме',  'both',  'Тентек те адал Қожаның мектептегі қызықты оқиғалары туралы балалар повесі.', false),
 ('Махаббат, қызық мол жылдар', 'Әзілхан Нұршайықов',  'Роман',   'audio', 'Жастық, махаббат және адалдық туралы лирикалық роман.', false),
 ('Қара сөздер',               'Абай Құнанбайұлы',     'Өзін-өзі дамыту', 'both', 'Абайдың адам, қоғам және білім туралы 45 философиялық толғанысы. Қоғамдық игілік — тегін.', true),
 ('Өлеңдер жинағы',            'Мағжан Жұмабаев',      'Поэзия',  'ebook', 'Мағжан ақынның таңдамалы өлеңдері. Қоғамдық игілік — тегін.', true),
 ('Ер Төстік',                 'Қазақ халық ертегісі', 'Ертегі',  'both',  'Батыр Ер Төстіктің жер асты еліндегі шытырман сапары. Халық ауыз әдебиеті — тегін.', true),
 ('Алдар Көсе',                'Қазақ халық ертегісі', 'Ертегі',  'audio', 'Тапқыр Алдар Көсенің сараң байларды алдауы туралы ертегілер. Халық ауыз әдебиеті — тегін.', true)
) AS v(title, author, genre, book_format, description, is_free)
WHERE NOT EXISTS (SELECT 1 FROM books b WHERE b.title = v.title);
