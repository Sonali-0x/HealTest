"""Selenium suite for the multistep-form sample app (9 tests)."""

from __future__ import annotations

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait


def _wait(driver, timeout: int = 5) -> WebDriverWait:
    return WebDriverWait(driver, timeout)


def _fill_step1(driver) -> None:
    driver.find_element(By.ID, "first-name").send_keys("Ada")
    driver.find_element(By.ID, "last-name").send_keys("Lovelace")
    driver.find_element(By.ID, "email").send_keys("ada@example.com")
    driver.find_element(By.ID, "next-1").click()


def _fill_step2(driver) -> None:
    driver.find_element(By.ID, "street").send_keys("1 Main St")
    driver.find_element(By.ID, "city").send_keys("Springfield")
    Select(driver.find_element(By.ID, "country")).select_by_value("US")
    driver.find_element(By.ID, "next-2").click()


def _current_step(driver) -> str:
    return driver.find_element(By.CSS_SELECTOR, "#progress li.current").get_attribute("data-step")


def test_step1_renders(driver):
    assert driver.find_element(By.ID, "step-1").is_displayed()
    assert not driver.find_element(By.ID, "step-2").is_displayed()


def test_step1_validation_blocks_empty(driver):
    driver.find_element(By.ID, "next-1").click()
    _wait(driver).until(lambda d: d.find_element(By.ID, "error-1").text == "All fields are required.")
    assert _current_step(driver) == "1"


def test_email_validation(driver):
    driver.find_element(By.ID, "first-name").send_keys("Ada")
    driver.find_element(By.ID, "last-name").send_keys("Lovelace")
    driver.find_element(By.ID, "email").send_keys("not-an-email")
    driver.find_element(By.ID, "next-1").click()
    _wait(driver).until(lambda d: d.find_element(By.ID, "error-1").text == "Invalid email.")


def test_navigate_to_step2(driver):
    _fill_step1(driver)
    _wait(driver).until(lambda d: d.find_element(By.ID, "step-2").is_displayed())
    assert _current_step(driver) == "2"


def test_back_navigation(driver):
    _fill_step1(driver)
    _wait(driver).until(lambda d: d.find_element(By.ID, "step-2").is_displayed())
    driver.find_element(By.ID, "back-2").click()
    _wait(driver).until(lambda d: d.find_element(By.ID, "step-1").is_displayed())
    assert driver.find_element(By.ID, "first-name").get_attribute("value") == "Ada"


def test_step2_validation(driver):
    _fill_step1(driver)
    _wait(driver).until(lambda d: d.find_element(By.ID, "step-2").is_displayed())
    driver.find_element(By.ID, "next-2").click()
    _wait(driver).until(lambda d: d.find_element(By.ID, "error-2").text == "All fields are required.")


def test_review_summary_contents(driver):
    _fill_step1(driver)
    _wait(driver).until(lambda d: d.find_element(By.ID, "step-2").is_displayed())
    _fill_step2(driver)
    _wait(driver).until(lambda d: d.find_element(By.ID, "step-3").is_displayed())
    summary = driver.find_element(By.ID, "review-summary").text
    assert "Ada Lovelace" in summary
    assert "ada@example.com" in summary


def test_progress_indicator(driver):
    assert _current_step(driver) == "1"
    _fill_step1(driver)
    _wait(driver).until(lambda d: _current_step(d) == "2")
    done = driver.find_elements(By.CSS_SELECTOR, "#progress li.done")
    assert len(done) == 1


def test_full_flow_submit(driver):
    _fill_step1(driver)
    _wait(driver).until(lambda d: d.find_element(By.ID, "step-2").is_displayed())
    _fill_step2(driver)
    _wait(driver).until(lambda d: d.find_element(By.ID, "step-3").is_displayed())
    driver.find_element(By.ID, "submit-btn").click()
    _wait(driver).until(lambda d: d.find_element(By.ID, "success-message").is_displayed())
