# Sample apps

Three static web apps (no build step), each with a Selenium + pytest suite.
Served by the `sample-apps` compose service at `http://sample-apps:8901/<app>/`
(`http://localhost:8901/<app>/` on the host).

| App | Page | Tests |
| --- | --- | --- |
| todo | `todo/index.html` | `todo/tests/test_todo.py` (10) |
| shop | `shop/index.html` | `shop/tests/test_shop.py` (10) |
| multistep-form | `multistep-form/tests/test_form.py` (9) |

Run one suite locally (needs Python, pytest, Selenium, Chrome):

```sh
cd sample_apps/todo/tests
APP_URL=http://localhost:8901 pytest -q
```

Element IDs are stable on purpose: the Phase 6 benchmark mutates them to
produce realistic breakages (renamed id, moved element, new overlay, ...).
