# План: порт `tg-ws-proxy` в плагин exteraGram (Elyx)

> **Статус:** реализовано. Оригинальный каталог `tg-ws-proxy-main/` удалён после
> порта; его код живёт в `wsproxy/`, а атрибуция — в `LICENSE` и `NOTICE.md`.
> Этот документ сохранён как историческая спецификация.

## Исходные данные

- Исходник `tg-ws-proxy` (лежит в проекте как `tg-ws-proxy-main/`) — локальный MTProto‑прокси (`127.0.0.1:1443`). Принимает obfuscated‑сессии Telegram, достаёт DC ID из пакета инициализации и вместо прямого TCP отправляет трафик через WebSocket/TLS к DC (`kwsN.web.telegram.org`), с fallback'ами на CF‑proxy / CF‑Worker / HTTP2 / TCP.
- Объём исходного кода ~4.7k строк, модули `proxy/*`, зависимости: stdlib + `certifi`, `httpx[http2]`, `h2`, `cryptography`.
- Плагины exteraGram — Python 3.11 через Chaquopy, SDK `BasePlugin`. Формат Elyx позволяет многофайловый архив, локальные модули, bundled wheels, строки и ассеты — сетевое ядро можно перенести почти один‑в‑один.
- Требуется полный паритет функционала, формат `.elyx`, автонастройка прокси в exteraGram с фолбэком на `tg://proxy`, дебаг через UI плагина (логи + статус/статистика), без adb.

## Целевая структура

```
tg_ws_bridge.elyx/
├─ refmap.yml                 # main, metainfo, wheels, strings, assets
├─ metainfo.yml               # id, sdk_version >=1.4.5.3, requirements
├─ main.py                    # TgWsBridgePlugin(BasePlugin): жизненный цикл, настройки, дебаг
├─ plugin/
│  ├─ runtime.py              # фоновый asyncio‑loop в отдельном потоке, start/stop/restart
│  ├─ config_bridge.py        # настройки плагина → proxy_config
│  ├─ telegram_proxy.py       # автонастройка MTProto‑прокси + tg://proxy фолбэк
│  ├─ logbus.py               # ring‑буфер логов + logging.Handler + файл логов
│  ├─ debug_ui.py             # экраны «Логи» и «Статус/Статистика»
│  └─ crypto_backend.py       # AES‑CTR: Java JCE → pyaes → cryptography
├─ wsproxy/                   # порт proxy/* (адаптированы импорты и API, без CLI/tray)
├─ vendor/pyaes/              # чистый Python AES‑CTR как запасной бэкенд
├─ wheels/                    # certifi, httpx, httpcore, h11, h2, hpack, hyperframe, anyio, sniffio, idna
└─ strings/strings_{en,ru}.yml
```

## Этап 0 — проверка совместимости (PoC)

1. Минимальный Elyx‑плагин: поднять `asyncio`‑сервер на `127.0.0.1`, принять коннект, сделать TLS‑рукоп握手ение к `kws1.web.telegram.org:443` через `ssl`.
2. Проверить доступность в Chaquopy: `socket`, `ssl` (ALPN, cafile), `hashlib`, `hmac`, `asyncio`, `ctypes`, Java `javax.crypto.Cipher`.
3. Проверить, что плагин может записать/прочитать настройки прокси exteraGram (рефлексия `ConnectionsManager`/prefs) и открыть `tg://proxy` через `Intent.ACTION_VIEW`.
4. По результатам PoC зафиксировать бэкенд AES и способ включения прокси.

## Этап 1 — порт ядра

Перенести `proxy/*` в `wsproxy/`, сохранив протокольную логику без изменений:

- `tg_ws_proxy.py` → вместо `argparse`/`main()` публичные `start(stop_event)` / `stop()`;
- `config.py`, `utils.py`, `bridge.py`, `raw_websocket.py`, `fake_tls.py`, `pool.py`, `balancer.py`, `cf_h2.py`, `h2_transport.py`, `stats.py`, `network_debug.py`, `event_loop.py`, `_aes.py`;
- убрать/заглушить десктопные части: `signal` в `event_loop.py`, `sys._MEIPASS`, проверку обновлений GitHub, tray/CLI; сетевые фетчи сделать неблокирующими и не фатальными;
- логирование перевести на `logbus` (тот же `logging`-API + `DomainCensorFilter`), чтобы всё попадало в UI.

## Этап 2 — зависимости, которых нет в Chaquopy

- `cryptography` заменить на `_aes.py` с бэкендами: ① Java `javax.crypto` (`AES/CTR/NoPadding`, big‑endian счётчик — совместим с текущим CTR), ② `pyaes` (чистый Python), ③ `cryptography`, если доступен. Сохранить публичный API `Cipher`/`algorithms`/`modes`.
- `certifi`, `httpx`, `h2` и транзитивные зависимости положить в `wheels/` (все `py3-none-any`), плюс объявить в `metainfo.yml` как fallback через PIP.
- Если `httpx` не заведётся на устройстве — запасной путь: переписать `h2_transport.py` на тонкий транспорт поверх `h2` + `asyncio` + SSL, чтобы H2‑медиа работало без httpx.

## Этап 3 — жизненный цикл плагина

- `on_plugin_load`: собрать `proxy_config` из настроек → запустить `ProxyRuntime` → применить прокси → запустить таймер статуса.
- `ProxyRuntime`: отдельный `threading.Thread` со своим `asyncio`-loop; старт/стоп через `run_coroutine_threadsafe`; корректный `stop()` (отмена задач, закрытие пулов, `loop.stop()`, `join(timeout)`). Защита от дублей при Elyx‑reload.
- `on_app_event(START/STOP/PAUSE/RESUME)`: перезапуск сервера при возврате в приложение, остановка на STOP.
- `on_plugin_unload`: остановить сервер, снять logging‑handler, восстановить прежние настройки прокси (если включено), очистить таймеры. Всё — в `on_plugin_unload`, иначе reload оставит мёртвый поток/занятый порт.

## Этап 4 — подключение к exteraGram

- Основной путь: рефлексией (`hook_utils.find_class`, `get/set_private_field`) выставить адрес/порт/секрет и вызвать `ConnectionsManager.setProxySettings(...)`. Точные поля и сигнатуру уточнить интроспекцией на целевом билде (для этого в дебаг‑UI будет кнопка «Дамп конфигурации прокси»).
- Фолбэк: построить `tg://proxy?server=127.0.0.1&port=1443&secret=dd<hex>` (или `ee<hex><domain_hex>` при fake TLS) и открыть через `Intent.ACTION_VIEW` / `AndroidUtilities`; пользователь подтверждает подключение.
- До включения запомнить прежние значения и опционально вернуть их при выключении плагина (настройка «Восстанавливать прокси при выключении»).

## Этап 5 — настройки

Секции в `create_settings()`:

- **Основное**: `enabled`, порт/хост, `secret` (32 hex, автогенерация и кнопка «сгенерировать заново»), кнопки «Старт/Стоп/Рестарт».
- **Транспорт**: `pool_size`, `buf_kb`, список DC→IP, `fake_tls_domain`, `force_test_dc`, `proxy_protocol`.
- **Fallback'ы**: `fallback_cfproxy`, домены CF proxy / Worker, `h2_enabled`, `disable_secure`.
- **Подключение**: `auto_apply_proxy`, «Открыть tg://proxy», «Копировать ссылку», «Восстанавливать при выключении».
- **Дебаг**: уровень логов, размер буфера, «Логи», «Статус и статистика».

## Этап 6 — дебаг без adb

- `logbus.py`: `collections.deque(maxlen=N)` + `logging.Handler`, запись в файл в каталоге плагина с ротацией; все traceback'и потоков и хуков попадают сюда.
- Экран «Логи»: `AlertDialogBuilder` со `ScrollView`/`TextView` (или под‑фрагмент настроек со списком строк); кнопки «Обновить», «Копировать» (`copy_to_clipboard`), «Очистить», переключатель уровня `DEBUG/INFO`.
- Экран «Статус»: состояние движка, uptime, активные/всего/bad соединения, байты, WS‑пул по DC, последняя ошибка; кнопка обновления; опционально авто‑рефреш через `set_setting(..., reload_settings=True)`.
- Обновление UI — только через `run_on_ui_thread`; уведомления — `BulletinHelper`.

## Этап 7 — проверка и очистка

1. На ПК: прогнать исходные `tests/*` против портированного `wsproxy/` под Python 3.11 с тестовым AES‑бэкендом; сравнить поведение handshake/bridge с оригиналом.
2. На устройстве (без adb для дебага): установить `.elyx` через чат, включить, проверить по UI, что сервер слушает, статистика растёт, в логах есть `DCx -> WS pool hit`, сообщения ходят.
3. Проверить выключение/включение/reload плагина, смену порта и секрета, работу фолбэков, восстановление прежнего прокси.
4. **После полной проверки удалить `tg-ws-proxy-main/` из проекта**, оставив `NOTICE.md` с атрибуцией MIT и ссылкой на оригинал (обязательно при порте лицензированного кода).

## Риски и меры

- `ssl`/CA/ALPN в Chaquopy — проверить на Этапе 0; при проблеме использовать bundled `cacert.pem` или Java TLS.
- Производительность чистого AES — приоритет Java JCE, pyaes только запасной.
- `httpx`/`h2` на Android — bundled wheels + запасной H2‑транспорт.
- API настройки прокси отличается в сборке exteraGram — интроспекция + deeplink‑фолбэк.
- Doze/убийство процесса — документировать, перезапуск при `AppEvent.START`; порт настраиваемый на случай конфликта.
- Утечка потоков при hot‑reload — строгий `on_plugin_unload` + реестр рантаймов.
