#!/usr/bin/env python3


def run_cdk():
    import importlib

    from vars import check_env_vars, env

    check_env_vars()

    from src.backend.configuration import common
    from src.backend.component import ResumeWebAppBackend

    from aws_cdk import App, Environment

    common.test_all_json_config()

    app = App()
    ResumeWebAppBackend(app, "ResumeWebAppBackend", env=Environment(account=env.APP_DEPLOY_ACCOUNT))
    app.synth()


if __name__ == "__main__":
    run_cdk()
