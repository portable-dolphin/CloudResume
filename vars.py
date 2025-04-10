import os
import re

from benedict import benedict
from pathlib import Path

required_env_vars = [
    "APP_DEPLOY_ACCOUNT",
    "APP_DEPLOY_ENV",
    "APP_STACK_PREFIX",
    "APP_DEV_BLOG_URL",
    "APP_DNS_ZONE_DOMAIN",
    "APP_DNS_ZONE_ACCOUNT",
    "APP_DNS_HOSTED_ZONE_ID",
    "APP_COGNITO_LOGIN_NOTIFICATION_EMAIL",
    "APP_COGNITO_INITIAL_USERNAME",
    "APP_COGNITO_INITIAL_USER_GIVEN_NAME",
    "APP_COGNITO_INITIAL_USER_EMAIL",
    "APP_COGNITO_INITIAL_USER_PASSWORD",
    "APP_MONITORING_EMAIL_LIST",
    "APP_HOMEPAGE_TITLE",
]
required_prod_env_vars = []
required_test_env_vars = ["APP_TEST_DNS_HOST"]

# From https://emailregex.com
email_address_regex = re.compile(
    r"""(?:[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*|"(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21\x23-\x5b\x5d-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])*")@(?:(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?|\[(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?|[a-z0-9-]*[a-z0-9]:(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21-\x5a\x53-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])+)\])"""
)


class _env_vars:
    def __init__(self):
        self.env = benedict({})

    def get_env_vars(self) -> list:
        check_env_vars()
        for var in required_env_vars:
            self.env[var] = os.environ[var]

        if os.environ["APP_DEPLOY_ENV"] == "PROD":
            for var in required_prod_env_vars:
                self.env[var] = os.environ[var]

        if os.environ["APP_DEPLOY_ENV"] == "TEST":
            for var in required_test_env_vars:
                self.env[var] = os.environ[var]

        self.env["APP_LAMBDA_FUNCTION_INCREMENT"] = (
            os.environ["APP_LAMBDA_FUNCTION_INCREMENT"] if "APP_LAMBDA_FUNCTION_INCREMENT" in os.environ.keys() else ""
        )

        return self.env


def check_env_vars():
    missing_env_vars = [var for var in required_env_vars if var not in os.environ.keys() or os.environ[var] == ""]

    if missing_env_vars:
        raise ValueError(
            f'ERROR: the following environment variables must be defined and not empty: {", ".join(missing_env_vars)}'
        )

    valid_deploy_types = ["PROD", "TEST"]

    if os.environ["APP_DEPLOY_ENV"] not in valid_deploy_types:
        raise ValueError(f'ERROR: The environment variable DEPLOY_TYPE must be one of {", ".join(valid_deploy_types)}')

    if not email_address_regex.match(os.environ["APP_COGNITO_LOGIN_NOTIFICATION_EMAIL"]):
        raise ValueError(
            f'ERROR: the following email address is invalid {os.environ["APP_COGNITO_LOGIN_NOTIFICATION_EMAIL"]}'
        )

    if os.environ["APP_DEPLOY_ENV"] == "PROD":
        missing_prod_env_vars = [
            var for var in required_prod_env_vars if var not in os.environ.keys() or os.environ[var] == ""
        ]

        if missing_prod_env_vars:
            raise ValueError(
                f'ERROR: the following environment variables for a production deployment must be defined and not empty: {", ".join(missing_prod_env_vars)}'
            )

    if os.environ["APP_DEPLOY_ENV"] == "TEST":
        missing_test_env_vars = [
            var for var in required_test_env_vars if var not in os.environ.keys() or os.environ[var] == ""
        ]

        if missing_test_env_vars:
            raise ValueError(
                f'ERROR: the following environment variables for a test deployment must be defined and not empty: {", ".join(missing_test_env_vars)}'
            )


root_dir = Path(__file__).parent
env = _env_vars().get_env_vars()
