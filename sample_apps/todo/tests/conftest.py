"""Shared Selenium fixtures for the todo sample app."""

from __future__ import annotations

import os

import pytest
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

APP_PATH = os.environ.get("TODO_APP_PATH", "/todo/")


def _base_url() -> str:
    root = os.environ.get("APP_URL", "http://localhost:8901").rstrip("/")
    return root + APP_PATH


def _service() -> Service:
    path = os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
    if path and os.path.exists(path):
        return Service(executable_path=path)
    return Service()  # Selenium Manager resolves the driver (host dev)


@pytest.fixture
def base_url() -> str:
    return _base_url()


@pytest.fixture
def driver(base_url: str):
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1366,768")
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    drv = webdriver.Chrome(service=_service(), options=opts)
    drv.get(base_url)
    drv.execute_script("localStorage.clear();")
    drv.get(base_url)  # reload so the app boots with clean storage
    yield drv
    drv.quit()
