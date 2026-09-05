This script allows for easier automatic certificate renewal of the Eaton M3 Network Card using a Python script and Selenium.

Make sure to replace the following values as needed within the script:

BASE_URL = "https://eaton-url.com"   # REPLACE - This is the base URL for the Network Card.

WEB_USERNAME = "USERNAME_HERE"       # REPLACE - Username of a user with Certificate management privileges or admin privileges.

WEB_PASSWORD = "PASSWORD_GOES_HERE"  # REPLACE - Password of the aforementioned user.

CHROMEDRIVER_PATH = "/usr/bin/chromedriver"    # Adjust as needed (or "" to auto-resolve)
CHROME_BINARY_PATH = ""                        # Adjust as needed, e.g. "/usr/bin/chromium-browser"

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

DEFAULT_WAIT = 20  # seconds, for explicit waits on element visibility/clickability
POST_SUBMIT_WAIT = 20  # seconds, the required wait after clicking Submit
