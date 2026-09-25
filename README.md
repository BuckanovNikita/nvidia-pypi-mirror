# Зеркало NVIDIA PyPI

Скрипт скачивает HTML-страницы, доступные по ссылкам из
[pypi.nvidia.com](https://pypi.nvidia.com/), и готовит статический индекс для вашего сервера:

- ссылки на файлы с `pypi.nvidia.com` получают базовый URL из `NVIDIA_REPO`;
- ссылки между страницами индекса получают базовый URL из `INDEX_URL`;
- исходные пути, параметры URL файлов, хеши `#sha256` и атрибуты пакетов сохраняются;
- относительные ссылки разрешаются относительно исходной страницы;
- ссылки на другие хосты остаются без изменений;
- wheel-архивы и исходники пакетов не скачиваются.

Нужен Python 3.12+ на Windows или Linux. Для запуска скрипта сторонние библиотеки
не требуются. HTTPS использует системные сертификаты доверенных центров сертификации.

## Быстрая проверка: три пакета

Windows PowerShell, из каталога со скриптом:

```powershell
$env:NVIDIA_REPO = 'https://artifactory.example.com/artifactory/nvidia-files'
$env:INDEX_URL = 'https://artifactory.example.com/artifactory/nvidia-index'

py -3 mirror_nvidia_pypi.py --output nvidia-index-small --packages nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 nvidia-cudnn-cu12
```

`py -3 --version` должен показывать Python 3.12 или новее. Если Python установлен
без команды `py`, используйте `python`. Пути с пробелами заключайте в кавычки,
например `--output 'C:\Mirrors\NVIDIA index'`.

Linux (Bash):

```bash
export NVIDIA_REPO='https://artifactory.example.com/artifactory/nvidia-files'
export INDEX_URL='https://artifactory.example.com/artifactory/nvidia-index'

python3 mirror_nvidia_pypi.py --output nvidia-index-small --packages \
  nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 nvidia-cudnn-cu12
```

`--packages` принимает список имён: для проверки можно указать от двух до пяти
пакетов или другое нужное количество. Корневая страница будет содержать только
выбранные пакеты. Все версии каждого выбранного пакета сохраняются в его HTML.
Имена нормализуются по правилам Python-индекса: регистр не учитывается,
последовательности `-`, `_` и `.` заменяются на `-`.

Результат примера:

```text
nvidia-index-small/
├── index.html
├── nvidia-cuda-runtime-cu12/index.html
├── nvidia-cublas-cu12/index.html
└── nvidia-cudnn-cu12/index.html
```

## Полный индекс

С теми же переменными окружения:

```powershell
py -3 mirror_nvidia_pypi.py --output nvidia-index
```

На Linux:

```bash
python3 mirror_nvidia_pypi.py --output nvidia-index
```

Скрипт рекурсивно обходит ссылки на HTML на исходном хосте, включая вложенные
страницы, и не скачивает один URL повторно. Страницами считаются пути с завершающим
`/`, без расширения, а также `.html` и `.htm`. Это обход доступных ссылок,
а не поиск не связанных с индексом страниц сервера.
HTML-страницы с query-параметрами и тегом `<base>` отклоняются с ошибкой:
скрипт не пытается размещать разные динамические страницы в одном статическом файле.
Пути HTML, недопустимые на Windows (например, `C:`, `NUL`, имена с завершающей
точкой или пробелом), отклоняются на всех платформах, без изменения исходных имён.

Каталог результата не должен существовать. Загрузка выполняется во временный
каталог рядом с ним; после успешного завершения каталог атомарно переименовывается.
При ошибке временные файлы удаляются, команда возвращает ненулевой код.
Для обновления создайте новый каталог и загрузите его после успешного завершения.
Каждый запуск должен использовать отдельный каталог результата; одновременная
запись в один путь другими процессами не поддерживается.

Параметры: `--workers 8`, `--timeout 30`, `--retries 3`. Таймаут задаётся в секундах;
повторяются временные сетевые ошибки, HTTP 408, 429 и 5xx. TLS проверяется
стандартными средствами Python. Подробнее: `py -3 mirror_nvidia_pypi.py --help`
на Windows или `python3 mirror_nvidia_pypi.py --help` на Linux.

## Системные сертификаты

Скрипт создаёт `ssl.create_default_context()` и использует его для корневой
страницы и всех параллельных загрузок. Проверка цепочки сертификатов и имени
сервера включена. По [документации Python](https://docs.python.org/3.12/library/ssl.html#ssl.SSLContext.load_default_certs):

- на Windows загружаются сертификаты из системных хранилищ `CA` и `ROOT`;
- на Linux используются стандартные пути CA, настроенные в Python/OpenSSL.

Если корпоративный прокси подменяет HTTPS-сертификаты, его доверенный корневой
сертификат должен быть установлен в хранилище, доступном учётной записи,
от которой запускается скрипт. Экспортировать сертификаты Windows в PEM для
обычного запуска не требуется. Отдельный набор сертификатов `certifi` не используется.

Для явной настройки OpenSSL также доступны `SSL_CERT_FILE` (PEM-файл CA) и
`SSL_CERT_DIR` (каталог CA в формате OpenSSL). На Windows эти настройки дополняют
загружаемые системные хранилища. Ошибки доверия или несовпадения имени сервера
завершают запуск с ошибкой; отключение проверки TLS не предусмотрено.

## URL и размещение в Artifactory

Обе переменные должны содержать полный `http://` или `https://` URL без query
и fragment. Завершающий `/` необязателен. Пример преобразования:

```text
https://pypi.nvidia.com/nvidia-cuda-runtime-cu12/
→ ${INDEX_URL}/nvidia-cuda-runtime-cu12/

https://pypi.nvidia.com/nvidia-cuda-runtime-cu12/example.whl#sha256=abc
→ ${NVIDIA_REPO}/nvidia-cuda-runtime-cu12/example.whl#sha256=abc
```

Загрузите **содержимое** каталога результата под `INDEX_URL`, сохранив структуру.
Файлы пакетов должны быть доступны под `NVIDIA_REPO` по тем же путям, например
через настроенный remote repository с upstream `https://pypi.nvidia.com/`.
Метаданные `.metadata`, если они заявлены в исходном HTML, также должны быть доступны.
Скрипт не загружает результат в Artifactory и не копирует туда бинарные файлы.

**Для pip URL `${INDEX_URL}/<package>/` должен возвращать содержимое
`<package>/index.html` с типом `text/html`.** Обычный список файлов каталога не
заменяет страницу Python-индекса. Проверьте это после загрузки:

```bash
curl -fSL "$INDEX_URL/nvidia-cuda-runtime-cu12/"
python3 -m pip index versions nvidia-cuda-runtime-cu12 --index-url "$INDEX_URL"
python3 -m pip install --index-url "$INDEX_URL" nvidia-cuda-runtime-cu12
```

Если Artifactory возвращает список каталога, настройте reverse proxy так, чтобы
запрос `/nvidia-index/<package>/` отдавал `/nvidia-index/<package>/index.html`.
Ссылка на `index.html` только в корневой странице не решает эту задачу: pip сам
строит URL проекта по [спецификации Simple Repository API](https://packaging.python.org/en/latest/specifications/simple-repository-api/).
Для зависимостей, которых нет в NVIDIA-индексе, потребуется отдельный доступный
источник пакетов.

## Разработка

```bash
uv sync --dev
uv run mirror-nvidia-pypi --help
uv lock --check
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen pytest
uv build
```

CI запускает проверки на Windows и Linux с Python 3.12 и 3.14. Автоматические
тесты используют временные локальные HTTP/HTTPS-серверы, без доступа к NVIDIA.
Они проверяют успешную загрузку с доверенным CA, отказ при недоверенном сертификате
и несовпадении имени сервера, а также использование API хранилища Windows.
Системное хранилище при тестировании не меняется. Тест символической ссылки
пропускается на Windows, если для её создания не хватает прав.
Сгенерированные индексы и `.env` исключены из Git.
