#!/usr/bin/env python3
import errno
import os
import platform
import requests

from argparse import ArgumentParser
from subprocess import PIPE, STDOUT, Popen, TimeoutExpired
from time import sleep
from typing import List


def install_web_browser(
    *, chrome: bool = False, edge: bool = False, firefox: bool = False, safari: bool = False, debug: bool = False
):
    apt_update_run = False
    if not (chrome or edge or firefox):
        raise RuntimeError("At least one broswer must be specified")

    try:
        os.rename("/etc/fakefile123", "/etc/fake")
    except IOError as e:
        if e.args[0] == errno.EPERM:
            raise RuntimeError("Script must be run with root privileges.")

    if chrome:
        google_version = run_process(["google-chrome --version"], shell=True, print_output=debug)

        if google_version["return_code"] not in [0, 127]:
            raise RuntimeError(
                "Checking Google Chrome's version returned an unexpected error.\n"
                + f"Error code: {google_version['return_code']}\nError: {google_version['stdout_text']}"
            )
        elif google_version["return_code"] == 127:
            if not platform.system() == "Linux":
                raise RuntimeError("To install Chrome, the operating system must be Linux")
            apt_key_location = "https://dl-ssl.google.com/linux/linux_signing_key.pub"
            apt_list_file = "/etc/apt/sources.list.d/google.list"
            apt_list_entry = "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main\n"
            google_gpg_key_path = "/etc/apt/trusted.gpg.d/google.gpg"

            apt_key = run_process(["apt-key list"], shell=True, print_output=debug)

            if apt_key["return_code"] != 0:
                raise RuntimeError(f"Error getting apt-key list. Error: {apt_key['stderr_text']}")

            if "Google, Inc." not in apt_key["stdout_text"]:
                print("Getting Google apt GPG key")
                response = requests.get(apt_key_location)

                if response.status_code > 200:
                    raise RuntimeError(
                        f"Unable to get Chrome public key. StatusCode: {response.status_code}, Reason: {response.reason}, Text: {response.text}"
                    )

                pub_key = response.text

                if "-----BEGIN PGP PUBLIC KEY BLOCK-----" in pub_key:
                    if debug:
                        print("Echoing pub_key")
                    echo = run_process(["echo", pub_key], wait=True)
                    if debug:
                        print("Running gpg --dearmor")
                    gpg = run_process(["gpg", "--dearmor"], stdin=echo["stdout"], wait=True)
                    if debug:
                        print(f"dd-ing dearmored key to {google_gpg_key_path}")
                    dd = run_process(
                        [f"dd of={google_gpg_key_path}"], stdin=gpg["stdout"], shell=True, print_output=debug
                    )

                    if dd["return_code"] != 0:
                        raise RuntimeError(f"Error dd-ing GPG file. Error: {dd['stdout_text']}")

            try:
                if debug:
                    print(f"Checking contents of {apt_list_file}")
                with open(apt_list_file, "r") as f:
                    contents = f.read()
            except IOError as e:
                if e.args[0] == errno.ENOENT:
                    print("File did not exit")
                    contents = ""

            if apt_list_entry not in contents:
                if debug:
                    print(f"File did not contain {apt_list_entry} - adding it")
                with open(apt_list_file, "a") as f:
                    f.write(apt_list_entry)

                print("Installing Google Chrome")
                apt_update = run_process(["apt", "update"], print_output=debug)

                apt_update_run = True

                if apt_update["return_code"] != 0:
                    raise RuntimeError(
                        f"Error updating apt. Error code: {apt_update['return_code']} -  Error: {apt_update['stdout_text']}"
                    )

                apt_install = run_process(["apt-get install google-chrome-stable -y"], shell=True, print_output=debug)

                if apt_install["return_code"] != 0:
                    raise RuntimeError(
                        f"Error installing Google Chrome. Error code: {apt_install['return_code']} "
                        + f"- Error: {apt_install['stdout_text']}"
                    )
                print("Google Chrome has been installed")
        else:
            print("Google Chrome was already installed")

    if edge:
        edge_version = run_process(["microsoft-edge --version"], shell=True, print_output=debug)

        if edge_version["return_code"] not in [0, 127]:
            raise RuntimeError(
                "Checking Microsoft Edge's version returned an unexpected error.\n"
                + f"Error code: {edge_version['return_code']}\nError: {edge_version['stdout_text']}"
            )
        elif edge_version["return_code"] == 127:
            if not platform.system() == "Linux":
                raise RuntimeError("To install Microsoft Edge, the operating system must be Linux")
            if not apt_update_run:
                apt_update = run_process(["apt", "update"])

                apt_update_run = True

                if apt_update["return_code"] != 0:
                    raise RuntimeError(
                        f"Error updating apt. Error code: {apt_update['return_code']} -  Error: {apt_update['stdout_text']}"
                    )

            apt_install = run_process(["apt-get install microsoft-edge-stable -y"], shell=True, print_output=debug)

            if apt_install["return_code"] != 0:
                raise RuntimeError(
                    f"Error installing Microsoft Edge. Error code: {apt_install['return_code']} "
                    + f"- Error: {apt_install['stdout_text']}"
                )

            print("Microsoft Edge has been installed")
        else:
            print("Microsoft Edge was already installed")
    if firefox:
        firefox_version = run_process(["firefox --version"], shell=True, print_output=debug)

        if firefox_version["return_code"] not in [0, 127]:
            raise RuntimeError(
                "Checking Firefox's version returned an unexpected error.\n"
                + f"Error code: {firefox_version['return_code']}\nError: {firefox_version['stdout_text']}"
            )
        elif firefox_version["return_code"] == 127:
            if not platform.system() == "Linux":
                raise RuntimeError("To install Firefox, the operating system must be Linux")
            if not apt_update_run:
                apt_update = run_process(["apt", "update"])

                apt_update_run = True

                if apt_update["return_code"] != 0:
                    raise RuntimeError(
                        f"Error updating apt. Error code: {apt_update['return_code']} -  Error: {apt_update['stdout_text']}"
                    )

            apt_install = run_process(["apt-get install firefox -y"], shell=True, print_output=debug)

            if apt_install["return_code"] != 0:
                raise RuntimeError(
                    f"Error installing Firefox. Error code: {apt_install['return_code']} "
                    + f"- Error: {apt_install['stdout_text']}"
                )

            print("Firefox has been installed")
        else:
            print("Firefox was already installed")
    if safari:
        if debug:
            print(
                "Safari cannot be installed as it is embedded into Apple's MacOS. However, the web driver may need to be enabled"
            )

        enable_webdriver = run_process(["safaridriver", "--enable"])
        if enable_webdriver["return_code"] != 0:
            raise RuntimeError(
                "Enabling Safari's web driver for Selenium to use returned an unexpected error.\n"
                + f"Error code: {enable_webdriver['return_code']}\nError: {enable_webdriver['stdout_text']}"
            )

        print("Safari's webdriver appears to have been enabled successfully")


def check_if_browser_installed(
    chrome: bool = False, edge: bool = False, firefox: bool = False, safari: bool = False
) -> dict:
    if not (chrome or edge or firefox or safari):
        raise RuntimeError("At least one browser must be specified")

    ret_dict = {}
    if chrome:
        version = run_process(["google-chrome --version"], shell=True)
        if version["return_code"] not in [0, 127]:
            raise RuntimeError(
                "Checking Google Chrome's version returned an unexpected error.\n"
                + f"Error code: {version['return_code']}\nError: {version['stdout_text']}"
            )

        ret_dict["chrome"] = version["return_code"] == 0
    if edge:
        version = run_process(["microsoft-edge --version"], shell=True)
        if version["return_code"] not in [0, 127]:
            raise RuntimeError(
                "Checking Edge's version returned an unexpected error.\n"
                + f"Error code: {version['return_code']}\nError: {version['stdout_text']}"
            )

        ret_dict["edge"] = version["return_code"] == 0
    if firefox:
        version = run_process(["firefox --version"], shell=True)
        if version["return_code"] not in [0, 127]:
            raise RuntimeError(
                "Checking Firefox's version returned an unexpected error.\n"
                + f"Error code: {version['return_code']}\nError: {version['stdout_text']}"
            )

        ret_dict["firefox"] = version["return_code"] == 0
    if safari:
        version = run_process(
            [
                '/usr/libexec/PlistBuddy -c "print :CFBundleShortVersionString" /Applications/Safari.app/Contents/Info.plist'
            ],
            shell=True,
        )
        if version["return_code"] not in [0, 127]:
            raise RuntimeError(
                "Checking Safari's version returned an unexpected error.\n"
                + f"Error code: {version['return_code']}\nError: {version['stdout_text']}"
            )

        ret_dict["safari"] = version["return_code"] == 0

    return ret_dict


def run_process(
    arguments: List[str],
    stdin=None,
    stdout=PIPE,
    stderr=STDOUT,
    wait: bool = False,
    shell: bool = False,
    read_stdout: bool = True,
    read_stderr: bool = True,
    print_output: bool = False,
) -> dict:
    proc = Popen(
        arguments,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        universal_newlines=True,
        shell=shell,
        executable=("/bin/bash" if shell else None),
    )

    ret_dict = {
        "return_code": None,
        "stdout_text": "",
        "stderr_text": "",
        "stdout": None,
        "stderr": None,
    }

    if wait:
        proc.wait()
    else:
        return_code = None
        while True:
            return_code = proc.poll()
            if stdout == PIPE and read_stdout and proc.stdout.readable():
                for line in iter(proc.stdout.readline, ""):
                    ret_dict["stdout_text"] += line
                    if print_output:
                        print(line, end="")
            if stderr == PIPE and read_stderr and proc.stderr.readable():
                for line in iter(proc.stderr.readline, ""):
                    ret_dict["stderr_text"] += line
                    if print_output:
                        print(line, end="")
            if return_code != None:
                break
            sleep(0.5)

        ret_dict["return_code"] = return_code

    ret_dict["stdout"] = proc.stdout
    ret_dict["stderr"] = proc.stderr

    return ret_dict


if __name__ == "__main__":
    parser = ArgumentParser(prog="Browser Installer")
    parser.add_argument("--chrome", action="store_true")
    parser.add_argument("--edge", action="store_true")
    parser.add_argument("--firefox", action="store_true")
    parser.add_argument("--safari", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser_args = parser.parse_args()
    args = vars(parser_args)
    install_web_browser(
        chrome=args["chrome"], edge=args["edge"], firefox=args["firefox"], safari=args["safari"], debug=args["debug"]
    )
