-- Kitap.AI: қазақ тіліндегі кітаптар каталогы
CREATE TABLE books (
    id          BIGSERIAL PRIMARY KEY,
    title       VARCHAR(150) NOT NULL CHECK (length(trim(title)) > 0),
    author      VARCHAR(120) NOT NULL CHECK (length(trim(author)) > 0),
    genre       VARCHAR(40)  NOT NULL,
    book_format VARCHAR(20)  NOT NULL CHECK (book_format IN ('ebook', 'audio', 'both')),
    description TEXT         NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Бастапқы деректер (standby көшірмеге pg_basebackup арқылы бірге өтеді)
INSERT INTO books (title, author, genre, book_format, description) VALUES
('Абай жолы',          'Мұхтар Әуезов',      'Роман',      'both',  'Абай Құнанбайұлының өмірі туралы эпопея'),
('Көшпенділер',        'Ілияс Есенберлин',   'Тарихи',     'ebook', 'Қазақ хандығы туралы трилогия'),
('Қан мен тер',        'Әбдіжәміл Нұрпейісов','Роман',     'audio', 'Арал өңірі балықшыларының тағдыры'),
('Мен қазақпын',       'Жұбан Молдағалиев',  'Поэзия',     'ebook', 'Атақты поэма'),
('Ақбілек',            'Жүсіпбек Аймауытов', 'Роман',      'both',  'Қазақ әйелінің тағдыры туралы роман');
