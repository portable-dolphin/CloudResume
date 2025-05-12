#!/bin/env -S python3 -m pytest
import os
import platform
import pytest
import re
import requests
import sys
import tempfile
import warnings
import xdist

from benedict import benedict
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path
from random import randint, randrange, sample
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchAttributeException,
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
    ElementClickInterceptedException,
)
from selenium.webdriver import ActionChains, Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.firefox_profile import FirefoxProfile
from selenium.webdriver.support.expected_conditions import (
    element_to_be_clickable,
    none_of,
    presence_of_all_elements_located,
    presence_of_element_located,
    staleness_of,
)
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support.ui import Select
from shutil import which
from time import sleep
from traceback import format_exc
from urllib3.exceptions import ReadTimeoutError

from .install_browsers import check_if_browser_installed

required_env_vars = [
    "APP_DEPLOY_ENV",
    "APP_DNS_ZONE_DOMAIN",
    "APP_DEV_BLOG_URL",
    "APP_COGNITO_INITIAL_USER_EMAIL",
    "APP_COGNITO_INITIAL_USER_PASSWORD",
    "APP_COGNITO_INITIAL_USER_GIVEN_NAME",
    "APP_INITIAL_RESUME_DOCX_URL",
    "APP_INITIAL_RESUME_SOURCE_CODE_URL",
    "APP_TEST_RESUME_DOCX_URL",
    "APP_TEST_RESUME_SOURCE_CODE_URL",
    "APP_TEST_MODIFIED_RESUME_DOCX_URL",
    "APP_TEST_MODIFIED_RESUME_SOURCE_CODE_URL",
    "APP_HOMEPAGE_SOURCE_CODE_URL",
]
test_host_env_var = "APP_TEST_DNS_HOST"

missing_env_vars = [var for var in required_env_vars if var not in os.environ.keys() or os.environ[var] == ""]
if missing_env_vars:
    raise ValueError(
        f'ERROR: the following environment variables must be defined and not empty: {", ".join(missing_env_vars)}'
    )

dns_test_host = f"{os.environ[test_host_env_var]}." if test_host_env_var in os.environ.keys() else ""

global test_vars
test_vars = benedict(
    {
        "deploy_env": os.environ["APP_DEPLOY_ENV"],
        "base_domain": f"{dns_test_host}{os.environ['APP_DNS_ZONE_DOMAIN']}",
        "dev_blog": os.environ["APP_DEV_BLOG_URL"],
        "manager_user": os.environ["APP_COGNITO_INITIAL_USER_EMAIL"],
        "manager_password": os.environ["APP_COGNITO_INITIAL_USER_PASSWORD"],
        "manager_given_name": os.environ["APP_COGNITO_INITIAL_USER_GIVEN_NAME"],
        "driver_wait_timeout": 5,
        "driver_wait_poll": 0.2,
        "any_url": "",
        "valid_id_characters": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-",
        "invalid_id_characters_list":
        # A selection of characters based on HTML4's allowed URL character listing
        (r" ?!@$^*=,'\":;<>\\/%#()[]{}|`~àèìòùÀÈÌÒÙáéíóúýÁÉÍÓÚÝâêîôûÂÊÎÔÛãñõÃÑÕäëïöüÿÄËÏÖÜŸåÅæÆœŒçÇðÐøØß¡¿"),
        "invalid_id_characters_regex": re.compile(r"[^-a-zA-Z0-9_]"),
        "ids_in_use": {},
        "date_created_regex": r"\d{4}-\d{2}-\d{2} - \d{2}:\d{2}",
        "whitespace_regex": re.compile(r" *(\n|\r|\n\r|\r\n) *"),
        "skip_browser_text": "--no-{}",
        "row_class": "table-row",
        "row_id_extractor_regex": re.compile("^row-(.*)"),
        "row_id": "row-{}",
        "row_ids": {
            "id": "row-{}-id",
            "company": "row-{}-company",
            "job_title": "row-{}-job-title",
            "job_posting": "row-{}-job-posting",
            "job_posting_a": "row-{}-job-posting-a",
            "resume_url": "row-{}-resume-url",
            "resume_url_a": "row-{}-resume-url-a",
            "date_created": "row-{}-date-created",
            "views": "row-{}-views",
        },
        "row_static_text": {
            "job_posting": "Job Posting",
            "resume_url_parse_pending": "RESUME_PARSE_PENDING",
            "resume_url_invalidation_pending": "CACHE_INVALIDATION_PENDING",
            "resume_url_success": "Resume Posting",
            "manager_loading_message": "Loading Resumes...",
            "manager_refreshing_message": "Refreshing Resumes...",
        },
        "resume_dom_view_counter_class": "view-counter-box",
        "homepage_general_resume_a_id": "general-resume-a",
        "input_pending_statuses": [
            "Submitting, please wait...",
            "Updating, please wait...",
        ],
        "permanently_delete_resume_text": {
            "confirm_title": "Permanently Delete Resume",
            "confirm_paragraph": "Are you sure you want to permanently delete the selected resume?",
        },
    },
    keyattr_dynamic=True,
)

test_vars.urls = {
    "initial_resume_docx_url": os.environ["APP_INITIAL_RESUME_DOCX_URL"],
    "initial_resume_source_code_url": os.environ["APP_INITIAL_RESUME_SOURCE_CODE_URL"],
    "test_resume_docx_url": os.environ["APP_TEST_RESUME_DOCX_URL"],
    "test_resume_source_code_url": os.environ["APP_TEST_RESUME_SOURCE_CODE_URL"],
    "test_resume_modified_docx_url": os.environ["APP_TEST_MODIFIED_RESUME_DOCX_URL"],
    "test_resume_modified_source_code_url": os.environ["APP_TEST_MODIFIED_RESUME_SOURCE_CODE_URL"],
    "homepage_source_url": os.environ["APP_HOMEPAGE_SOURCE_CODE_URL"],
}

if "PYTEST_DEBUG_PRINT" in os.environ.keys():
    test_vars.debug = True

test_vars.urls.base_url = f"https://www.{test_vars.base_domain}"
test_vars.urls.manager_urls = {
    "login_redirect": f"{test_vars.urls.base_url}/management-zone/login-redirect.html",
    "login_verify": f"{test_vars.urls.base_url}/management-zone/login-verify.html",
    "manager": f"{test_vars.urls.base_url}/management-zone/manager/index.html",
}
test_vars.urls.login_urls = {
    "base": f"https://app-management-zone.{test_vars.base_domain}/login",
    "error_base": f"https://app-management-zone.{test_vars.base_domain}/error",
}
test_vars.urls.resume_urls = {
    "base_resume_url": f"{test_vars.urls.base_url}/resumes",
}
test_vars.urls.resume_urls.default_resume = f"{test_vars.urls.resume_urls.base_resume_url}/resume.html"
test_vars.urls.error_pages = [
    f"{test_vars.urls.base_url}/404.html",
    f"{test_vars.urls.base_url}/management-zone/403.html",
    f"{test_vars.urlsbase_url}/management-zone/401.html",
    f"{test_vars.urls.base_url}/management-zone/error.html",
]

test_vars.initial_resume = {
    "id": "resume",
    "company": "None",
    "job_title": "None",
    "job_posting": test_vars.urls.base_url,
}

test_vars.login_dom_elements = benedict(
    {
        "username_textbox": "signInFormUsername",
        "password_textbox": "signInFormPassword",
        "submit_button": "submitButton-customizable",
        "login_error": "loginErrorMessage",
    },
    keyattr_dynamic=True,
)

test_vars.manager_dom_elements = benedict(
    {
        "overlay": "overlay",
        "welcome_text": "welcome-h2",
        "table_body": "table-body",
        "add_resume_selector": "add-resume-selector",
        "modify_resume_selector": "modify-resume-selector",
        "delete_resume": "delete-resume-action",
        "undelete_resume": "undelete-resume-action",
        "permanently_delete_resume": "permanently-delete-resume-action",
        "refresh_resume": "refresh-resume-action",
        "refresh_all_resumes": "refresh-all-resumes-action",
        "view_deleted_resumes": "view-deleted-resumes-selector",
        "view_active_resumes": "view-active-resumes-selector",
        "add_modify_item_div": "modify-add-item-outer",
        "resume_id_textbox": "resume-id-textbox",
        "company_textbox": "company-textbox",
        "job_title_textbox": "job-title-textbox",
        "job_posting_textbox": "job-posting-textbox",
        "resume_file_textbox": "resume-file-textbox",
        "select_resume_file_input": "select-resume-file-input",
        "select_resume_file": "select-resume-file-action",
        "clear_selected_resume_file_action": "clear-selected-resume-file-action",
        "add_resume": "add-resume-action",
        "modify_resume": "modify-resume-action",
        "cancel_add_modify_resume": "cancel-add-modify-resume-action",
        "confirm_title": "confirm-title",
        "confirm_paragraph": "confirm-paragraph",
        "confirm_yes": "confirm-yes-action",
        "confirm_no": "confirm-no-action",
        "page_status": "status-div",
        "input_status": "inputs-info-paragraph",
    },
    keyattr_dynamic=True,
)
test_vars.resume_dom_elements = benedict(
    {
        "view_counter_hundred_thousands": "view-counter-hundred-thousands",
        "view_counter_ten_thousands": "view-counter-ten-thousands",
        "view_counter_thousands": "view-counter-thousands",
        "view_counter_hundreds": "view-counter-hundreds",
        "view_counter_tens": "view-counter-tens",
        "view_counter_ones": "view-counter-ones",
        "footer_section": "footer-section",
    },
    keyattr_dynamic=True,
)

test_vars.test_inputs = {
    "drivers": {
        "chrome": "chrome",
        "edge": "edge",
        "firefox": "firefox",
        "safari": "safari",
        "default": "safari" if platform.system() == "Darwin" else "chrome",
    },
    "resume_id": {
        "VALID": "valid_resume_id",
        "INVALID": "invalid_resume_id",
        "EMPTY": "empty_resume_id",
        "PROD": "prod_resume_id",
    },
    "company": {"VALID": "valid_company", "EMPTY": "empty_company", "PROD": "prod_company"},
    "job_title": {"VALID": "valid_job_title", "EMPTY": "empty_job_title", "PROD": "prod_job_title"},
    "job_posting": {
        "VALID": "valid_job_posting",
        "INVALID": "invalid_job_posting",
        "EMPTY": "empty_job_posting",
        "PROD": "prod_job_posting",
    },
    "test_resume_path": {
        "GOOD": "good_test_resume_path",
        "BAD": "bad_test_resume_path",
        "EMPTY": "empty_test_resume_path",
    },
    "keep_resume": {"KEEP": "keep_resume", "DESTROY": "destroy_resume", "KEEP_IF_PROD": "keep_if_prod"},
    "cancel": {"CANCEL": "cancel", "PROCEED": "proceed"},
}


test_outcome_success = "SUCCESSS"
test_outcome_action_unavailable = "ACTION_UNAVAILABLE"
test_outcome_id_input_error = "ID_INPUT_ERROR"
test_outcome_id_already_exists = "ID_ALREADY_EXISTS"
test_outcome_job_posting_input_error = "JOB_POSTING_INPUT_ERROR"
test_outcome_modify_no_changes_error = "MODIFY_NO_CHANGES_ERROR"
test_outcome_resume_parse_error = "RESUME_PARSE_ERROR"
test_outcome_api_error = "API_ERROR"
test_outcome_element_not_found = "ELEMENT_NOT_FOUND"
test_outcome_element_not_interactable = "ELEMENT_NOT_INTERACTABLE"
test_outcome_row_not_found = "ROW_NOT_FOUND"
test_outcome_row_already_exists = "ROW_ALREADY_EXISTS"
test_outcome_row_contains_bad_data = "ROW_CONTAINS_BAD_DATA"
test_outcome_manager_redirect_url_error = "MANAGER_REDIRECT_URL_ERROR"
test_outcome_resume_url_error = "RESUME_URL_ERROR"
test_outcome_url_timeout = "URL_TIMEOUT"
test_outcome_page_load_timeout = "PAGE_LOAD_TIMEOUT"
test_outcome_refresh_timeout = "REFRESH_TIMEOUT"
test_outcome_uncategorized_error = "UNCATEGORIZED_ERROR"
test_outcome_input_status_pending = "INPUT_STATUS_PENDING"
test_outcome_resume_upload_status_pending = "RESUME_UPLOAD_STATUS_PENDING"

test_vars.error_messages = {
    "input_status": {
        test_outcome_id_already_exists: "Resume appears to already exist.",
        test_outcome_id_input_error: "ID contains the following invalid characters:",
        test_outcome_job_posting_input_error: 'Job posting is invalid. It must begin with "https://"',
        test_outcome_modify_no_changes_error: "The company, job title, job posting, and resume file were not changed.",
    },
    "resume_upload_status": {
        test_outcome_resume_parse_error: "handler_docx_conversion_error",
    },
}

test_vars.test_drivers = {
    test_vars.test_inputs.drivers.chrome: {"os": ("Linux",)},
    test_vars.test_inputs.drivers.edge: {"os": ("Linux",)},
    test_vars.test_inputs.drivers.firefox: {"os": ("Linux",)},
    test_vars.test_inputs.drivers.safari: {"os": ("Darwin",)},
}


class TestResumeApp:
    @pytest.fixture(scope="session")
    def resume_test_html(self):
        return self.get_url_contents(f"{test_vars.urls.test_resume_source_code_url}", decode=True)

    @pytest.fixture(scope="session")
    def resume_test_modified_html(self):
        return self.get_url_contents(f"{test_vars.urls.test_resume_modified_source_code_url}", decode=True)

    @pytest.fixture(scope="session")
    def resume_initial_html(self):
        return self.get_url_contents(f"{test_vars.urls.initial_resume_source_code_url}", decode=True)

    @pytest.fixture(scope="session")
    def homepage_source_html(self):
        return self.get_url_contents(f"{test_vars.urls.homepage_source_url}", decode=True)

    @pytest.fixture(scope="session")
    def resume_test_docx(self, tmp_path_factory):
        resume = self.get_url_contents(f"{test_vars.urls.test_resume_docx_url}")
        resume_path = tmp_path_factory.mktemp("resume") / "resume_test.docx"
        with resume_path.open("wb") as f:
            f.write(resume)
        return resume_path.as_posix()

    @pytest.fixture(scope="session")
    def resume_test_modified_docx(self, tmp_path_factory):
        resume = self.get_url_contents(f"{test_vars.urls.test_resume_modified_docx_url}")
        resume_path = tmp_path_factory.mktemp("resume") / "resume_test_modified.docx"
        with resume_path.open("wb") as f:
            f.write(resume)
        return resume_path.as_posix()

    @pytest.fixture(scope="session")
    def resume_test_bad(self, tmp_path_factory):
        resume_path = tmp_path_factory.mktemp("resume") / "resume_bad.docx"
        contents = self.generate_random_string(alphanumeric_only=True, length=200)
        with resume_path.open("w") as f:
            f.write(contents)
        return resume_path.as_posix()

    @pytest.fixture(scope="session")
    def resume_initial_docx_path(self, tmp_path_factory):
        resume = self.get_url_contents(f"{test_vars.urls.initial_resume_docx_url}")
        resume_path = tmp_path_factory.mktemp("resume") / "resume_initial.docx"
        with resume_path.open("wb") as f:
            f.write(resume)
        return resume_path.as_posix()

    @pytest.fixture(scope="session")
    def session_driver(self):
        driver = self.get_driver(test_vars.test_inputs.drivers.default)
        self.manager_action_login(driver)

        yield driver

        driver.delete_all_cookies()
        driver.close()

    @pytest.fixture
    def resume_test_docx_path(self, request, resume_test_docx, resume_test_bad):
        if request.param and request.param not in test_vars.test_inputs.test_resume_path.values():
            raise ValueError("resume_test_docx_path is invalud - use test_vars.test_inputs.test_resume_path.[key]}")
        path = None
        if request.param == test_vars.test_inputs.test_resume_path.GOOD or not request.param:
            path = resume_test_docx
        elif request.param == test_vars.test_inputs.test_resume_path.BAD:
            path = resume_test_bad
        return path

    @pytest.fixture
    def resume_test_modified_docx_path(self, request, resume_test_modified_docx, resume_test_bad):
        if request.param and request.param not in test_vars.test_inputs.test_resume_path.values():
            raise ValueError("resume_test_docx_path is invalud - use test_vars.test_inputs.test_resume_path.[key]}")
        path = None
        if request.param == test_vars.test_inputs.test_resume_path.GOOD or not request.param:
            path = resume_test_modified_docx
        elif request.param == test_vars.test_inputs.test_resume_path.BAD:
            path = resume_test_bad
        return path

    @pytest.fixture
    def driver(self, request):
        try:
            browser_name = request.param
        except AttributeError:
            browser_name = test_vars.test_inputs.drivers.default
        driver = self.get_driver(browser_name)

        yield driver

        driver.delete_all_cookies()
        driver.quit()

    @pytest.fixture
    def dual_drivers(self, request):
        if not isinstance(request.param, tuple):
            raise RuntimeError(
                "dual_drivers argument must be a tuple with two items via test_vars.test_inputs.company.[key]"
            )
        if len(request.param) != 2:
            raise RuntimeError("dual_drivers argument must contain two items")

        driver_1 = request.param[0]
        driver_2 = request.param[1]

        drivers = (self.get_driver(driver_1), self.get_driver(driver_2))

        yield drivers

        for driver in drivers:
            driver.delete_all_cookies()
            driver.quit()

    @pytest.fixture
    def resume_id(self, request, driver) -> str:
        if not (
            isinstance(request.param, tuple)
            and len(request.param) == 2
            and request.param[1] in test_vars.test_inputs.keep_resume.values()
        ):
            raise RuntimeError(
                f"resume_id argument is invalid. Expected: tuple(test_vars.test_inputs.resume_id.[key], test_vars.test_inputs.keep_resume.[key]) - Got: {request.param}"
            )

        (id_type, keep_resume) = request.param
        resume_id = self.generate_resume_id(id_type, keep_resume)
        yield resume_id

        if (
            keep_resume == test_vars.test_inputs.keep_resume.DESTROY
            or (keep_resume == test_vars.test_inputs.keep_resume.KEEP_IF_PROD and test_vars.deploy_env != "PROD")
        ) and not len(test_vars.invalid_id_characters_regex.findall(resume_id)):
            deleted_resume = False
            medium_wait = self.get_driver_waiter(driver, timeout=5)
            wait = self.get_driver_waiter(driver)
            row_id = test_vars.row_id.format(resume_id)

            self.manager_action_login(driver)

            self.manager_action_refresh_all_resumes(driver)

            if self.manager_check_if_row_exists(driver, row_id):
                self.manager_action_delete_resume(driver, resume_id)
                deleted_resume = True

            self.manager_action_view_deleted_resumes(driver)
            try:
                medium_wait.until(lambda _: self.manager_check_if_row_exists(driver, row_id))
                self.manager_action_view_active_resumes(driver)
                try:
                    self.manager_action_permanently_delete_resume(
                        driver, resume_id, test_vars.test_inputs.cancel.PROCEED
                    )
                except AssertionError:
                    driver.get(test_vars.urls.manager_urls.manager)
                    try:
                        wait.until(lambda _: self.manager_check_if_page_loaded(driver))
                    except TimeoutException:
                        assert test_outcome_url_timeout is None
                    row_exists = False
                    if self.manager_check_if_row_exists(driver, row_id):
                        row_exists = True
                    else:
                        self.manager_action_view_deleted_resumes(driver)
                        if self.manager_check_if_row_exists(driver, row_id):
                            row_exists = True
                            self.manager_action_view_active_resumes(driver)
                    if row_exists:
                        self.manager_action_permanently_delete_resume(
                            driver, resume_id, test_vars.test_inputs.cancel.PROCEED
                        )

            except TimeoutException:
                if deleted_resume:
                    assert test_outcome_row_not_found is None

    @pytest.fixture
    def company(self, request) -> str:
        return self.generate_company(request.param)

    @pytest.fixture
    def job_title(self, request) -> str:
        return self.generate_job_title(request.param)

    @pytest.fixture
    def job_posting(self, request) -> str:
        return self.generate_job_posting(request.param)

    def generate_resume_id(self, id_type, keep_resume):
        if id_type not in test_vars.test_inputs.resume_id.values():
            raise ValueError(
                f"resume_id argument is invalid. Expected: test_vars.test_inputs.resume_id.[key] - Got: {id_type}"
            )
        if id_type == test_vars.test_inputs.resume_id.PROD:
            resume_id = test_vars.initial_resume.id
        else:
            resume_id = None
            resume_id_list = []
            if id_type != test_vars.test_inputs.resume_id.EMPTY:
                min_range = 1 if test_vars.test_inputs.resume_id.VALID else 20
                max_range = randint(min_range, 100)
                for _ in range(0, max_range):
                    resume_id_list.append(
                        test_vars.valid_id_characters[randrange(0, len(test_vars.valid_id_characters))]
                    )
                if id_type == test_vars.test_inputs.resume_id.INVALID:

                    def _get_replacement_indicies(valid_chars_length, invalid_chars_length, quantity_to_get):
                        valid_indicies = sample(range(0, valid_chars_length), quantity_to_get)
                        invalid_indicies = sample(range(0, invalid_chars_length), quantity_to_get)
                        for i in range(0, quantity_to_get):
                            yield (valid_indicies[i], invalid_indicies[i])

                    for x, y in _get_replacement_indicies(
                        max_range, len(test_vars.invalid_id_characters_list), min(max_range, 10)
                    ):
                        resume_id_list[x] = test_vars.invalid_id_characters_list[y]

            resume_id = "".join(resume_id_list)

        return resume_id

    def generate_company(self, company_type):
        if company_type not in test_vars.test_inputs.company.values():
            raise RuntimeError(f"job_title argument is invalid - use test_vars.test_inputs.company.[key]")

        if company_type == test_vars.test_inputs.company.PROD:
            company = test_vars.initial_resume.company
        else:
            company = ""
            if company_type == test_vars.test_inputs.company.VALID:
                company = self.generate_random_string(length=20)
        return company

    def generate_job_title(self, job_title_type):
        if job_title_type not in test_vars.test_inputs.job_title.values():
            raise RuntimeError(f"job_title argument is invalid - use test_vars.test_inputs.job_title.[key]")
        if job_title_type == test_vars.test_inputs.job_title.PROD:
            job_title = test_vars.initial_resume.job_title
        else:
            job_title = ""
            if job_title_type == test_vars.test_inputs.job_title.VALID:
                job_title = self.generate_random_string()
        return job_title

    def generate_job_posting(self, job_posting_type):
        if job_posting_type not in test_vars.test_inputs.job_posting.values():
            raise RuntimeError(f"job_posting argument is invalid - use test_vars.test_inputs.job_posting.[key]")

        if job_posting_type == test_vars.test_inputs.job_posting.PROD:
            job_posting = test_vars.initial_resume.job_posting
        else:
            job_posting = ""
            if job_posting_type != test_vars.test_inputs.job_posting.EMPTY:
                host = self.generate_random_string(alphanumeric_only=True, length=3)
                domain = f"{self.generate_random_string(alphanumeric_only=True, length=10)}.com"
                path = "/".join(
                    [
                        self.generate_random_string(alphanumeric_only=True, length=randint(5, 20))
                        for _ in range(3, randrange(4, 6))
                    ]
                )
                extension = self.generate_random_string(alphanumeric_only=True, length=3)
                job_posting = f"{host}.{domain}/{path}.{extension}"
                if job_posting_type == test_vars.test_inputs.job_posting.VALID:
                    job_posting = f"https://{job_posting}"
        return job_posting

    def generate_random_string(self, alphanumeric_only: bool = False, length: int = 60):
        alphanumeric_characters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        all_characters = (
            alphanumeric_characters
            + "?!@#$%^&*;\\/<>()-_=+,.'\"[]{}|`~"
            + "àèìòùÀÈÌÒÙáéíóúýÁÉÍÓÚÝâêîôûÂÊÎÔÛãñõÃÑÕäëïöüÿÄËÏÖÜŸåÅæÆœŒçÇðÐøØß¡¿"
        )
        string = ""
        for _ in range(0, length):
            characters = alphanumeric_characters if alphanumeric_only else all_characters
            string += characters[randrange(0, len(characters) - 1, 1)]
        return string

    def get_driver(self, driver_name):
        def _get_shadow_root(driver, element):
            return driver.execute_script("return arguments[0].shadowRoot", element)

        if driver_name not in test_vars.test_inputs.drivers.values():
            raise RuntimeError("Requested driver is invalid - use test_vars.test_inputs.drivers.[key]")
        if driver_name == "chrome":
            check = check_if_browser_installed(chrome=True)
            if not check:
                raise RuntimeError("Google Chrome browser is not installed!")
            options = webdriver.ChromeOptions()
            options.add_argument("--no-sandbox")
            options.add_argument("--start-maximized")
            options.add_argument("--headless=new")
            options.page_load_strategy = "normal"

            driver = webdriver.Chrome(options=options)
            wait = self.get_driver_waiter(driver, timeout=10)

            driver.get("chrome://settings/clearBrowserData")
            try:
                wait.until(lambda _: driver.execute_script("return document.readyState") == "complete")
            except TimeoutException:
                assert test_outcome_page_load_timeout is None
            sleep(2)
            settings_ui_shadow_root = _get_shadow_root(driver, driver.find_element(By.TAG_NAME, "settings-ui"))
            settings_main_shadow_root = _get_shadow_root(driver, settings_ui_shadow_root.find_element(By.ID, "main"))
            settings_basic_page_shadow_root = _get_shadow_root(
                driver, settings_main_shadow_root.find_element(By.CLASS_NAME, "cr-centered-card-container")
            )
            basic_page = settings_basic_page_shadow_root.find_element(By.ID, "basicPage")
            privacy_and_security = [
                section
                for section in basic_page.find_elements(By.CSS_SELECTOR, "*")
                if section.get_attribute("section") == "privacy"
            ][0]
            settings_privacy_page_shadow_root = _get_shadow_root(
                driver, privacy_and_security.find_element(By.CSS_SELECTOR, "*")
            )
            settings_clear_browsing_data_dialog = [
                section
                for section in settings_privacy_page_shadow_root.find_elements(By.CSS_SELECTOR, "*")
                if section.get_attribute("tagName").lower() == "settings-clear-browsing-data-dialog"
            ][0]
            settings_clear_browsing_data_dialog_shadow_root = _get_shadow_root(
                driver, settings_clear_browsing_data_dialog
            )
            clearBrowsingDataDialog = settings_clear_browsing_data_dialog_shadow_root.find_element(
                By.ID, "clearBrowsingDataDialog"
            )
            clearFromBasic_shadow_root = _get_shadow_root(
                driver, clearBrowsingDataDialog.find_element(By.ID, "clearFromBasic")
            )

            select = Select(clearFromBasic_shadow_root.find_element(By.ID, "dropdownMenu"))
            select.select_by_value("4")
            delete_data_button = clearBrowsingDataDialog.find_element(By.ID, "clearButton")
            delete_data_button.click()

        elif driver_name == "edge":
            check = check_if_browser_installed(edge=True)
            if not check:
                raise RuntimeError("Edge browser is not installed!")
            options = webdriver.EdgeOptions()
            options.add_argument("--no-sandbox")
            options.add_argument("--start-maximized")
            options.add_argument("--headless=new")
            options.page_load_strategy = "normal"

            driver = webdriver.Edge(options=options)
            driver.get("edge://settings/clearBrowserData")
            driver.find_element(By.ID, "clear-now").send_keys(Keys.ENTER)
        elif driver_name == "firefox":
            check = check_if_browser_installed(firefox=True)
            if not check:
                raise RuntimeError("Firefox browser is not installed!")

            driver_profile = webdriver.FirefoxProfile()
            driver_profile.set_preference("browser.cache.disk.enable", False)
            driver_profile.set_preference("browser.cache.memory.enable", False)
            driver_profile.set_preference("browser.cache.offline.enable", False)
            driver_profile.set_preference("network.http.use-cache", False)

            options = webdriver.FirefoxOptions()

            options.profile = driver_profile

            options.add_argument("--no-sandbox")
            options.add_argument("--start-maximized")
            options.add_argument("-headless")
            options.page_load_strategy = "normal"

            driver_service = webdriver.FirefoxService(executable_path=which("geckodriver"))

            driver = webdriver.Firefox(options=options, service=driver_service)
        elif driver_name == "safari":
            check = check_if_browser_installed(safari=True)
            if not check:
                raise RuntimeError("Safari browser is not installed!")
            options = webdriver.SafariOptions()
            options.page_load_strategy = "normal"

            driver = webdriver.Safari(options=options)
        else:
            self.print(f"Driver of type {driver_name} is not valid!")
            assert not driver_name is driver_name

        return driver

    def get_url_contents(self, url, decode=False):
        response = requests.get(url)
        if response.status_code > 299:
            raise RuntimeError(f"Could not fetch url. Code: {response.status_code} - Error: {response.text}")
        return response.content.decode("utf-8") if decode else response.content

    def print(self, message):
        warnings.warn(UserWarning(message))

    def get_driver_waiter(self, driver, timeout: int = test_vars.driver_wait_timeout, poll_frequency: int = None):
        return WebDriverWait(
            driver,
            timeout=timeout,
            poll_frequency=(test_vars.driver_wait_poll if poll_frequency is None else poll_frequency),
        )

    def assert_urls_equal(self, url_1, url_2, ignore_query_strings=False):
        url_parts = {
            "hypertext": 0,
            "host": 2,
            "path": 3,
        }
        if ignore_query_strings:
            url_1_parts = url_1.split("?")[0].rstrip("/").split("/")
            url_2_parts = url_2.split("?")[0].rstrip("/").split("/")
        else:
            url_1_parts = url_1.rstrip("/").split("/")
            url_2_parts = url_2.rstrip("/").split("/")

        try:
            url_1_parts[url_parts["hypertext"]] = url_1_parts[url_parts["hypertext"]].lower()
            url_2_parts[url_parts["hypertext"]] = url_2_parts[url_parts["hypertext"]].lower()

            url_1_parts[url_parts["host"]] = url_1_parts[url_parts["host"]].lower()
            url_2_parts[url_parts["host"]] = url_2_parts[url_parts["host"]].lower()
        except IndexError:
            pass

        assert "/".join(url_1_parts) == "/".join(url_2_parts)

    def wait_until_url_startswith(self, driver, wait, *url_redirects):
        def _check_urls():
            found = False
            for url in url_redirects:
                if driver.current_url.startswith(url):
                    found = True
            return found

        try:
            wait.until(lambda _: _check_urls())
        except TimeoutException:
            assert driver.current_url in url_redirects

    def wait_until_url_redirect(self, driver, wait, old_url):
        try:
            wait.until(lambda _: driver.current_url != old_url)
        except TimeoutException:
            assert old_url == driver.current_url

    def wait_until_element_found(self, wait, element_id):
        try:
            return wait.until(presence_of_element_located(element_id))
        except TimeoutException:
            assert element_id == None

    def manager_action_login(self, driver):
        wait = self.get_driver_waiter(driver, timeout=10)
        try:
            driver.implicitly_wait(10)
            try:
                driver.get(test_vars.urls.manager_urls.login_redirect)
            except ReadTimeoutError:
                sleep(3)
                driver.get(test_vars.urls.manager_urls.login_redirect)
            driver.implicitly_wait(0)
        except WebDriverException as e:
            self.print(", ".join(e.args))
            assert test_outcome_manager_redirect_url_error == e.msg

        self.wait_until_url_startswith(
            driver, wait, test_vars.urls.manager_urls.manager, test_vars.urls.login_urls.base
        )

        for error_page in test_vars.urls.error_pages:
            assert not driver.current_url.startswith(error_page)

        if driver.current_url.startswith(test_vars.urls.login_urls.base):
            try:
                username_inputs = wait.until(
                    presence_of_all_elements_located((By.ID, test_vars.login_dom_elements.username_textbox))
                )
            except TimeoutException:
                assert test_outcome_url_timeout is None
            try:
                password_inputs = wait.until(
                    presence_of_all_elements_located((By.ID, test_vars.login_dom_elements.password_textbox))
                )
            except TimeoutException:
                assert test_outcome_url_timeout is None
            try:
                signin_buttons = wait.until(
                    presence_of_all_elements_located((By.CLASS_NAME, test_vars.login_dom_elements.submit_button))
                )
            except TimeoutException:
                assert test_outcome_url_timeout is None

            try:
                wait.until(lambda _: len([form for form in username_inputs if form.is_displayed()]) != 0)
                wait.until(lambda _: len([form for form in password_inputs if form.is_displayed()]) != 0)
            except TimeoutException:
                assert test_outcome_url_timeout is None

            username_input = [form for form in username_inputs if form.is_displayed()][0]
            password_input = [form for form in password_inputs if form.is_displayed()][0]
            signin_button = [button for button in signin_buttons if button.is_displayed()][0]

            username_input.send_keys(test_vars.manager_user)
            password_input.send_keys(test_vars.manager_password)

            old_url = driver.current_url
            signin_button.click()

            try:
                self.wait_until_url_redirect(driver, wait, old_url)
            except AssertionError:
                error_ps = wait.until(
                    presence_of_all_elements_located((By.ID, test_vars.login_dom_elements.login_error))
                )
                if error_ps:
                    error_p = [p for p in username_inputs if p.is_displayed()][0]
                    assert error_p.text is False
                else:
                    assert old_url is False

            for error_page in test_vars.urls.error_pages:
                assert not driver.current_url.startswith(error_page)

        loop_check = []
        while True:
            old_url = driver.current_url
            loop_check.append(old_url)
            self.wait_until_url_redirect(driver, wait, old_url)
            if not (
                driver.current_url.startswith(test_vars.urls.manager_urls.login_verify)
                or driver.current_url == test_vars.urls.manager_urls.login_redirect
            ):
                break
            if len(loop_check) > 4:
                self.print(
                    f"It appears logging in to the manager caused a loop between"
                    + f"{test_vars.urls.manager_urls.login_redirect} and {test_vars.urls.manager_urls.login_verify}"
                )
                assert test_vars.urls.manager_urls.login_redirect == test_vars.urls.manager_urls.login_verify

        if driver.current_url == "about:blank":
            driver.implicitly_wait(10)
            try:
                driver.get(test_vars.urls.manager_urls.manager)
            except ReadTimeoutError:
                sleep(3)
                driver.get(test_vars.urls.manager_urls.manager)
            driver.implicitly_wait(0)
            try:
                wait.until(lambda _: self.manager_check_if_page_loaded(driver))
            except TimeoutException:
                assert test_outcome_url_timeout is None
        assert driver.current_url == test_vars.urls.manager_urls.manager
        try:
            wait.until(lambda _: self.manager_check_if_page_loaded(driver))
        except TimeoutException:
            assert test_outcome_page_load_timeout is None

    def manager_action_add_input(
        self,
        driver,
        resume_id=None,
        company=None,
        job_title=None,
        job_posting=None,
        resume_path=None,
        replace_text=False,
    ):
        def _set_textbox_text(element, text, replace_text):
            existing_text = element.get_attribute("value")
            element.click()
            if replace_text:
                element.send_keys(Keys.END)
                element.send_keys(Keys.BACKSPACE * len(existing_text))
                assert element.get_attribute("value") == ""
            element.send_keys(text)
            assert element.get_attribute("value") == (text if replace_text else existing_text + text)

        resume_id_textbox = self.manager_get_element(driver, test_vars.manager_dom_elements.resume_id_textbox)
        company_textbox = self.manager_get_element(driver, test_vars.manager_dom_elements.company_textbox)
        job_title_textbox = self.manager_get_element(driver, test_vars.manager_dom_elements.job_title_textbox)
        job_posting_textbox = self.manager_get_element(driver, test_vars.manager_dom_elements.job_posting_textbox)
        select_resume_file = self.manager_get_element(driver, test_vars.manager_dom_elements.select_resume_file)
        clear_selected_resume_file_action = self.manager_get_element(
            driver, test_vars.manager_dom_elements.clear_selected_resume_file_action
        )
        resume_file_textbox = self.manager_get_element(driver, test_vars.manager_dom_elements.resume_file_textbox)
        select_resume_file_input = self.manager_get_element(
            driver, test_vars.manager_dom_elements.select_resume_file_input
        )

        assert resume_id_textbox.is_displayed()
        if resume_id:
            assert resume_id_textbox.is_enabled()
        assert company_textbox.is_displayed()
        assert company_textbox.is_enabled()
        assert job_title_textbox.is_displayed()
        assert job_title_textbox.is_enabled()
        assert job_posting_textbox.is_displayed()
        assert job_posting_textbox.is_enabled()
        assert select_resume_file.is_displayed()
        assert select_resume_file.is_enabled()
        assert clear_selected_resume_file_action.is_displayed()
        assert clear_selected_resume_file_action.is_enabled()

        if not resume_id is None:
            _set_textbox_text(resume_id_textbox, resume_id, replace_text)
        if not company is None:
            _set_textbox_text(company_textbox, company, replace_text)
        if not job_title is None:
            _set_textbox_text(job_title_textbox, job_title, replace_text)
        if not job_posting is None:
            _set_textbox_text(job_posting_textbox, job_posting, replace_text)
        if not resume_path is None:
            resume_file_name = Path(resume_path).name
            select_resume_file_input.send_keys(resume_path)
            select_resume_file.click()
            assert resume_file_textbox.get_attribute("value") == resume_file_name

    def manager_action_select_row(self, driver, resume_id):
        def _firefox_scroll_to_location(x, y):
            driver.execute_script(
                f'let e = document.getElementById("{test_vars.manager_dom_elements.table_body}");'
                + f"e.scrollTo({x},{y});"
            )

        wait = self.get_driver_waiter(driver)
        row_id = test_vars.row_ids.id.format(resume_id)
        try:
            row_element = wait.until(presence_of_element_located((By.ID, row_id)))
        except TimeoutException:
            assert row_id == test_outcome_row_not_found
        if driver.capabilities["browserName"] == test_vars.test_inputs.drivers.firefox:
            _firefox_scroll_to_location(row_element.location["x"], row_element.location["y"])
        else:
            actions = ActionChains(driver, duration=1000)
            actions.move_to_element(row_element).perform()

        try:
            wait.until(element_to_be_clickable((By.ID, row_id)))
        except TimeoutException:
            assert test_outcome_element_not_interactable == None
        try:
            row_element.click()
        except ElementClickInterceptedException:
            if driver.capabilities["browserName"] == test_vars.test_inputs.drivers.firefox:
                rect = row_element.rect
                _firefox_scroll_to_location(rect["x"] + int(rect["width"] / 2), rect["y"] + rect["height"])
                row_element.click()
            else:
                raise

    def manager_action_deselect_row(self, driver):
        wait = self.get_driver_waiter(driver)
        try:
            overlay_element = wait.until(presence_of_element_located((By.ID, test_vars.manager_dom_elements.overlay)))
        except NoSuchElementException:
            assert test_outcome_element_not_found == None
        overlay_element.click()

    def manager_action_add_resume(
        self,
        driver,
        resume_id,
        company,
        job_title,
        job_posting,
        resume_path,
        resume_html,
        expected=test_outcome_success,
    ):
        check_status_wait = self.get_driver_waiter(driver, timeout=600, poll_frequency=2.0)
        short_wait = self.get_driver_waiter(driver, timeout=1)

        add_resume_selector = self.manager_get_element(driver, test_vars.manager_dom_elements.add_resume_selector)
        add_resume = self.manager_get_element(driver, test_vars.manager_dom_elements.add_resume)
        cancel_add_modify_resume = self.manager_get_element(
            driver, test_vars.manager_dom_elements.cancel_add_modify_resume
        )
        modify_resume = self.manager_get_element(driver, test_vars.manager_dom_elements.modify_resume)

        assert add_resume_selector.is_displayed()
        assert add_resume_selector.is_enabled()

        add_resume_selector.click()

        assert add_resume.is_displayed()
        assert not add_resume.is_enabled()
        assert cancel_add_modify_resume.is_displayed()
        assert cancel_add_modify_resume.is_enabled()
        assert not modify_resume.is_displayed()

        self.manager_action_add_input(
            driver=driver,
            resume_id=resume_id,
            company=company,
            job_title=job_title,
            job_posting=job_posting,
            resume_path=resume_path,
        )

        try:
            short_wait.until(element_to_be_clickable(add_resume))
            assert expected != test_outcome_action_unavailable
        except TimeoutException:
            assert expected == test_outcome_action_unavailable
            return

        add_resume.click()

        try:
            check_status_wait.until(
                lambda _: self.manager_check_status(driver=driver, expected=expected, resume_id=resume_id)
            )
        except TimeoutException:
            assert test_outcome_url_timeout is self.manager_check_status(
                driver=driver, expected=expected, resume_id=resume_id, return_outcome=True
            )

        status = self.manager_check_status(driver=driver, expected=expected, resume_id=resume_id, return_outcome=True)

        if expected in test_vars.error_messages.input_status.keys():
            input_status = self.manager_check_input_status(driver)
            assert input_status.startswith(test_vars.error_messages.input_status[expected])
            invalid_characters = re.compile('"(.)"').findall(input_status)
            assert len(invalid_characters) == len(
                [char for char in invalid_characters if test_vars.invalid_id_characters_regex.match(char)]
            )
            return
        elif expected == test_outcome_success:
            self.manager_validate_row_data(driver, resume_id, company, job_title, job_posting, resume_path, expected)
            self.resume_check_url(driver, resume_id, resume_html)
        else:
            try:
                assert expected == status
            except AssertionError:
                assert expected is test_outcome_uncategorized_error

    def manager_action_modify_resume(
        self, driver, resume_id, company, job_title, job_posting, resume_html, expected=test_outcome_success
    ):
        check_status_wait = self.get_driver_waiter(driver, timeout=25, poll_frequency=1.0)

        modify_resume_selector = self.manager_get_element(driver, test_vars.manager_dom_elements.modify_resume_selector)
        modify_resume = self.manager_get_element(driver, test_vars.manager_dom_elements.modify_resume)
        cancel_add_modify_resume = self.manager_get_element(
            driver, test_vars.manager_dom_elements.cancel_add_modify_resume
        )
        add_resume = self.manager_get_element(driver, test_vars.manager_dom_elements.add_resume)

        assert modify_resume_selector.is_displayed()
        self.manager_action_select_row(driver, resume_id)
        assert modify_resume_selector.is_enabled()

        modify_resume_selector.click()

        assert modify_resume.is_displayed()
        assert modify_resume.is_enabled()
        assert cancel_add_modify_resume.is_displayed()
        assert cancel_add_modify_resume.is_enabled()
        assert not add_resume.is_displayed()

        self.manager_action_add_input(
            driver=driver,
            company=company,
            job_title=job_title,
            job_posting=job_posting,
            replace_text=True,
        )

        if modify_resume.is_enabled():
            assert expected != test_outcome_action_unavailable
        else:
            assert expected == test_outcome_action_unavailable
            return

        modify_resume.click()

        try:
            check_status_wait.until(
                lambda _: self.manager_check_status(driver=driver, expected=expected, resume_id=resume_id)
            )
        except TimeoutException:
            assert test_outcome_url_timeout is self.manager_check_status(
                driver=driver, expected=expected, resume_id=resume_id, return_outcome=True
            )

        if expected in test_vars.error_messages.input_status.keys():
            input_status = self.manager_check_input_status(driver)
            assert input_status.startswith(test_vars.error_messages.input_status[expected])
            return

        self.manager_validate_row_data(driver, resume_id, company, job_title, job_posting, expected)

    def manager_action_reupload_resume(self, driver, resume_id, resume_path, resume_html, expected):
        check_status_wait = self.get_driver_waiter(driver, timeout=600, poll_frequency=1.0)
        medium_wait = self.get_driver_waiter(driver, timeout=5)

        modify_resume_selector = self.manager_get_element(driver, test_vars.manager_dom_elements.modify_resume_selector)
        modify_resume = self.manager_get_element(driver, test_vars.manager_dom_elements.modify_resume)
        cancel_add_modify_resume = self.manager_get_element(
            driver, test_vars.manager_dom_elements.cancel_add_modify_resume
        )
        add_resume = self.manager_get_element(driver, test_vars.manager_dom_elements.add_resume)

        assert modify_resume_selector.is_displayed()

        self.manager_action_select_row(driver, resume_id)

        assert modify_resume_selector.is_enabled()

        modify_resume_selector.click()

        assert modify_resume.is_displayed()
        assert modify_resume.is_enabled()
        assert cancel_add_modify_resume.is_displayed()
        assert cancel_add_modify_resume.is_enabled()
        assert not add_resume.is_displayed()

        self.manager_action_add_input(driver=driver, resume_path=resume_path)

        row_elements = self.manager_get_row_elements(driver, resume_id)

        modify_resume.click()

        try:
            medium_wait.until(lambda _: row_elements.resume_url.text != test_vars.row_static_text.resume_url_success)
        except TimeoutException:
            pass

        try:
            check_status_wait.until(
                lambda _: self.manager_check_status(driver=driver, expected=expected, resume_id=resume_id)
            )
        except TimeoutException:
            assert test_outcome_url_timeout is self.manager_check_status(
                driver=driver, expected=expected, resume_id=resume_id, return_outcome=True
            )

        if expected in test_vars.error_messages.input_status.keys():
            input_status = self.manager_check_input_status(driver)
            assert input_status.startswith(test_vars.error_messages.input_status[expected])
            return
        elif expected in test_vars.error_messages.resume_upload_status.keys():
            resume_upload_status = self.manager_get_resume_upload_status(driver, resume_id)
            assert resume_upload_status == test_vars.error_messages.resume_upload_status[expected]
            return
        elif expected != test_outcome_success:
            assert expected is test_outcome_uncategorized_error

        self.manager_validate_row_data(driver, resume_id, resume_path=resume_path, expected=expected)

        self.resume_check_url(driver, resume_id, resume_html, expect_match=(expected == test_outcome_success))

    def manager_action_refresh_resume(
        self,
        driver,
        resume_id,
        expected_company,
        expected_job_title,
        expected_job_posting,
        expected_resume_path,
        expected,
    ):
        wait = self.get_driver_waiter(driver, timeout=10)
        refresh_resume = self.manager_get_element(driver, test_vars.manager_dom_elements.refresh_resume)

        self.manager_action_select_row(driver, resume_id)

        assert refresh_resume.is_displayed()
        assert refresh_resume.is_enabled()

        self.manager_action_select_row(driver, resume_id)

        refresh_resume.click()

        try:
            wait.until(lambda _: self.manager_check_if_resume_refresh_complete(driver, resume_id))
        except TimeoutException:
            assert test_outcome_refresh_timeout is None

        self.manager_validate_row_data(
            driver,
            resume_id,
            expected_company,
            expected_job_title,
            expected_job_posting,
            expected_resume_path,
            expected=expected,
        )

    def manager_action_refresh_all_resumes(self, driver):
        wait = self.get_driver_waiter(driver, timeout=10)
        refresh_all_resumes = self.manager_get_element(driver, test_vars.manager_dom_elements.refresh_all_resumes)

        assert refresh_all_resumes.is_displayed()
        assert refresh_all_resumes.is_enabled()

        refresh_all_resumes.click()

        try:
            wait.until(lambda _: self.manager_check_if_refresh_all_resumes_done(driver))
        except TimeoutException:
            assert test_outcome_refresh_timeout is None

    def manager_action_view_deleted_resumes(self, driver):
        wait = self.get_driver_waiter(driver, timeout=10)
        short_wait = self.get_driver_waiter(driver, timeout=2.5)
        view_deleted_resumes = self.manager_get_element(driver, test_vars.manager_dom_elements.view_deleted_resumes)
        view_active_resumes = self.manager_get_element(driver, test_vars.manager_dom_elements.view_active_resumes)

        try:
            short_wait.until(lambda _: view_deleted_resumes.is_displayed())
        except TimeoutException:
            assert view_deleted_resumes.is_displayed()
        try:
            short_wait.until(lambda _: view_deleted_resumes.is_enabled())
        except TimeoutException:
            assert view_deleted_resumes.is_enabled()
        assert not view_active_resumes.is_displayed()

        view_deleted_resumes.click()

        try:
            wait.until(lambda _: self.manager_check_if_refresh_all_resumes_done(driver))
        except TimeoutException:
            assert test_outcome_refresh_timeout is None

        try:
            short_wait.until(lambda _: view_active_resumes.is_displayed())
        except TimeoutException:
            assert view_active_resumes.is_displayed()
        try:
            short_wait.until(lambda _: view_active_resumes.is_enabled())
        except TimeoutException:
            assert view_active_resumes.is_enabled()
        assert not view_deleted_resumes.is_displayed()

    def manager_action_view_active_resumes(self, driver):
        wait = self.get_driver_waiter(driver, timeout=10)
        short_wait = self.get_driver_waiter(driver, timeout=2.5)
        view_active_resumes = self.manager_get_element(driver, test_vars.manager_dom_elements.view_active_resumes)
        view_deleted_resumes = self.manager_get_element(driver, test_vars.manager_dom_elements.view_deleted_resumes)

        assert view_active_resumes.is_displayed()
        try:
            short_wait.until(lambda _: view_active_resumes.is_enabled())
        except TimeoutException:
            assert view_active_resumes.is_enabled()
        assert not view_deleted_resumes.is_displayed()

        view_active_resumes.click()

        try:
            short_wait.until(lambda _: view_deleted_resumes.is_displayed())
        except TimeoutException:
            assert view_deleted_resumes.is_displayed()
        try:
            short_wait.until(lambda _: view_deleted_resumes.is_enabled())
        except TimeoutException:
            assert view_deleted_resumes.is_enabled()
        assert not view_active_resumes.is_displayed()

        try:
            wait.until(lambda _: self.manager_check_if_refresh_all_resumes_done(driver))
        except TimeoutException:
            assert test_outcome_refresh_timeout is None

    def manager_action_delete_resume(self, driver, resume_id):
        wait = self.get_driver_waiter(driver, timeout=10)
        row_id = test_vars.row_id.format(resume_id)

        delete_resume = self.manager_get_element(driver, test_vars.manager_dom_elements.delete_resume)

        self.manager_action_select_row(driver, resume_id)

        assert delete_resume.is_displayed()
        assert delete_resume.is_enabled()

        delete_resume.click()

        try:
            wait.until(none_of(presence_of_element_located((By.ID, row_id))))
        except TimeoutException:
            assert test_outcome_url_timeout is None

        self.manager_action_view_deleted_resumes(driver)

        try:
            _ = driver.find_element(By.ID, row_id)
        except NoSuchElementException:
            test_outcome_row_not_found is None

        self.manager_action_view_active_resumes(driver)

    def manager_action_restore_resume(self, driver, resume_id):
        wait = self.get_driver_waiter(driver, timeout=10)
        self.manager_action_view_deleted_resumes(driver)
        row_id = test_vars.row_id.format(resume_id)

        undelete_resume = self.manager_get_element(driver, test_vars.manager_dom_elements.undelete_resume)

        self.manager_action_select_row(driver, resume_id)

        assert undelete_resume.is_displayed()
        assert undelete_resume.is_enabled()

        self.manager_action_select_row(driver, resume_id)

        undelete_resume.click()

        try:
            wait.until(none_of(presence_of_element_located((By.ID, row_id))))
        except TimeoutException:
            assert test_outcome_url_timeout is None

        self.manager_action_view_active_resumes(driver)

        try:
            _ = driver.find_element(By.ID, row_id)
        except NoSuchElementException:
            test_outcome_row_not_found is None

    def manager_action_permanently_delete_resume(self, driver, resume_id, cancel):
        wait = self.get_driver_waiter(driver)
        short_wait = self.get_driver_waiter(driver, timeout=1)
        row_id = test_vars.row_id.format(resume_id)

        try:
            short_wait.until(lambda _: self.manager_check_if_row_exists(driver, row_id))
            self.manager_action_delete_resume(driver, resume_id)
        except TimeoutException:
            pass

        self.manager_action_view_deleted_resumes(driver)

        permanently_delete_resume = self.manager_get_element(
            driver, test_vars.manager_dom_elements.permanently_delete_resume
        )
        confirm_title = self.manager_get_element(driver, test_vars.manager_dom_elements.confirm_title)
        confirm_paragraph = self.manager_get_element(driver, test_vars.manager_dom_elements.confirm_paragraph)
        confirm_yes = self.manager_get_element(driver, test_vars.manager_dom_elements.confirm_yes)
        confirm_no = self.manager_get_element(driver, test_vars.manager_dom_elements.confirm_no)

        self.manager_action_select_row(driver, resume_id)

        assert permanently_delete_resume.is_displayed()
        assert permanently_delete_resume.is_enabled()
        permanently_delete_resume.click()

        assert confirm_title.is_displayed()
        assert confirm_paragraph.is_displayed()
        assert confirm_yes.is_displayed()
        assert confirm_yes.is_enabled()
        assert confirm_no.is_displayed()
        assert confirm_no.is_enabled()
        assert confirm_title.text == test_vars.permanently_delete_resume_text.confirm_title
        assert confirm_paragraph.text == test_vars.permanently_delete_resume_text.confirm_paragraph

        if cancel == test_vars.test_inputs.cancel.PROCEED:
            confirm_yes.click()

            try:
                wait.until(none_of(presence_of_element_located((By.ID, row_id))))
            except TimeoutException:
                assert test_outcome_url_timeout is None

            row_expectation = test_outcome_row_not_found
        else:
            confirm_no.click()
            row_expectation = test_outcome_success

        self.manager_action_refresh_all_resumes(driver)
        try:
            _ = driver.find_element(By.ID, row_id)
            assert row_expectation == test_outcome_success
            self.manager_action_view_active_resumes(driver)
            self.manager_action_restore_resume(driver, resume_id)
        except NoSuchElementException:
            assert row_expectation == test_outcome_row_not_found
            self.manager_action_view_active_resumes(driver)

    def manager_action_reload_page(self, driver, hard_refresh: bool = False):
        wait = self.get_driver_waiter(driver, timeout=10)
        action = ActionChains(driver)
        if hard_refresh:
            action.key_down(Keys.CONTROL)
        action.send_keys("F5")
        if hard_refresh:
            action.key_up(Keys.CONTROL)
        action.perform()

        try:
            wait.until(self.manager_check_if_page_loaded(driver))
        except TimeoutException:
            assert test_outcome_page_load_timeout is None

    def manager_check_if_row_exists(self, driver, row_id) -> bool:
        try:
            _ = driver.find_element(By.ID, row_id)
            return True
        except NoSuchElementException:
            pass

        page_status = self.manager_check_page_status(driver)
        if page_status:
            self.print(page_status)
            assert test_outcome_api_error == None

        return False

    def manager_check_if_page_loaded(self, driver) -> bool:
        if not driver.execute_script("return document.readyState") == "complete":
            return False
        loading_resumes_id = self.manager_get_row_resume_id(test_vars.row_static_text.manager_loading_message)
        return not self.manager_check_if_row_exists(driver, loading_resumes_id)

    def manager_check_if_refresh_all_resumes_done(self, driver) -> bool:
        refreshing_resumes_id = self.manager_get_row_resume_id(test_vars.row_static_text.manager_refreshing_message)
        refresh_all_resumes_selector = driver.find_element(By.ID, test_vars.manager_dom_elements.refresh_all_resumes)
        return (
            not self.manager_check_if_row_exists(driver, refreshing_resumes_id)
            and refresh_all_resumes_selector.is_enabled()
        )

    def manager_check_if_resume_refresh_complete(self, driver, resume_id) -> bool:
        row_elements = self.manager_get_row_elements(driver, resume_id)
        if (
            row_elements.company.text
            == row_elements.job_title.text
            == row_elements.job_posting.text
            == row_elements.resume_url.text
            == row_elements.date_created.text
            == row_elements.views.text
            == ""
        ):
            return False
        else:
            return True

    def manager_check_resume_view_count(self, driver, resume_id) -> int:
        resume_views_id = test_vars.row_ids.views.format(resume_id)
        try:
            resume_views = driver.find_element(By.ID, resume_views_id)
            return resume_views
        except NoSuchElementException:
            assert resume_views_id is None

    def manager_check_page_status(self, driver) -> str:
        wait = self.get_driver_waiter(driver)
        try:
            page_status = wait.until(presence_of_element_located((By.ID, test_vars.manager_dom_elements.page_status)))
        except TimeoutException:
            assert test_outcome_element_not_found is None
        return page_status.text if page_status.is_displayed() and page_status.text else None

    def manager_check_input_status(self, driver) -> str:
        wait = self.get_driver_waiter(driver)
        try:
            input_status = wait.until(presence_of_element_located((By.ID, test_vars.manager_dom_elements.input_status)))
        except TimeoutException:
            assert test_outcome_element_not_found is driver.page_source
        return input_status.text if input_status.is_displayed() and input_status.text != "" else None

    def manager_check_status(self, driver, expected, resume_id: str = None, return_outcome=False) -> bool:
        outcome = None

        page_status = self.manager_check_page_status(driver)
        input_status = self.manager_check_input_status(driver)
        resume_upload_status = None

        # Page status outweighs all other errors
        if page_status:
            outcome = test_outcome_api_error
        # Input status errors means the resume will not be uploaded
        elif input_status:
            if input_status in test_vars.input_pending_statuses:
                outcome = test_outcome_input_status_pending
            else:
                error_message = [
                    k for k, v in test_vars.error_messages.input_status.items() if input_status.startswith(v)
                ]
                if len(error_message):
                    outcome = error_message[0]
                else:
                    outcome = test_outcome_uncategorized_error
        # Resume status will state whether the resume parse succeeded or failed
        elif resume_id and not input_status:
            resume_upload_status = self.manager_get_resume_upload_status(driver, resume_id)
            if resume_upload_status in [
                test_vars.row_static_text.resume_url_parse_pending,
                test_vars.row_static_text.resume_url_invalidation_pending,
            ]:
                outcome = test_outcome_resume_upload_status_pending
            elif resume_upload_status == test_vars.row_static_text.resume_url_success:
                outcome = test_outcome_success
            else:
                inverted_resume_errors = {v: k for (k, v) in test_vars.error_messages.resume_upload_status.items()}
                outcome = (
                    inverted_resume_errors[resume_upload_status]
                    if resume_upload_status in inverted_resume_errors.keys()
                    else test_outcome_uncategorized_error
                )

        if (
            outcome != test_outcome_input_status_pending
            and outcome != test_outcome_resume_upload_status_pending
            and not outcome is None
        ):
            if expected != outcome:
                if page_status:
                    self.print(f"API status error: {page_status}")
                if input_status:
                    self.print(f"Input status error: {input_status}")
                if resume_upload_status:
                    self.print(f"Resume upload status error: {resume_upload_status}")

            assert outcome == expected

        return outcome if return_outcome else outcome == expected

    def manager_check_resume_view_increase(self, driver, resume_id, no_increment=True):
        wait = self.get_driver_waiter(driver)
        resume_url = self.manager_get_resume_posting_url(driver, resume_id, no_increment)
        manager_url = test_vars.urls.manager_urls.manager
        driver.get(manager_url)
        try:
            wait.until(lambda _: self.manager_check_if_page_loaded(driver))
        except TimeoutException:
            assert test_outcome_url_timeout is None

        old_count = self.manager_get_row_view_count(driver, resume_id)
        _ = self.resume_get_view_count(driver, resume_url)

        driver.get(manager_url)
        try:
            wait.until(lambda _: self.manager_check_if_page_loaded(driver))
        except TimeoutException:
            assert test_outcome_url_timeout is None

        new_count = self.manager_get_row_view_count(driver, resume_id)

        if no_increment:
            assert old_count == new_count
        else:
            assert old_count == (new_count - 1)

    def manager_get_current_view(self, driver) -> str:
        view_active_resumes = self.manager_get_element(driver, test_vars.manager_dom_elements.view_active_resumes)
        view_deleted_resumes = self.manager_get_element(driver, test_vars.manager_dom_elements.view_deleted_resumes)

        if view_deleted_resumes.is_displayed():
            return "active"
        if view_active_resumes.is_displayed():
            return "deleted"
        assert test_outcome_uncategorized_error is None

    def manager_get_row_resume_id(self, resume_id) -> str:
        partial_id = resume_id.replace(" ", "_-_").replace(".", "dot")
        assert len(test_vars.invalid_id_characters_regex.findall(partial_id)) == 0
        return test_vars.row_ids.company.format(partial_id)

    def manager_get_row_view_count(self, driver, resume_id):
        row_views_id = test_vars.row_ids.views
        try:
            element = driver.find_element(By.ID, row_views_id.format(resume_id))
        except NoSuchElementException:
            assert test_outcome_row_not_found is None

        views = element.text
        if not views.isdigit():
            assert test_outcome_row_contains_bad_data is None
        return int(views)

    def manager_get_resume_upload_status(self, driver, resume_id) -> str:
        resume_url_a_id = test_vars.row_ids.resume_url_a.format(resume_id)
        resume_url_id = test_vars.row_ids.resume_url.format(resume_id)
        try:
            try:
                resume_url_status = driver.find_element(By.ID, resume_url_a_id)
            except NoSuchElementException:
                resume_url_status = driver.find_element(By.ID, resume_url_id)
        except NoSuchElementException:
            assert resume_url_id is test_outcome_element_not_found
        return resume_url_status.text

    def manager_get_element(self, driver, element_id):
        wait = self.get_driver_waiter(driver)
        if element_id not in test_vars.manager_dom_elements.values():
            raise RuntimeError(f"Manager element {element_id} is not valid")
        return self.wait_until_element_found(wait, (By.ID, element_id))

    def manager_get_row_elements(self, driver, resume_id):
        elements = benedict({}, keyattr_dynamic=True)
        elements_template = test_vars.row_ids.clone()

        for key, value in elements_template.items():
            try:
                elements[key] = driver.find_element(By.ID, value.format(resume_id))
            except NoSuchElementException:
                if key.endswith("_a"):
                    elements[key] = None
                else:
                    assert test_outcome_row_not_found == None

        return elements

    def manager_get_resume_posting_url(self, driver, resume_id, no_increment=True):
        if no_increment:
            resume_row_id = self.manager_get_row_resume_id(resume_id)
            current_view = self.manager_get_current_view(driver)
            row_exists = self.manager_check_if_row_exists(driver, resume_row_id)

            if not row_exists:
                current_view_func, other_view_func = (
                    (self.manager_action_view_active_resumes, self.manager_action_view_deleted_resumes)
                    if current_view == "active"
                    else (self.manager_action_view_deleted_resumes, self.manager_action_view_active_resumes)
                )

                other_view_func(driver)

            assert self.manager_check_if_row_exists(driver, resume_row_id)
            resume_url_a = driver.find_element(By.ID, test_vars.row_ids.resume_url_a.format(resume_id))
            resume_url = resume_url_a.get_attribute("href")

            if not row_exists:
                current_view_func(driver)
        else:
            resume_url = f"{test_vars.urls.resume_urls.base_resume_url}/{resume_id}.html"
        return resume_url

    def manager_validate_row_data(
        self,
        driver,
        resume_id,
        company="",
        job_title="",
        job_posting="",
        resume_path="",
        expected=test_outcome_success,
    ):
        if expected in test_vars.error_messages.resume_upload_status.keys():
            resume_upload_status = self.manager_get_resume_upload_status(driver, resume_id)
            assert resume_upload_status.startswith(test_vars.error_messages.resume_upload_status[expected])
            return

        row_elements = self.manager_get_row_elements(driver, resume_id)
        assert row_elements.id.text == resume_id
        if company:
            assert row_elements.company.text == company
        if job_title:
            assert row_elements.job_title.text == job_title
        if job_posting:
            assert row_elements.job_posting.text == test_vars.row_static_text.job_posting

            try:
                self.assert_urls_equal(row_elements.job_posting_a.get_attribute("href"), job_posting)
            except NoSuchAttributeException:
                assert job_posting == None
        if resume_path:
            assert row_elements.resume_url.text == test_vars.row_static_text.resume_url_success
            resume_url = self.manager_get_resume_posting_url(driver, resume_id)
            try:
                self.assert_urls_equal(
                    row_elements.resume_url_a.get_attribute("href"), resume_url, ignore_query_strings=True
                )
            except NoSuchAttributeException:
                assert resume_url == None

        date_created_regex = re.compile(test_vars.date_created_regex)
        assert date_created_regex.match(row_elements.date_created.text)

    def resume_check_if_footer_element_loaded(self, driver):
        try:
            footer_element = driver.find_element(By.ID, test_vars.resume_dom_elements.footer_section)
        except NoSuchElementException:
            assert test_outcome_element_not_found == None

        footer_children = footer_element.find_elements(By.CSS_SELECTOR, "*")
        return True if footer_children else False

    def resume_check_url(self, driver, resume_id, reference_html, expect_match=True):
        resume_url = self.manager_get_resume_posting_url(driver, resume_id)
        wait = self.get_driver_waiter(driver)

        try:
            driver.implicitly_wait(10)
            try:
                driver.get(resume_url)
            except ReadTimeoutError:
                sleep(3)
                driver.get(resume_url)
            driver.implicitly_wait(0)
        except Exception as e:
            self.print(", ".join(e.args))
            assert test_outcome_resume_url_error is None

        try:
            wait.until(lambda _: driver.execute_script("return document.readyState") == "complete")
        except TimeoutException:
            assert test_outcome_url_timeout is None

        try:
            wait.until(lambda _: self.resume_check_if_footer_element_loaded(driver))
        except TimeoutException:
            assert test_outcome_url_timeout is None

        for error_page in test_vars.urls.error_pages:
            assert not driver.current_url.startswith(error_page)

        source_soup = BeautifulSoup(test_vars.whitespace_regex.sub("", driver.page_source), "html.parser")
        reference_soup = BeautifulSoup(test_vars.whitespace_regex.sub("", reference_html), "html.parser")

        for view_counter in source_soup.find_all(attrs={"class": test_vars.resume_dom_view_counter_class}):
            view_counter.string = ""

        for view_counter in reference_soup.find_all(attrs={"class": test_vars.resume_dom_view_counter_class}):
            view_counter.string = ""

        soups_equal = source_soup.prettify() == reference_soup.prettify()

        if not soups_equal == expect_match:
            from difflib import Differ
            from pprint import pprint

            d = Differ()
            result = list(
                d.compare(
                    source_soup.prettify().splitlines(keepends=True),
                    reference_soup.prettify().splitlines(keepends=True),
                )
            )
            result.append(f"\nMatch expected: {expect_match}\nStack trace: {format_exc()}")
            warnings.warn(UserWarning("".join(result)))

        assert soups_equal == expect_match

        driver.back()
        sleep(1)
        if driver.current_url == test_vars.urls.manager_urls.manager:
            try:
                wait.until(lambda _: self.manager_check_if_page_loaded(driver))
            except TimeoutException:
                assert test_outcome_url_timeout is None

    def resume_check_view_count_increase(self, driver, resume_id):
        resume_url = self.manager_get_resume_posting_url(driver, resume_id, no_increment=False)

        count_1 = self.resume_get_view_count(driver, resume_url)
        count_2 = self.resume_get_view_count(driver, resume_url)

        assert count_2 > count_1

    def resume_get_view_count(self, driver, resume_url):
        wait = self.get_driver_waiter(driver)

        def _get_resume_view_count():
            count = ""
            for element in [value for key, value in test_vars.resume_dom_elements.items() if key.startswith("view")]:
                try:
                    digit_element = driver.find_element(By.ID, element)
                except NoSuchElementException:
                    assert test_outcome_element_not_found == None
                count = count + digit_element.text
            return count

        wait = self.get_driver_waiter(driver)
        try:
            driver.implicitly_wait(10)
            try:
                driver.get(resume_url)
            except ReadTimeoutError:
                sleep(3)
                driver.get(resume_url)
            driver.implicitly_wait(0)
        except Exception as e:
            self.print(", ".join(e.args))
            assert test_outcome_resume_url_error is None

        try:
            wait.until(lambda _: driver.execute_script("return document.readyState") == "complete")
        except TimeoutException:
            assert test_outcome_url_timeout is None

        if driver.current_url in test_vars.urls.error_pages:
            assert test_outcome_resume_url_error is driver.current_url

        try:
            wait.until(lambda _: _get_resume_view_count().isdigit())
        except TimeoutException:
            assert test_outcome_url_timeout is None

        resume_view_count = int(_get_resume_view_count())

        driver.back()
        sleep(1)
        if driver.current_url == test_vars.urls.manager_urls.manager:
            try:
                wait.until(lambda _: self.manager_check_if_page_loaded(driver))
            except TimeoutException:
                assert test_outcome_url_timeout is None

        return resume_view_count

    def homepage_check_source(self, driver, reference_homepage_html, initial_resume_id):
        wait = self.get_driver_waiter(driver)

        driver.implicitly_wait(10)
        try:
            driver.get(test_vars.urls.base_url)
        except ReadTimeoutError:
            sleep(3)
            driver.get(test_vars.urls.base_url)
        driver.implicitly_wait(0)
        try:
            wait.until(lambda _: driver.execute_script("return document.readyState") == "complete")
        except TimeoutException:
            assert test_outcome_url_timeout is None

        try:
            wait.until(lambda _: self.resume_check_if_footer_element_loaded(driver))
        except TimeoutException:
            assert test_outcome_url_timeout is None

        source_soup = BeautifulSoup(test_vars.whitespace_regex.sub("", driver.page_source), "html.parser")
        reference_soup = BeautifulSoup(test_vars.whitespace_regex.sub("", reference_homepage_html), "html.parser")

        source_general_resume_a = source_soup.find(attrs={"id": test_vars.homepage_general_resume_a_id})
        reference_general_resume_a = reference_soup.find(attrs={"id": test_vars.homepage_general_resume_a_id})

        assert not source_general_resume_a is None

        source_general_resume_a_href = source_general_resume_a.get("href")
        assert not source_general_resume_a_href is None
        assert source_general_resume_a_href == f"{test_vars.urls.resume_urls.base_resume_url}/{initial_resume_id}.html"

        source_general_resume_a["href"] = ""
        reference_general_resume_a["href"] = ""

        soups_match = source_soup.prettify() == reference_soup.prettify()

        assert soups_match == True

        driver.back()
        sleep(1)
        if driver.current_url == test_vars.urls.manager_urls.manager:
            try:
                wait.until(lambda _: self.manager_check_if_page_loaded(driver))
            except TimeoutException:
                assert test_outcome_url_timeout is None

    # test_add_test_resume
    @pytest.mark.parametrize(
        "driver",
        [
            pytest.param(
                browser,
                marks=(pytest.mark.skipif(platform.system() not in value["os"], reason=f"OS is not {value['os']}"),),
            )
            for browser, value in test_vars.test_drivers.items()
        ],
        indirect=["driver"],
    )
    @pytest.mark.parametrize(
        "resume_id,company,job_title,job_posting,resume_test_docx_path,expected",
        [
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_success,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.BAD,
                test_outcome_resume_parse_error,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.INVALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_id_input_error,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.INVALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_job_posting_input_error,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.EMPTY, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_action_unavailable,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.EMPTY,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_action_unavailable,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.EMPTY,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_action_unavailable,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.EMPTY,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_action_unavailable,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.EMPTY,
                test_outcome_action_unavailable,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.EMPTY, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.EMPTY,
                test_vars.test_inputs.job_title.EMPTY,
                test_vars.test_inputs.job_posting.EMPTY,
                test_vars.test_inputs.test_resume_path.EMPTY,
                test_outcome_action_unavailable,
            ),
        ],
        indirect=["resume_id", "company", "job_title", "job_posting", "resume_test_docx_path"],
    )
    def test_add_test_resume(
        self, driver, resume_id, company, job_title, job_posting, resume_test_docx_path, resume_test_html, expected
    ):
        self.manager_action_login(driver)

        self.manager_action_add_resume(
            driver,
            resume_id,
            company,
            job_title,
            job_posting,
            resume_test_docx_path,
            resume_test_html,
            expected=expected,
        )
        if expected == test_outcome_success:
            self.manager_check_resume_view_increase(driver, resume_id)

    # test_add_test_resume_already_exists
    @pytest.mark.parametrize(
        "driver",
        [
            pytest.param(
                browser,
                marks=(pytest.mark.skipif(platform.system() not in value["os"], reason=f"OS is not {value['os']}"),),
            )
            for browser, value in test_vars.test_drivers.items()
        ],
        indirect=["driver"],
    )
    @pytest.mark.parametrize(
        "resume_id,company,job_title,job_posting,resume_test_docx_path,expected",
        [
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_id_already_exists,
            ),
        ],
        indirect=["resume_id", "company", "job_title", "job_posting", "resume_test_docx_path"],
    )
    def test_add_test_resume_already_exists(
        self, driver, resume_id, company, job_title, job_posting, resume_test_docx_path, resume_test_html, expected
    ):
        self.manager_action_login(driver)
        self.manager_action_add_resume(
            driver,
            resume_id,
            company,
            job_title,
            job_posting,
            resume_test_docx_path,
            resume_test_html,
            expected=test_outcome_success,
        )
        self.manager_action_add_resume(
            driver,
            resume_id,
            company,
            job_title,
            job_posting,
            resume_test_docx_path,
            resume_test_html,
            expected=expected,
        )

    # test_add_initial_resume
    @pytest.mark.parametrize(
        "driver",
        [
            pytest.param(
                browser,
                marks=(pytest.mark.skipif(platform.system() not in value["os"], reason=f"OS is not {value['os']}"),),
            )
            for browser, value in test_vars.test_drivers.items()
            if browser == test_vars.test_inputs.drivers.default
        ],
        indirect=["driver"],
    )
    @pytest.mark.parametrize(
        "resume_id,company,job_title,job_posting,expected",
        [
            pytest.param(
                (test_vars.test_inputs.resume_id.PROD, test_vars.test_inputs.keep_resume.KEEP_IF_PROD),
                test_vars.test_inputs.company.PROD,
                test_vars.test_inputs.job_title.PROD,
                test_vars.test_inputs.job_posting.PROD,
                test_outcome_success,
            )
        ],
        indirect=["resume_id", "company", "job_title", "job_posting"],
    )
    def test_add_initial_resume(
        self,
        driver,
        resume_id,
        company,
        job_title,
        job_posting,
        resume_initial_docx_path,
        resume_initial_html,
        homepage_source_html,
        expected,
    ):
        self.manager_action_login(driver)
        resume_exists = True
        # Skip if resume already exists
        try:
            self.manager_action_select_row(driver, resume_id)
        except AssertionError:
            self.manager_action_view_deleted_resumes(driver)

            try:
                self.manager_action_select_row(driver, resume_id)
            except AssertionError:
                resume_exists = False

            self.manager_action_view_active_resumes(driver)

        if resume_exists:
            warnings.warn(UserWarning("Initial resume already exists. Will not test."))
            return

        self.manager_action_add_resume(
            driver,
            resume_id,
            company,
            job_title,
            job_posting,
            resume_initial_docx_path,
            resume_initial_html,
            expected=expected,
        )

        self.homepage_check_source(
            driver=driver, reference_homepage_html=homepage_source_html, initial_resume_id=resume_id
        )

    # test_modify_resume
    @pytest.mark.parametrize(
        "driver",
        [
            pytest.param(
                browser,
                marks=(pytest.mark.skipif(platform.system() not in value["os"], reason=f"OS is not {value['os']}"),),
            )
            for browser, value in test_vars.test_drivers.items()
        ],
        indirect=["driver"],
    )
    @pytest.mark.parametrize(
        "resume_id,company,job_title,job_posting,resume_test_docx_path,expected",
        [
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_success,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.EMPTY,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_action_unavailable,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.EMPTY,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_action_unavailable,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.EMPTY,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_action_unavailable,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.EMPTY,
                test_vars.test_inputs.job_title.EMPTY,
                test_vars.test_inputs.job_posting.EMPTY,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_action_unavailable,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.INVALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_job_posting_input_error,
            ),
        ],
        indirect=["resume_id", "company", "job_title", "job_posting", "resume_test_docx_path"],
    )
    def test_modify_resume(
        self, driver, resume_id, company, job_title, job_posting, resume_test_docx_path, resume_test_html, expected
    ):
        self.manager_action_login(driver)
        self.manager_action_add_resume(
            driver,
            resume_id,
            self.generate_company(test_vars.test_inputs.company.VALID),
            self.generate_job_title(test_vars.test_inputs.job_title.VALID),
            self.generate_job_posting(test_vars.test_inputs.job_posting.VALID),
            resume_test_docx_path,
            resume_test_html,
            expected=test_outcome_success,
        )
        self.manager_action_modify_resume(
            driver, resume_id, company, job_title, job_posting, resume_test_html, expected=expected
        )

    # test_modify_resume_no_changes
    @pytest.mark.parametrize(
        "driver",
        [
            pytest.param(
                browser,
                marks=(pytest.mark.skipif(platform.system() not in value["os"], reason=f"OS is not {value['os']}"),),
            )
            for browser, value in test_vars.test_drivers.items()
        ],
        indirect=["driver"],
    )
    @pytest.mark.parametrize(
        "resume_id,company,job_title,job_posting,resume_test_docx_path,expected",
        [
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_modify_no_changes_error,
            ),
        ],
        indirect=["resume_id", "company", "job_title", "job_posting", "resume_test_docx_path"],
    )
    def test_modify_resume_no_changes(
        self, driver, resume_id, company, job_title, job_posting, resume_test_docx_path, resume_test_html, expected
    ):
        self.manager_action_login(driver)
        self.manager_action_add_resume(
            driver,
            resume_id,
            company,
            job_title,
            job_posting,
            resume_test_docx_path,
            resume_test_html,
            expected=test_outcome_success,
        )
        self.manager_action_modify_resume(
            driver, resume_id, company, job_title, job_posting, resume_test_html, expected=expected
        )

    # test_reupload_resume
    @pytest.mark.parametrize(
        "driver",
        [
            pytest.param(
                browser,
                marks=(pytest.mark.skipif(platform.system() not in value["os"], reason=f"OS is not {value['os']}"),),
            )
            for browser, value in test_vars.test_drivers.items()
        ],
        indirect=["driver"],
    )
    @pytest.mark.parametrize(
        "resume_id,company,job_title,job_posting,resume_test_docx_path,resume_test_modified_docx_path,expected",
        [
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_success,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_vars.test_inputs.test_resume_path.BAD,
                test_outcome_resume_parse_error,
            ),
        ],
        indirect=[
            "resume_id",
            "company",
            "job_title",
            "job_posting",
            "resume_test_docx_path",
            "resume_test_modified_docx_path",
        ],
    )
    def test_reupload_resume(
        self,
        driver,
        resume_id,
        company,
        job_title,
        job_posting,
        resume_test_docx_path,
        resume_test_html,
        resume_test_modified_docx_path,
        resume_test_modified_html,
        expected,
    ):
        self.manager_action_login(driver)
        self.manager_action_add_resume(
            driver,
            resume_id,
            self.generate_company(test_vars.test_inputs.company.VALID),
            self.generate_job_title(test_vars.test_inputs.job_title.VALID),
            self.generate_job_posting(test_vars.test_inputs.job_posting.VALID),
            resume_test_docx_path,
            resume_test_html,
            expected=test_outcome_success,
        )
        self.manager_action_reupload_resume(
            driver, resume_id, resume_test_modified_docx_path, resume_test_modified_html, expected=expected
        )

    # test_refresh_resume
    @pytest.mark.parametrize(
        "dual_drivers",
        [
            pytest.param(
                (browser, browser),
                marks=(pytest.mark.skipif(platform.system() not in value["os"], reason=f"OS is not {value['os']}"),),
            )
            for browser, value in test_vars.test_drivers.items()
        ],
        indirect=["dual_drivers"],
    )
    @pytest.mark.parametrize(
        "resume_id,company,job_title,job_posting,resume_test_docx_path,expected",
        [
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_success,
            ),
        ],
        indirect=["resume_id", "company", "job_title", "job_posting", "resume_test_docx_path"],
    )
    def test_refresh_resume(
        self,
        dual_drivers,
        resume_id,
        company,
        job_title,
        job_posting,
        resume_test_docx_path,
        resume_test_html,
        expected,
    ):
        primary_driver, alternate_driver = dual_drivers
        self.manager_action_login(primary_driver)
        self.manager_action_add_resume(
            primary_driver,
            resume_id,
            self.generate_company(test_vars.test_inputs.company.VALID),
            self.generate_job_title(test_vars.test_inputs.job_title.VALID),
            self.generate_job_posting(test_vars.test_inputs.job_posting.VALID),
            resume_test_docx_path,
            resume_test_html,
            expected=test_outcome_success,
        )

        self.manager_action_login(alternate_driver)
        self.manager_action_modify_resume(
            alternate_driver,
            resume_id,
            company,
            job_title,
            job_posting,
            resume_test_html,
            expected=test_outcome_success,
        )

        self.manager_action_refresh_resume(
            primary_driver,
            resume_id,
            company,
            job_title,
            job_posting,
            resume_test_html,
            expected=expected,
        )

    # test_refresh_all_resumes
    @pytest.mark.parametrize(
        "dual_drivers",
        [
            pytest.param(
                (browser, browser),
                marks=(pytest.mark.skipif(platform.system() not in value["os"], reason=f"OS is not {value['os']}"),),
            )
            for browser, value in test_vars.test_drivers.items()
        ],
        indirect=["dual_drivers"],
    )
    @pytest.mark.parametrize(
        "resume_id,company,job_title,job_posting,resume_test_docx_path,expected",
        [
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_success,
            ),
        ],
        indirect=["resume_id", "company", "job_title", "job_posting", "resume_test_docx_path"],
    )
    def test_refresh_all_resumes(
        self,
        dual_drivers,
        resume_id,
        company,
        job_title,
        job_posting,
        resume_test_docx_path,
        resume_test_html,
        expected,
    ):
        primary_driver, alternate_driver = dual_drivers
        self.manager_action_login(primary_driver)
        self.manager_action_add_resume(
            primary_driver,
            resume_id,
            self.generate_company(test_vars.test_inputs.company.VALID),
            self.generate_job_title(test_vars.test_inputs.job_title.VALID),
            self.generate_job_posting(test_vars.test_inputs.job_posting.VALID),
            resume_test_docx_path,
            resume_test_html,
            expected=test_outcome_success,
        )
        self.manager_action_login(alternate_driver)
        self.manager_action_modify_resume(
            alternate_driver,
            resume_id,
            company,
            job_title,
            job_posting,
            resume_test_html,
            expected=test_outcome_success,
        )
        self.manager_action_refresh_all_resumes(primary_driver)
        self.manager_validate_row_data(
            primary_driver, resume_id, company, job_title, job_posting, resume_test_html, expected=expected
        )

    # test_delete_resume
    @pytest.mark.parametrize(
        "driver",
        [
            pytest.param(
                browser,
                marks=(pytest.mark.skipif(platform.system() not in value["os"], reason=f"OS is not {value['os']}"),),
            )
            for browser, value in test_vars.test_drivers.items()
        ],
        indirect=["driver"],
    )
    @pytest.mark.parametrize(
        "resume_id,company,job_title,job_posting,resume_test_docx_path",
        [
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
            ),
        ],
        indirect=["resume_id", "company", "job_title", "job_posting", "resume_test_docx_path"],
    )
    def test_delete_resume(
        self,
        driver,
        resume_id,
        company,
        job_title,
        job_posting,
        resume_test_docx_path,
        resume_test_html,
    ):
        self.manager_action_login(driver)
        self.manager_action_add_resume(
            driver,
            resume_id,
            self.generate_company(test_vars.test_inputs.company.VALID),
            self.generate_job_title(test_vars.test_inputs.job_title.VALID),
            self.generate_job_posting(test_vars.test_inputs.job_posting.VALID),
            resume_test_docx_path,
            resume_test_html,
            expected=test_outcome_success,
        )
        self.manager_action_delete_resume(driver, resume_id)

    # test_restore_resume
    @pytest.mark.parametrize(
        "driver",
        [
            pytest.param(
                browser,
                marks=(pytest.mark.skipif(platform.system() not in value["os"], reason=f"OS is not {value['os']}"),),
            )
            for browser, value in test_vars.test_drivers.items()
        ],
        indirect=["driver"],
    )
    @pytest.mark.parametrize(
        "resume_id,company,job_title,job_posting,resume_test_docx_path",
        [
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
            ),
        ],
        indirect=["resume_id", "company", "job_title", "job_posting", "resume_test_docx_path"],
    )
    def test_restore_resume(
        self,
        driver,
        resume_id,
        company,
        job_title,
        job_posting,
        resume_test_docx_path,
        resume_test_html,
    ):
        self.manager_action_login(driver)
        self.manager_action_add_resume(
            driver,
            resume_id,
            self.generate_company(test_vars.test_inputs.company.VALID),
            self.generate_job_title(test_vars.test_inputs.job_title.VALID),
            self.generate_job_posting(test_vars.test_inputs.job_posting.VALID),
            resume_test_docx_path,
            resume_test_html,
            expected=test_outcome_success,
        )
        self.manager_action_delete_resume(driver, resume_id)
        self.manager_action_restore_resume(driver, resume_id)

    # test_permanently_delete_resume
    @pytest.mark.parametrize(
        "driver",
        [
            pytest.param(
                browser,
                marks=(pytest.mark.skipif(platform.system() not in value["os"], reason=f"OS is not {value['os']}"),),
            )
            for browser, value in test_vars.test_drivers.items()
        ],
        indirect=["driver"],
    )
    @pytest.mark.parametrize(
        "resume_id,company,job_title,job_posting,resume_test_docx_path,cancel",
        [
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_vars.test_inputs.cancel.PROCEED,
            ),
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_vars.test_inputs.cancel.CANCEL,
            ),
        ],
        indirect=["resume_id", "company", "job_title", "job_posting", "resume_test_docx_path"],
    )
    def test_permanently_delete_resume(
        self,
        driver,
        resume_id,
        company,
        job_title,
        job_posting,
        resume_test_docx_path,
        resume_test_html,
        cancel,
    ):
        self.manager_action_login(driver)
        self.manager_action_add_resume(
            driver,
            resume_id,
            self.generate_company(test_vars.test_inputs.company.VALID),
            self.generate_job_title(test_vars.test_inputs.job_title.VALID),
            self.generate_job_posting(test_vars.test_inputs.job_posting.VALID),
            resume_test_docx_path,
            resume_test_html,
            expected=test_outcome_success,
        )
        self.manager_action_permanently_delete_resume(driver, resume_id, cancel)

    # test_increment_resume_view_count
    @pytest.mark.parametrize(
        "driver",
        [
            pytest.param(
                browser,
                marks=(pytest.mark.skipif(platform.system() not in value["os"], reason=f"OS is not {value['os']}"),),
            )
            for browser, value in test_vars.test_drivers.items()
        ],
        indirect=["driver"],
    )
    @pytest.mark.parametrize(
        "resume_id,company,job_title,job_posting,resume_test_docx_path,expected",
        [
            pytest.param(
                (test_vars.test_inputs.resume_id.VALID, test_vars.test_inputs.keep_resume.DESTROY),
                test_vars.test_inputs.company.VALID,
                test_vars.test_inputs.job_title.VALID,
                test_vars.test_inputs.job_posting.VALID,
                test_vars.test_inputs.test_resume_path.GOOD,
                test_outcome_success,
            ),
        ],
        indirect=["resume_id", "company", "job_title", "job_posting", "resume_test_docx_path"],
    )
    def test_increment_resume_view_count(
        self,
        driver,
        resume_id,
        company,
        job_title,
        job_posting,
        resume_test_docx_path,
        resume_test_html,
        expected,
    ):
        self.manager_action_login(driver)
        self.manager_action_add_resume(
            driver,
            resume_id,
            self.generate_company(test_vars.test_inputs.company.VALID),
            self.generate_job_title(test_vars.test_inputs.job_title.VALID),
            self.generate_job_posting(test_vars.test_inputs.job_posting.VALID),
            resume_test_docx_path,
            resume_test_html,
            expected=test_outcome_success,
        )
        self.resume_check_view_count_increase(driver, resume_id)
        self.manager_check_resume_view_increase(driver, resume_id, no_increment=False)
