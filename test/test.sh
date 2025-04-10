#!/bin/env bash
if [ -z ${1+x} ]; then
    workers=4;
else
    workers=$1;
fi

pytest -n 6 --reruns $workers -vv -rsfE test/test_resume_app.py::TestResumeApp::test_add_test_resume
pytest -n 6 --reruns $workers -vv -rsfE test/test_resume_app.py::TestResumeApp::test_add_test_resume_already_exists
pytest -n 6 --reruns $workers -vv -rsfE test/test_resume_app.py::TestResumeApp::test_add_initial_resume
pytest -n 6 --reruns $workers -vv -rsfE test/test_resume_app.py::TestResumeApp::test_modify_resume
pytest -n 6 --reruns $workers -vv -rsfE test/test_resume_app.py::TestResumeApp::test_modify_resume_no_changes
pytest -n 6 --reruns $workers -vv -rsfE test/test_resume_app.py::TestResumeApp::test_refresh_resume
pytest -n 6 --reruns $workers -vv -rsfE test/test_resume_app.py::TestResumeApp::test_refresh_all_resumes
pytest -n 6 --reruns $workers -vv -rsfE test/test_resume_app.py::TestResumeApp::test_reupload_resume
pytest -n 6 --reruns $workers -vv -rsfE test/test_resume_app.py::TestResumeApp::test_delete_resume
pytest -n 6 --reruns $workers -vv -rsfE test/test_resume_app.py::TestResumeApp::test_restore_resume
pytest -n 6 --reruns $workers -vv -rsfE test/test_resume_app.py::TestResumeApp::test_permanently_delete_resume
pytest -n 6 --reruns $workers -vv -rsfE test/test_resume_app.py::TestResumeApp::test_increment_resume_view_count
