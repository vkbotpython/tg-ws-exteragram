# NOTICE / Attribution

This plugin contains a port of **tg-ws-proxy**, originally developed by
**Flowseal** and distributed under the MIT License.

- Original project: <https://github.com/Flowseal/tg-ws-proxy>
- License: MIT, Copyright (c) 2026 Flowseal

The protocol engine (`wsproxy/*`) is derived from the original `proxy/*`
modules. Changes made for the Elyx port include: removal of the CLI/tray and
desktop update checks, an Android-friendly CA bundle lookup, optional HTTP/2
dependencies, a background-loop lifecycle wrapper, and a multi-backend AES-CTR
shim.

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

The bundled `certifi` CA bundle is distributed under the Mozilla Public
License 2.0; its license is included in the corresponding wheel under
`wheels/`.
