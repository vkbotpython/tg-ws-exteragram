# NOTICE / Атрибуция

Проект в целом распространяется под **GNU General Public License v3.0** —
полный текст находится в файле `LICENSE`.

Часть кода (`wsproxy/*`) является портом **tg-ws-proxy** авторства **Flowseal**,
распространяемого под лицензией MIT. Условия MIT требуют сохранить оригинальное
уведомление об авторстве и текст лицензии — они приведены ниже и остаются в силе
для этих файлов. MIT-совместим с GPL-3.0, поэтому включение этого кода в
GPL-проект допустимо.

- Оригинальный проект: <https://github.com/Flowseal/tg-ws-proxy>
- Оригинальная лицензия: MIT, Copyright (c) 2026 Flowseal

Изменения, сделанные при порте в Elyx: удаление CLI/tray и десктопной проверки
обновлений, поиск CA-бандла для Android, опциональные HTTP/2-зависимости,
обёртка жизненного цикла с фоновым event loop и мультибэкендная реализация AES-CTR.

```text
MIT License

Copyright (c) 2026 Flowseal

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Бандл корневых сертификатов `certifi` (`assets/cacert.pem`, `wheels/certifi-*.whl`)
распространяется по лицензии Mozilla Public License 2.0; её текст входит в
соответствующий wheel.
