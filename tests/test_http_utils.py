# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for utils/http_utils.py — RetCode and response construction."""

import os
import sys
import json

import pytest
from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))

from utils.http_utils import RetCode, construct_response, get_json_result


@pytest.fixture
def app():
    flask_app = Flask(__name__)
    flask_app.config['TESTING'] = True
    return flask_app


class TestRetCode:
    def test_success_value(self):
        assert RetCode.SUCCESS == 0

    def test_error_codes(self):
        assert RetCode.EXCEPTION_ERROR == 100
        assert RetCode.ARGUMENT_ERROR == 101
        assert RetCode.DATA_ERROR == 102
        assert RetCode.OPERATING_ERROR == 103

    def test_http_like_codes(self):
        assert RetCode.UNAUTHORIZED == 401
        assert RetCode.NOT_EXISTING == 404
        assert RetCode.CONFLICT == 409
        assert RetCode.SERVER_ERROR == 500

    def test_valid_method(self):
        assert RetCode.valid(0) is True
        assert RetCode.valid(100) is True
        assert RetCode.valid(9999) is False

    def test_values_method(self):
        values = RetCode.values()
        assert 0 in values
        assert 100 in values
        assert 401 in values

    def test_names_method(self):
        names = RetCode.names()
        assert "SUCCESS" in names
        assert "EXCEPTION_ERROR" in names


class TestConstructResponse:
    def test_success_response(self, app):
        with app.app_context():
            resp = construct_response(data={"key": "value"}, retmsg="OK")
            data = json.loads(resp.get_data(as_text=True))
            assert data['retcode'] == 0
            assert data['retmsg'] == "OK"
            assert data['data'] == {"key": "value"}

    def test_error_response(self, app):
        with app.app_context():
            resp = construct_response(
                retcode=RetCode.ARGUMENT_ERROR,
                retmsg="Bad argument",
                data={}
            )
            data = json.loads(resp.get_data(as_text=True))
            assert data['retcode'] == 101
            assert data['retmsg'] == "Bad argument"

    def test_cors_headers(self, app):
        with app.app_context():
            resp = construct_response(data={})
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"
            assert resp.headers.get("Access-Control-Allow-Method") == "*"
            assert resp.headers.get("Access-Control-Allow-Headers") == "*"

    def test_auth_header_when_provided(self, app):
        with app.app_context():
            resp = construct_response(data={}, auth="Bearer token123")
            assert resp.headers.get("Authorization") == "Bearer token123"
            assert resp.headers.get("Access-Control-Expose-Headers") == "Authorization"

    def test_none_data_excluded(self, app):
        with app.app_context():
            resp = construct_response(retmsg="no data")
            data = json.loads(resp.get_data(as_text=True))
            assert 'data' not in data

    def test_retcode_zero_always_present(self, app):
        with app.app_context():
            resp = construct_response()
            data = json.loads(resp.get_data(as_text=True))
            assert 'retcode' in data
            assert data['retcode'] == 0


class TestGetJsonResult:
    def test_basic_json_result(self, app):
        with app.app_context():
            resp = get_json_result(data={"items": [1, 2, 3]})
            data = json.loads(resp.get_data(as_text=True))
            assert data['retcode'] == 0
            assert data['retmsg'] == 'success'
            assert data['data'] == {"items": [1, 2, 3]}

    def test_error_json_result(self, app):
        with app.app_context():
            resp = get_json_result(
                retcode=RetCode.SERVER_ERROR,
                retmsg="internal error"
            )
            data = json.loads(resp.get_data(as_text=True))
            assert data['retcode'] == 500
            assert data['retmsg'] == "internal error"

    def test_none_values_excluded_except_retcode(self, app):
        with app.app_context():
            resp = get_json_result()
            data = json.loads(resp.get_data(as_text=True))
            assert 'retcode' in data
            assert 'data' not in data
