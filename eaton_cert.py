#!/usr/bin/env python3
"""
generate_csr_and_ssh.py

Automates:
  1. Logging into a web admin UI with Selenium
  2. Navigating to Settings -> Certificate
  3. Finding the "Web Server" row under the "Used for" column
  4. Opening its three-dot menu -> "Generate CSR | Import"
  5. In the popup, selecting "Generate signing request (CSR) excluding IP addresses"
     and clicking Submit
  6. Waiting 20 seconds, then running a set of commands natively/locally on
     this Ubuntu host (no remote SSH hop — this script IS running on the
     target server, so commands are run via subprocess).

NOTE ON SELECTORS
------------------
Every admin UI structures its DOM differently, so the exact By.XPATH / By.ID
selectors below are written generically and marked with `# ADJUST ME` where
you will almost certainly need to inspect the real page (right-click ->
Inspect) and swap in the actual element locators (id, name, css selector,
or a more precise xpath). I've used flexible XPath text-matching
(contains(text(), ...)) so it's more likely to work with minimal changes,
but you should verify against the real DOM.

REQUIREMENTS
------------
    pip install selenium
    -or-
    apt install python3-selenium

You'll also need a matching browser driver (e.g. chromedriver) available on
PATH, or use Selenium Manager (bundled with recent Selenium versions, which
will auto-resolve the driver for you).
"""

import os
import sys
import time
import shlex
import logging
import subprocess
from contextlib import contextmanager

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# CONFIG - fill these in (or load from env vars / a config file instead of
# hardcoding secrets here)
# --------------------------------------------------------------------------

BASE_URL = "https://eaton-url.com"   # REPLACE
WEB_USERNAME = "USERNAME_HERE"       # REPLACE
WEB_PASSWORD = "PASSWORD_GOES_HERE"  # REPLACE

# Explicit paths to work around Ubuntu's apt-packaged Selenium Manager binary
# often being broken/non-executable (NoSuchDriverException). Set these after
# running:  sudo apt install -y chromium-browser chromium-chromedriver
# then check with:  which chromedriver ; which chromium-browser
# Leave both as "" to fall back to Selenium Manager auto-resolution.
CHROMEDRIVER_PATH = "/usr/bin/chromedriver"    # ADJUST ME if needed (or "" to auto-resolve)
CHROME_BINARY_PATH = ""                        # ADJUST ME if needed, e.g. "/usr/bin/chromium-browser"

# Commands to run locally on this host once the CSR generation has been
# submitted. Each entry can be a plain shell string (run with shell=True,
# supports pipes/redirects) or a list of args (run without a shell, safer
# when arguments come from anywhere untrusted).
LOCAL_COMMANDS = [
    "sshpass -p [REPLACE_ME] ssh [USERNAME]@[URL] certificates local csr webserver > /tmp/cert-request-eaton.csr",
    "certbot certonly --csr /tmp/cert-request-eaton.csr --preferred-challenges dns-01 --dns-cloudflare --dns-cloudflare-credentials /etc/letsencrypt/cloudflare.ini --rsa-key-size 2048 --key-type rsa --cert-path /tmp/0000_cert.pem --chain-path /tmp/0000_chain.pem --fullchain-path /tmp/0001_chain.pem", # ADJUST CERTBOT AS NEEDED
    "cat /tmp/0001_chain.pem  | sshpass -p [REPLACE_ME] ssh [USERNAME]@[URL] certificates local import webserver",
    "rm /tmp/000*.pem",
    "rm /tmp/cert-request-eaton.csr"
]

# If True, run each command via a shell (needed for pipes, &&, redirects,
# etc). If False, commands must be given as arg lists, e.g. ["ls", "-la"].
USE_SHELL = True

# Set True if the commands need root privileges and this script itself is
# not already running as root (will prefix with `sudo`). Requires either
# passwordless sudo for this user, or that you run this whole script with
# sudo/as root to begin with.
RUN_WITH_SUDO = False

DEFAULT_WAIT = XX  # REPLACE ME seconds, for explicit waits on element visibility/clickability
POST_SUBMIT_WAIT = XX  # REPLACE ME seconds, the required wait after clicking Submit


# --------------------------------------------------------------------------
# Selenium helpers
# --------------------------------------------------------------------------

@contextmanager
def get_driver(headless: bool = None):
    """Create and yield a Chrome WebDriver, quitting it on exit.

    Prefers an explicit chromedriver path (CHROMEDRIVER_PATH) if set, since
    the apt-packaged python3-selenium's bundled Selenium Manager binary is
    frequently broken/non-executable on Ubuntu servers (raises
    NoSuchDriverException: "Unable to obtain working Selenium Manager
    binary"). If CHROMEDRIVER_PATH is empty, falls back to Selenium Manager
    auto-resolution, which requires that binary to actually work.

    If `headless` is left as None, auto-detects: headless server (no
    $DISPLAY) -> headless=True, desktop with a display -> headless=False.
    A "session not created: Chrome instance exited" error almost always
    means Chrome tried to open a real window with no display available --
    that's what this auto-detection avoids.
    """
    if headless is None:
        headless = not bool(os.environ.get("DISPLAY"))
        if headless:
            log.info("No $DISPLAY detected -> running Chrome headless.")

    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")  # headless has no real window to size
    else:
        options.add_argument("--start-maximized")
    options.add_argument("--ignore-certificate-errors")  # many admin UIs use self-signed certs
    options.add_argument("--no-sandbox")  # often required when running as root / in minimal server envs
    options.add_argument("--disable-dev-shm-usage")  # avoids crashes from limited /dev/shm on some servers
    options.add_argument("--disable-gpu")  # avoids GPU-related crashes on headless servers

    if CHROME_BINARY_PATH:
        options.binary_location = CHROME_BINARY_PATH

    if CHROMEDRIVER_PATH:
        # log_output surfaces chromedriver's verbose log to stdout so if Chrome
        # still fails to start, the real underlying reason is visible instead
        # of just "Chrome instance exited".
        service = webdriver.ChromeService(executable_path=CHROMEDRIVER_PATH, log_output=sys.stdout)
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    try:
        yield driver
    finally:
        driver.quit()


def login(driver, base_url: str, username: str, password: str):
    """Log into the Eaton admin UI (Angular Material app)."""
    log.info("Navigating to login page: %s", base_url)
    driver.get(base_url)

    wait = WebDriverWait(driver, DEFAULT_WAIT)

    # These ids come directly from the app's real DOM (confirmed via a debug
    # dump), so they should be exact -- no more guessing.
    username_field = wait.until(
        EC.presence_of_element_located((By.ID, "lib-ui-login-username-lib-input-input"))
    )
    password_field = driver.find_element(By.ID, "lib-ui-login-password-lib-input-input")

    username_field.clear()
    username_field.send_keys(username)
    password_field.clear()
    password_field.send_keys(password)

    log.info("Locating the login button...")
    try:
        login_button = wait.until(EC.element_to_be_clickable((By.ID, "lib-ui-login-submit")))
    except TimeoutException:
        dump_debug_artifacts(driver, "login_button_not_found")
        raise

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", login_button)
    try:
        login_button.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", login_button)

    # ADJUST ME: wait for some post-login element that proves the login succeeded
    # (e.g. a dashboard header, a nav menu, a logout link, etc.). Left as a
    # generic text match for now -- tighten this the same way once we see the
    # post-login page's real DOM.
    wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Settings')]")))
    log.info("Login successful.")


def navigate_to_certificate_page(driver):
    """Click Settings, then Certificate."""
    wait = WebDriverWait(driver, DEFAULT_WAIT)

    log.info("Clicking Settings...")
    settings_el = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//*[self::a or self::button or self::span][contains(., 'Settings')]"))
    )
    settings_el.click()

    log.info("Clicking Certificate...")
    certificate_el = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//*[self::a or self::button or self::span][contains(., 'Certificate')]"))
    )
    certificate_el.click()

    # ADJUST ME: wait for something that confirms the Certificate page has loaded,
    # e.g. the table header containing "Used for"
    wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Used for')]")))
    log.info("On Certificate page.")


def generate_csr_for_web_server(driver):
    """
    Find the table row where the 'Used for' column contains 'Web Server',
    open its three-dot (kebab) menu, click 'Generate CSR | Import', then in
    the popup select the 'excluding IP addresses' radio/option and Submit.
    """
    wait = WebDriverWait(driver, DEFAULT_WAIT)

    log.info("Locating the 'Web Server' row...")
    # ADJUST ME: this assumes a standard <table> with <tr> rows and that the
    # text "Web Server" appears somewhere in that row (likely in the
    # "Used for" column). Narrow the xpath if there are multiple matches.
    web_server_row = wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//tr[.//*[contains(text(), 'Web Server')]]")
        )
    )

    log.info("Opening the three-dot menu for the Web Server row...")
    # Exact id confirmed from the real DOM: the Web Server row's action-menu
    # trigger button is app-local-certificates-table-lib-table-menu-button-webserver.
    try:
        kebab_button = wait.until(
            EC.element_to_be_clickable((By.ID, "app-local-certificates-table-lib-table-menu-button-webserver"))
        )
    except TimeoutException:
        dump_debug_artifacts(driver, "kebab_button_not_found")
        raise

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", kebab_button)
    try:
        kebab_button.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", kebab_button)

    log.info("Selecting 'Generate CSR | Import'...")
    # This app renders mat-menu items into a CDK overlay only once the menu is
    # opened, so we don't have a static id for this item -- match on the full
    # string value (contains(., ...), NOT text()) so nested <span> wrappers
    # around the label still match (this is what tripped up the login button
    # locator earlier).
    try:
        generate_csr_option = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//*[self::button or self::a][contains(., 'Generate CSR') and contains(., 'Import')]")
            )
        )
    except TimeoutException:
        dump_debug_artifacts(driver, "generate_csr_menu_item_not_found")
        raise
    try:
        generate_csr_option.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", generate_csr_option)

    log.info("Waiting for popup window/modal...")
    try:
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[@role='dialog' or contains(@class,'modal') or contains(@class,'mat-dialog')]")))
    except TimeoutException:
        dump_debug_artifacts(driver, "csr_modal_not_found")
        raise

    log.info("Selecting 'Generate signing request (CSR) excluding IP addresses'...")
    # Exact id confirmed from the real DOM. NOTE: the earlier generic
    # contains(.,...) match was landing on the *outer* container div that
    # wraps all three radio options (since it's the first ancestor in
    # document order whose full text includes this option's label) rather
    # than the actual clickable mat-radio-button -- which is why Submit
    # stayed disabled even though the click "succeeded" with no error.
    try:
        exclude_ip_option = wait.until(
            EC.element_to_be_clickable(
                (By.ID, "generate-csr-local-certificates-actions-generate-self-signed-checkbox-radio-1")
            )
        )
    except TimeoutException:
        dump_debug_artifacts(driver, "exclude_ip_option_not_found")
        raise
    try:
        exclude_ip_option.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", exclude_ip_option)

    # Confirm the radio actually got selected before moving on -- if it
    # didn't, Submit will just silently stay disabled and the wait below
    # will correctly time out with a clear debug dump instead of clicking a
    # disabled button. (Fetch the WebElement explicitly first: this
    # Selenium version's element_selection_state_to_be expects an actual
    # element, not a locator tuple.)
    radio_input = driver.find_element(
        By.ID, "generate-csr-local-certificates-actions-generate-self-signed-checkbox-radio-1-input"
    )
    try:
        wait.until(EC.element_selection_state_to_be(radio_input, True))
    except TimeoutException:
        dump_debug_artifacts(driver, "radio_not_selected")
        raise
    log.info("Radio option selected.")

    log.info("Clicking Submit...")
    # Exact id confirmed from the real DOM (the inner <button>, which is the
    # element that actually carries the disabled attribute -- its wrapping
    # <app-button-loader id="generate-csr-local-certificate-continue-btn">
    # is not itself clickable/disabled-aware).
    try:
        submit_button = wait.until(
            EC.element_to_be_clickable((By.ID, "generate-csr-local-certificate-continue-btn-button"))
        )
    except TimeoutException:
        dump_debug_artifacts(driver, "submit_button_not_found_or_disabled")
        raise
    try:
        submit_button.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", submit_button)

    log.info("CSR generation submitted.")


# --------------------------------------------------------------------------
# Local command execution (this script runs directly on the target host)
# --------------------------------------------------------------------------

def run_local_commands(commands: list, use_shell: bool = True, use_sudo: bool = False):
    """
    Run a list of commands natively on this machine, sequentially, logging
    stdout/stderr/exit status for each. Stops and raises on the first
    non-zero exit status.

    Each entry in `commands` can be:
      - a string, e.g. "systemctl restart nginx"  (requires use_shell=True
        if it needs pipes, &&, redirects, etc.)
      - a list of args, e.g. ["systemctl", "restart", "nginx"]
    """
    for cmd in commands:
        display_cmd = cmd if isinstance(cmd, str) else " ".join(shlex.quote(c) for c in cmd)

        if use_sudo:
            if isinstance(cmd, str):
                cmd = f"sudo {cmd}"
            else:
                cmd = ["sudo", *cmd]
            display_cmd = f"sudo {display_cmd}"

        log.info("Running local command: %s", display_cmd)

        result = subprocess.run(
            cmd,
            shell=use_shell if isinstance(cmd, str) else False,
            capture_output=True,
            text=True,
        )

        if result.stdout.strip():
            log.info("STDOUT:\n%s", result.stdout.strip())
        if result.stderr.strip():
            log.warning("STDERR:\n%s", result.stderr.strip())
        log.info("Exit status: %s", result.returncode)

        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)


# --------------------------------------------------------------------------
# Debug helper
# --------------------------------------------------------------------------

def dump_debug_artifacts(driver, stage: str):
    """
    Save a screenshot + full page source when a step fails, so you can see
    exactly what the browser was looking at and fix selectors against the
    real DOM instead of guessing.
    """
    ts = time.strftime("%Y%m%d-%H%M%S")
    screenshot_path = f"/tmp/debug_{stage}_{ts}.png"
    html_path = f"/tmp/debug_{stage}_{ts}.html"
    try:
        driver.save_screenshot(screenshot_path)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        log.error("Saved debug screenshot to %s", screenshot_path)
        log.error("Saved debug page source to %s", html_path)
        log.error("Current URL at failure: %s", driver.current_url)
    except Exception as dump_err:  # don't let debug-dumping itself hide the real error
        log.error("Could not save debug artifacts: %s", dump_err)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    with get_driver() as driver:  # auto-detects headless vs. real display; see get_driver() docstring
        try:
            login(driver, BASE_URL, WEB_USERNAME, WEB_PASSWORD)
        except (TimeoutException, NoSuchElementException) as e:
            log.error("Failed during LOGIN step: %s", e or "(timed out waiting for an element)")
            dump_debug_artifacts(driver, "login")
            sys.exit(1)

        try:
            navigate_to_certificate_page(driver)
        except (TimeoutException, NoSuchElementException) as e:
            log.error("Failed during NAVIGATE TO CERTIFICATE PAGE step: %s", e or "(timed out waiting for an element)")
            dump_debug_artifacts(driver, "navigate")
            sys.exit(1)

        try:
            generate_csr_for_web_server(driver)
        except (TimeoutException, NoSuchElementException) as e:
            log.error("Failed during GENERATE CSR step: %s", e or "(timed out waiting for an element)")
            dump_debug_artifacts(driver, "generate_csr")
            sys.exit(1)

        log.info("Waiting %s seconds before running local commands...", POST_SUBMIT_WAIT)
        time.sleep(POST_SUBMIT_WAIT)

    # Driver is closed at this point (context manager exited); local commands
    # run after, directly on this host.
    try:
        run_local_commands(LOCAL_COMMANDS, use_shell=USE_SHELL, use_sudo=RUN_WITH_SUDO)
    except subprocess.CalledProcessError as e:
        log.error("Command failed with exit code %s: %s", e.returncode, e.cmd)
        sys.exit(1)


if __name__ == "__main__":
    main()
