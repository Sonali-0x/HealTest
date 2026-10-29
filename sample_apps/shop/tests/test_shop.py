"""Selenium suite for the shop sample app (10 tests)."""

from __future__ import annotations

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


def _wait(driver, timeout: int = 5) -> WebDriverWait:
    return WebDriverWait(driver, timeout)


def _add_first(driver) -> None:
    driver.find_elements(By.CSS_SELECTOR, ".add-to-cart")[0].click()


def _cart_count(driver) -> str:
    return driver.find_element(By.ID, "cart-count").text


def _open_cart(driver) -> None:
    driver.find_element(By.ID, "cart-btn").click()
    _wait(driver).until(lambda d: "open" in d.find_element(By.ID, "cart-drawer").get_attribute("class"))


def test_products_listed(driver):
    names = [e.text for e in driver.find_elements(By.CSS_SELECTOR, ".product-name")]
    assert names == ["Red Widget", "Blue Widget", "Green Widget"]


def test_add_to_cart(driver):
    _add_first(driver)
    _wait(driver).until(lambda d: _cart_count(d) == "1")


def test_cart_count_accumulates(driver):
    buttons = driver.find_elements(By.CSS_SELECTOR, ".add-to-cart")
    buttons[0].click()
    buttons[1].click()
    buttons[1].click()
    _wait(driver).until(lambda d: _cart_count(d) == "3")


def test_open_cart_drawer(driver):
    _add_first(driver)
    _open_cart(driver)
    assert driver.find_element(By.ID, "cart-drawer").is_displayed()


def test_quantity_increase(driver):
    _add_first(driver)
    _open_cart(driver)
    driver.find_element(By.CSS_SELECTOR, ".qty-inc").click()
    _wait(driver).until(lambda d: d.find_element(By.CSS_SELECTOR, ".item-qty").text == "2")
    assert _cart_count(driver) == "2"


def test_quantity_decrease(driver):
    _add_first(driver)
    _add_first(driver)
    _open_cart(driver)
    driver.find_element(By.CSS_SELECTOR, ".qty-dec").click()
    _wait(driver).until(lambda d: d.find_element(By.CSS_SELECTOR, ".item-qty").text == "1")


def test_remove_item(driver):
    _add_first(driver)
    _open_cart(driver)
    driver.find_element(By.CSS_SELECTOR, ".remove-item").click()
    _wait(driver).until(lambda d: _cart_count(d) == "0")
    assert driver.find_element(By.ID, "empty-cart").is_displayed()


def test_cart_total(driver):
    driver.find_elements(By.CSS_SELECTOR, ".add-to-cart")[0].click()  # $10.00
    driver.find_elements(By.CSS_SELECTOR, ".add-to-cart")[2].click()  # $5.50
    _open_cart(driver)
    _wait(driver).until(lambda d: d.find_element(By.ID, "cart-total").text == "$15.50")


def test_checkout_flow(driver):
    _add_first(driver)
    _open_cart(driver)
    driver.find_element(By.ID, "checkout-btn").click()
    _wait(driver).until(lambda d: d.find_element(By.ID, "checkout-form").is_displayed())
    driver.find_element(By.ID, "buyer-name").send_keys("Ada")
    driver.find_element(By.ID, "buyer-email").send_keys("ada@example.com")
    driver.find_element(By.ID, "buyer-address").send_keys("1 Main St")
    driver.find_element(By.ID, "place-order").click()
    _wait(driver).until(lambda d: d.find_element(By.ID, "order-confirmation").is_displayed())
    assert _cart_count(driver) == "0"


def test_empty_cart_message(driver):
    _open_cart(driver)
    assert driver.find_element(By.ID, "empty-cart").is_displayed()
