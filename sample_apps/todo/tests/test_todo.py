"""Selenium suite for the todo sample app (10 tests)."""

from __future__ import annotations

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def _wait(driver, timeout: int = 5) -> WebDriverWait:
    return WebDriverWait(driver, timeout)


def _add(driver, text: str) -> None:
    driver.find_element(By.ID, "new-todo").send_keys(text)
    driver.find_element(By.ID, "add-btn").click()


def _items(driver):
    return driver.find_elements(By.CSS_SELECTOR, "#todo-list li")


def _count_text(driver) -> str:
    return driver.find_element(By.ID, "todo-count").text


def test_add_todo(driver):
    _add(driver, "Buy milk")
    _wait(driver).until(lambda d: len(_items(d)) == 1)
    assert _items(driver)[0].find_element(By.CLASS_NAME, "todo-text").text == "Buy milk"


def test_add_empty_ignored(driver):
    _add(driver, "   ")
    assert len(_items(driver)) == 0
    assert driver.find_element(By.ID, "empty-state").is_displayed()


def test_toggle_complete(driver):
    _add(driver, "Task one")
    box = _wait(driver).until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".todo-toggle")))
    box.click()
    _wait(driver).until(lambda d: "completed" in _items(d)[0].get_attribute("class"))
    assert _count_text(driver) == "0 items left"


def test_delete_todo(driver):
    _add(driver, "Temp task")
    _wait(driver).until(lambda d: len(_items(d)) == 1)
    driver.find_element(By.CSS_SELECTOR, ".todo-delete").click()
    _wait(driver).until(lambda d: len(_items(d)) == 0)


def test_filter_active(driver):
    _add(driver, "Active task")
    _add(driver, "Done task")
    _wait(driver).until(lambda d: len(_items(d)) == 2)
    _items(driver)[1].find_element(By.CSS_SELECTOR, ".todo-toggle").click()
    driver.find_element(By.CSS_SELECTOR, 'button[data-filter="active"]').click()
    _wait(driver).until(lambda d: len(_items(d)) == 1)
    assert _items(driver)[0].find_element(By.CLASS_NAME, "todo-text").text == "Active task"


def test_filter_completed(driver):
    _add(driver, "Active task")
    _add(driver, "Done task")
    _wait(driver).until(lambda d: len(_items(d)) == 2)
    _items(driver)[1].find_element(By.CSS_SELECTOR, ".todo-toggle").click()
    driver.find_element(By.CSS_SELECTOR, 'button[data-filter="completed"]').click()
    _wait(driver).until(lambda d: len(_items(d)) == 1)
    assert _items(driver)[0].find_element(By.CLASS_NAME, "todo-text").text == "Done task"


def test_clear_completed(driver):
    _add(driver, "Keep me")
    _add(driver, "Drop me")
    _wait(driver).until(lambda d: len(_items(d)) == 2)
    _items(driver)[1].find_element(By.CSS_SELECTOR, ".todo-toggle").click()
    driver.find_element(By.ID, "clear-completed").click()
    _wait(driver).until(lambda d: len(_items(d)) == 1)
    assert _items(driver)[0].find_element(By.CLASS_NAME, "todo-text").text == "Keep me"


def test_count_updates(driver):
    assert _count_text(driver) == "0 items left"
    _add(driver, "One")
    _add(driver, "Two")
    _wait(driver).until(lambda d: _count_text(d) == "2 items left")


def test_persist_reload(driver):
    _add(driver, "Persistent")
    _wait(driver).until(lambda d: len(_items(d)) == 1)
    driver.refresh()
    _wait(driver).until(lambda d: len(_items(d)) == 1)
    assert _items(driver)[0].find_element(By.CLASS_NAME, "todo-text").text == "Persistent"


def test_empty_state(driver):
    assert driver.find_element(By.ID, "empty-state").is_displayed()
    _add(driver, "Something")
    _wait(driver).until(EC.invisibility_of_element_located((By.ID, "empty-state")))
