from os import environ
from shutil import which
from subprocess import PIPE, STDOUT, Popen, TimeoutExpired
from time import sleep

from vars import check_env_vars, env


def destroy_cdk():
    args = [
        "cdk destroy -f --all",
    ]

    print(f"- Executing \"{' '.join(args)}\"")

    proc = Popen(args, stdout=PIPE, stderr=STDOUT, env=environ, universal_newlines=True, shell=True)
    return_code = None

    while True:
        return_code = proc.poll()
        if proc.stdout.readable():
            for line in iter(proc.stdout.readline, ""):
                print(line, end="")
        if return_code != None:
            print(f"return_code: {return_code}")
            break

        sleep(0.5)

    if return_code != 0:
        print(f"Process failed with return code {return_code}")


if __name__ == "__main__":
    destroy_cdk()
