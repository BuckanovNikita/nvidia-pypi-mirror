# Зеркало NVIDIA PyPI

Скрипт скачивает HTML-страницы, доступные по ссылкам из
[pypi.nvidia.com](https://pypi.nvidia.com/), и готовит статический индекс для вашего сервера:

- ссылки на файлы с `pypi.nvidia.com` получают базовый URL из `NVIDIA_REPO`;
- ссылки между страницами индекса получают базовый URL из `INDEX_URL`;
- исходные пути, параметры URL файлов, хеши `#sha256` и атрибуты пакетов сохраняются;
- относительные ссылки разрешаются относительно исходной страницы;
- ссылки на другие хосты остаются без изменений;
- wheel-архивы и исходники пакетов не скачиваются.

Нужен Python 3.12+. Для запуска скрипта сторонние библиотеки не требуются.

## Быстрая проверка: три пакета

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

```bash
python3 mirror_nvidia_pypi.py --output nvidia-index
```

Скрипт рекурсивно обходит ссылки на HTML на исходном хосте, включая вложенные
страницы, и не скачивает один URL повторно. Страницами считаются пути с завершающим
`/`, без расширения, а также `.html` и `.htm`. Это обход доступных ссылок,
а не поиск не связанных с индексом страниц сервера.
HTML-страницы с query-параметрами и тегом `<base>` отклоняются с ошибкой:
скрипт не пытается размещать разные динамические страницы в одном статическом файле.

Каталог результата не должен существовать. Загрузка выполняется во временный
каталог рядом с ним; после успешного завершения каталог атомарно переименовывается.
При ошибке временные файлы удаляются, команда возвращает ненулевой код.
Для обновления создайте новый каталог и загрузите его после успешного завершения.
Каждый запуск должен использовать отдельный каталог результата; одновременная
запись в один путь другими процессами не поддерживается.

Параметры: `--workers 8`, `--timeout 30`, `--retries 3`. Таймаут задаётся в секундах;
повторяются временные сетевые ошибки, HTTP 408, 429 и 5xx. TLS проверяется
стандартными средствами Python. Подробнее: `python3 mirror_nvidia_pypi.py --help`.

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

Автоматические тесты используют временный локальный HTTP-сервер, без доступа к
NVIDIA. Сгенерированные индексы и `.env` исключены из Git.
