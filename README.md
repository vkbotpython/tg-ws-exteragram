<div align="center">
    <a href="https://www.youtube.com/@avencores/" target="_blank">
      <img src="https://github.com/user-attachments/assets/338bcd74-e3c3-4700-87ab-7985058bd17e" alt="YouTube" height="40">
    </a>
    <a href="https://t.me/avencoresyt" target="_blank">
      <img src="https://github.com/user-attachments/assets/939f8beb-a49a-48cf-89b9-d610ee5c4b26" alt="Telegram" height="40">
    </a>
    <a href="https://vk.ru/avencoresreuploads" target="_blank">
      <img src="https://github.com/user-attachments/assets/dc109dda-9045-4a06-95a5-3399f0e21dc4" alt="VK" height="40">
    </a>
    <a href="https://dzen.ru/avencores" target="_blank">
      <img src="https://github.com/user-attachments/assets/bd55f5cf-963c-4eb8-9029-7b80c8c11411" alt="Dzen" height="40">
    </a>
</div>

# tg_ws_bridge

[![tests](https://github.com/avencores/tg_ws_bridge/actions/workflows/tests.yml/badge.svg)](https://github.com/avencores/tg_ws_bridge/actions/workflows/tests.yml)

**Локальный мост MTProto → WebSocket в виде плагина exteraGram / AyuGram формата Elyx.**

Порт [Flowseal/tg-ws-proxy](https://github.com/Flowseal/tg-ws-proxy) (MIT) на Python 3.11 / Chaquopy. Плагин принимает обфусцированные сессии Telegram на `127.0.0.1:1443`, извлекает ID DC из пакета инициализации и передаёт трафик через WebSocket/TLS на `kwsN.web.telegram.org`, с fallback'ами на Cloudflare proxy / Worker / HTTP2 / TCP.

Вся отладка — логи, живая статистика и дамп конфигурации прокси — доступна прямо из настроек плагина. **adb не требуется.**

## Возможности

- Полный паритет протокольной логики с оригинальным прокси (`proxy/*` → `wsproxy/`)
- Автоматическое включение прокси через рефлексию с фолбэком на `tg://proxy`
- AES-CTR с цепочкой бэкендов: Java JCE → `cryptography` → чистый Python → OpenSSL
- В комплекте чистые Python-зависимости (wheels) — без компилятора и нативных модулей на устройстве
- Фоновый asyncio-цикл в отдельном потоке: корректный stop/restart и защита от утечек при hot-reload
- Просмотр логов (кольцевой буфер + ротирующийся файл) и экран статуса/статистики внутри плагина
- Опциональная маскировка Fake TLS (`ee`-secret), свои перенаправления DC, поддержка PROXY protocol

## Установка

1. Скачайте `tg_ws_bridge-*.elyx` из [Releases](../../releases) или соберите archive самостоятельно (см. ниже).
2. Отправьте файл в «Избранное» в exteraGram и откройте его → **Install Plugin**.
3. Откройте настройки плагина. 32-символьный hex-секрет создаётся автоматически.
4. Оставьте **Enabled** включённым. При включённом **Apply proxy automatically** плагин сначала пытается рефлексию; если API прокси в этой сборке неизвестен — открывает `tg://proxy` для подтверждения.
5. Откройте **Status and statistics**: `RUNNING`, растущие счётчики `total` / `up` / `down` и строка `DCx -> WS pool hit` в **Logs** подтверждают работу моста.

Для ручного подключения используйте **Open tg://proxy** или **Copy tg://proxy link**.

## Сборка через GitHub Actions (вручную)

1. Откройте вкладку **Actions** → workflow **build plugin** → **Run workflow**.
2. При желании укажите метку версии (по умолчанию используется `manual-<номер run'а>`).
3. После завершения скачайте артефакт `tg_ws_bridge-<версия>.elyx` со страницы run'а.
4. Отправьте `.elyx` в «Избранное» в клиенте и откройте файл для установки.

Workflow проверяет состав пакета (обязательные `refmap.yml`, `main.py`, `LICENSE`,
`NOTICE.md`, `cacert.pem`) и хранит артефакт 90 дней. По тегу `v*` отдельный
workflow `release` публикует готовый архив в Releases.

## Сборка из исходников

Каноничный способ (ElyxBuilder):

```bash
elyb build -nf
# builds/tg_ws_bridge-1.0.0.elyx
```

Запасной способ без дополнительных зависимостей:

```bash
python scripts/build_elyx.py                    # builds/tg_ws_bridge-1.0.0.elyx
python -c "from scripts.build_elyx import verify; verify()"
```

Обновление bundled wheels (только на ПК, не на устройстве):

```bash
pip download --only-binary=:all: --platform any --python-version 3.11 \
  --implementation cp --abi none --dest wheels \
  "httpx[http2]==0.28.1" certifi sniffio
```

## Структура репозитория

```
refmap.yml          карта компонентов Elyx (main / metainfo / strings / assets / wheels)
metainfo.yml        id, sdk_version >=1.4.5.3, requirements
meta.yml            копия для совместимости со старыми валидаторами Elyx
main.py             TgWsBridgePlugin(BasePlugin)
plugin/             совместимость с SDK, рантайм, мост настроек, хук прокси,
                    лог-шина, дебаг-UI, криптобэкенд
wsproxy/            портированное ядро прокси (протокольная логика не изменена)
vendor/pyaes/       чистый Python AES (проверен по NIST-векторам)
wheels/             certifi, httpx, httpcore, h11, h2, hpack, hyperframe,
                    anyio, sniffio, idna (все py3-none-any)
strings/ locales/   строки en/ru (YAML для Elyx, JSON для совместимости)
assets/cacert.pem   bundled CA-бандл для Chaquopy
docs/PLAN.ru.md     исходный план реализации
tests_port/         портированные тесты ядра + тесты плагина
scripts/            скрипты сборки и проверки
```

## Разработка

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests_port -q
```

Текущий результат: **230 passed, 415 subtests passed** (Python 3.11+).
CI прогоняет тесты на Python 3.11, 3.12 и 3.13.

### AES-бэкенд

Выбирается автоматически, переопределяется через `TGWS_AES_BACKEND=java|cryptography|pyaes|openssl`:

1. `java` — `javax.crypto.Cipher("AES/CTR/NoPadding")` (предпочтительно на Android)
2. `cryptography` — ПК / тесты
3. `pyaes` — встроенный чистый Python (проверен по NIST-векторам)
4. `openssl` — ctypes-обёртка над libcrypto

Все бэкенды используют 128-битный big-endian счётчик, как оригинальный режим CTR.

## Ограничения и примечания

- Точная сигнатура `ConnectionsManager.setProxySettings(...)` отличается в разных сборках exteraGram. Для интроспекции используйте **Dump proxy configuration** в дебаг-меню; фолбэк `tg://proxy` работает всегда.
- Doze / убийство процесса может остановить слушатель. Плагин перезапускает его на `START`/`RESUME`; если порт 1443 занят — измените его в настройках.
- HTTP/2 для медиа требует bundled wheels; без них мост автоматически использует WS/CF/TCP.
- Fake TLS требует TLS 1.3 в системном `ssl`; иначе используются случайные размеры ApplicationData.
- Не публикуйте секрет прокси — обращайтесь с ним как с паролем.

## Лицензия

**GNU GPL v3.0** — полный текст в [LICENSE](LICENSE).

Ядро `wsproxy/` является портом MIT-проекта tg-ws-proxy, поэтому оригинальное
MIT-уведомление сохранено в [NOTICE.md](NOTICE.md). Бандл `certifi` распространяется
по MPL-2.0 (текст входит в соответствующий wheel).

---

# 💰 Поддержать автора
+ **SBER**: `2202 2050 1464 4675`